"""
Generador de diseños de poleras.

Flujo:
1. Analiza la(s) imagen(es) de referencia (estructura/composición) -> Gemini Flash (barato)
2. Si corresponde, quita el fondo de la(s) foto(s) del personaje -> rembg (local, gratis)
3. Arma el prompt final combinando: estructura + colores + texto + poses variadas
4. Genera la imagen final -> el modelo de imagen que el usuario haya elegido (Lite/Media/Pro)
5. Si no se pidió un color de fondo sólido específico, se le quita el fondo con rembg

NOTA: el SDK de Google (google-genai) cambia de vez en cuando sus nombres de métodos
y de modelos. Si algo falla al correr esto, lo primero es revisar la documentación
actual en https://ai.google.dev/gemini-api/docs para confirmar que los nombres
siguen siendo los mismos.
"""

import os
import io
import json
import re
import colorsys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image
from google import genai
from google.genai import types
from colorthief import ColorThief

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "No encontré GEMINI_API_KEY. Copia .env.example como .env y pega tu key ahí."
    )

client = genai.Client(api_key=API_KEY)

# Modelo usado para analizar imágenes (barato, no es el que genera el diseño final)
MODELO_VISION = "gemini-3.6-flash"

# Modelos de generación de imagen disponibles para elegir en la interfaz, de más
# barato a más caro. Verifica en ai.google.dev/gemini-api/docs/models si estos
# nombres siguen vigentes -- Google los actualiza de vez en cuando.
MODELOS_IMAGEN = {
    "Lite (más barato, ideal para probar)": "gemini-3.1-flash-lite-image",
    "Media (balance calidad/precio)": "gemini-3.1-flash-image",
    "Pro (mejor calidad, más caro)": "gemini-3-pro-image",
}
MODELO_IMAGEN_POR_DEFECTO = "Lite (más barato, ideal para probar)"

# Tamaño final del archivo de diseño, pensado para mandar a estampar.
# 1620x2160 = proporción 3:4 exacta, que es una de las proporciones nativas
# que soporta la API. IMPORTANTE sobre el costo: entre las opciones de resolución
# de Gemini, 1K y 2K cuestan exactamente lo mismo -- solo 4K sube el precio.
# Por eso pedimos 2K (mejor calidad sin pagar más) y al final reescalamos con
# Pillow al tamaño exacto que necesitas, sin perder nitidez porque la proporción
# ya calza (no hay que recortar, solo escalar).
ANCHO_FINAL_PX = 1620
ALTO_FINAL_PX = 2160
RELACION_ASPECTO = "3:4"

# Color chroma exacto usado como fondo cuando no se pide uno sólido específico.
# Se elige lejos de pieles/telas/paletas de diseño típicas para que el chroma
# key (quitar_fondo_por_chroma) lo pueda separar de forma limpia y consistente.
COLOR_CHROMA_DEFECTO = "#00FF7F"

# Alpha matting (el recorte fino que usamos para bordes más limpios) puede
# consumir muchísima memoria en fotos de celular modernas (12-48 megapíxeles).
# Como el resultado final igual se reescala a ANCHO_FINAL_PX x ALTO_FINAL_PX,
# no perdemos nada útil bajando la imagen ANTES de ese paso.
LADO_MAXIMO_PARA_RECORTE = 1600


def _redimensionar_si_es_muy_grande(imagen: Image.Image, lado_maximo: int = LADO_MAXIMO_PARA_RECORTE) -> Image.Image:
    """Reduce la imagen si su lado más largo supera lado_maximo, manteniendo proporción."""
    ancho, alto = imagen.size
    lado_mayor = max(ancho, alto)
    if lado_mayor <= lado_maximo:
        return imagen
    factor = lado_maximo / lado_mayor
    nuevo_tamano = (round(ancho * factor), round(alto * factor))
    return imagen.resize(nuevo_tamano, Image.LANCZOS)


def extraer_perfil_color(imagenes_referencia: list[Image.Image], cantidad_colores: int = 5) -> dict:
    """
    Extrae, con matemática real (no con la interpretación de un modelo de IA),
    los colores dominantes y el nivel de saturación de la(s) referencia(s).

    Usa ColorThief para sacar la paleta dominante de cada imagen, y colorsys
    para calcular la saturación promedio (en HSV) de esos colores. Con eso
    clasificamos el "tono" en sobrio / medio / vibrante de forma objetiva,
    en vez de depender de que Gemini lo describa bien con palabras.

    Devuelve un diccionario con:
    - "colores_hex": lista de colores dominantes en formato hexadecimal
    - "saturacion_promedio_pct": número de 0 a 100
    - "categoria_tono": "sobria/apagada" | "saturación media" | "vibrante/muy saturada"
    """
    todos_los_colores = []
    for imagen in imagenes_referencia:
        buffer_entrada = io.BytesIO()
        imagen.convert("RGB").save(buffer_entrada, format="PNG")
        buffer_entrada.seek(0)
        color_thief = ColorThief(buffer_entrada)
        paleta = color_thief.get_palette(color_count=cantidad_colores, quality=1)
        todos_los_colores.extend(paleta)

    colores_hex = ["#{:02X}{:02X}{:02X}".format(r, g, b) for r, g, b in todos_los_colores]

    saturaciones = []
    for r, g, b in todos_los_colores:
        _, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        saturaciones.append(s)
    saturacion_promedio_pct = round(sum(saturaciones) / len(saturaciones) * 100, 1)

    if saturacion_promedio_pct < 35:
        categoria_tono = "sobria/apagada"
    elif saturacion_promedio_pct < 65:
        categoria_tono = "saturación media"
    else:
        categoria_tono = "vibrante/muy saturada"

    return {
        "colores_hex": colores_hex,
        "saturacion_promedio_pct": saturacion_promedio_pct,
        "categoria_tono": categoria_tono,
    }


def _parsear_json_seguro(texto: str, fallback: dict | None = None) -> dict:
    """Extrae y parsea JSON de la respuesta de Gemini de forma tolerante a fallos."""
    texto_limpio = texto.strip()
    if "```" in texto_limpio:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", texto_limpio)
        if match:
            texto_limpio = match.group(1).strip()
    try:
        data = json.loads(texto_limpio)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    return fallback if fallback is not None else {}


def _sanitizar_texto_de_referencia(texto: str, sujeto_nombre: str) -> str:
    """Elimina palabras o memes que se hayan podido colar de referencias conocidas si el sujeto no trata de eso."""
    if not isinstance(texto, str):
        return ""
    palabras_peligrosas = [
        "fósil", "fosil", "fósiles", "fosiles", "fossil", "fossils",
        "trilobite", "trilobites", "ammonite", "ammonites", "megalodon",
        "diplomystus", "milf", "man i love fossils", "jurassic", "dinosaur", "dinosaurio"
    ]
    resultado = texto
    if not any(p in sujeto_nombre.lower() for p in ["fósil", "fosil", "fossil", "dinosaur", "paleontolog"]):
        for p in palabras_peligrosas:
            pattern = re.compile(re.escape(p), re.IGNORECASE)
            resultado = pattern.sub("especímenes", resultado)
    return resultado


def analizar_referencias(
    imagenes_referencia: list[Image.Image],
    sujeto_nombre: str = "",
    incluir_colores: bool = False,
) -> dict:
    """
    Analiza la(s) polera(s) de referencia para adaptar su técnica visual y diagramación
    al sujeto objetivo (sujeto_nombre), ignorando por completo el tema viejo de la referencia.
    """
    instruccion_colores = (
        """
      "paleta_referencia_descripcion": "<describe la armonía cromática por roles de forma abstracta: ej 'Títulos en tinta contrastante, fondo en papel envejecido crema, ilustraciones con tintas apagadas terrosas y acentos sutiles'>","""
        if incluir_colores
        else """
      "paleta_referencia_descripcion": "Los colores se definirán externamente.","""
    )

    instruccion_multiple = (
        f"Te muestro {len(imagenes_referencia)} imágenes de poleras de referencia. Analízalas en conjunto y sintetiza una estructura de diseño combinada."
        if len(imagenes_referencia) > 1
        else "Analiza esta imagen de diseño de polera de referencia."
    )

    prompt = f"""
    {instruccion_multiple}
    Actúa como un director de arte y tipógrafo experto.
    El usuario va a crear un nuevo diseño de polera cuyo tema e ilustraciones serán: "{sujeto_nombre}".
    
    Tu tarea es extraer ÚNICAMENTE la TÉCNICA VISUAL (ej: grabado botánico/científico vintage, fotocollage 90s, serigrafía vectorial, etc.), la TIPOGRAFÍA y la DIAGRAMACIÓN de la referencia para aplicarlas a "{sujeto_nombre}".

    REGLAS ESTRICTAS DE ADAPTACIÓN:
    1. La imagen de referencia tiene un tema anterior (viejo) que DEBES IGNORAR COMPLETAMENTE. NUNCA transcribas palabras, frases, lemas ni textos que veas en la foto de referencia (como memes, lemas o marcas).
    2. NUNCA menciones los objetos, animales, fósiles, bandas o temas antiguos de la foto de referencia. Toda la descripción técnica debe estar redactada en función de cómo ilustrar "{sujeto_nombre}".
    3. Responde EXCLUSIVAMENTE con un JSON válido con esta estructura:

    {{
      "tipo_layout": "<'lamina_especimenes_catalogo' | 'collage_merch' | 'sujeto_central_hero' | 'vector_minimal' | 'poster_vintage'>",
      "cantidad_elementos_en_layout": <número entero: cuántas ilustraciones o retratos individuales componen la pieza, ej 1, 2, 3, 6, 8, 10, etc.>,
      "distribucion_elementos": [
        {{
          "posicion": "<ubicación: ej 'Fila superior izquierda', 'Centro dominante', 'Inferior derecha'>",
          "tipo_vista": "<ej 'Vista completa del elemento', 'Detalle en primer plano', 'Vista angular o en perspectiva'>"
        }}
      ],
      "tipografia_titulo": {{
        "estilo_fuente": "<anatomía de la fuente: ej 'Serif condensada elegante estilo enciclopedia clásica', 'Sans-serif ultra bold condensada geométrica', 'Gothic / Blackletter con remates afilados', 'Script fluida y elegante'>",
        "detalles_visuales": "<efectos de texto: ej 'Mayúsculas con serifa nítida y alto contraste', 'Relieve cromado metálico con sombra 3D', 'Texto plano con textura gastada de serigrafía'>",
        "posicion_y_forma": "<disposición: ej 'Arriba centrado en bloque horizontal amplio', 'Arqueado sobre los elementos', 'Enmarcado en caja superior'>"
      }},
      "estilo_subtitulos_y_rotulos": "<estilo tipográfico de textos secundarios o etiquetas pequeñas bajo cada elemento: ej 'Sans-serif bold pequeña en mayúsculas centrada', 'Tipografía tipo máquina de escribir o rótulo enciclopédico vintage'>",
      "medio_y_tecnica": "<técnica artística abstracta aplicada a {sujeto_nombre}: ej 'Ilustración estilo grabado enciclopédico/científico vintage a tinta con achurado lineal fino y coloreado tenue', 'Fotocollage estilo bootleg rap tee de los 90s con semitono de serigrafía', 'Ilustración vectorial plana estilo anime con líneas limpias'>",
      "composicion_general": "<jerarquía visual: ej 'Composición de lámina o catálogo con título superior, subtítulo en bloque, cuadrícula equilibrada de ilustraciones de {sujeto_nombre} con pequeños rótulos descriptivos bajo cada una'>",
      "tono_y_saturacion": "<clima cromático y contraste: ej 'Fondo neutro/crema con tonos naturales apagados y contraste medio', 'Contraste alto con tonos oscuros y brillos metálicos'>",
      "estilo_grafico_detalles": "<elementos ornamentales o texturas: ej 'Marco perimetral delgado, textura de papel envejecido sutil, achurado lineal clásico, destellos'>{instruccion_colores}
    }}
    """

    fallback_ref = {
        "tipo_layout": "sujeto_central_hero",
        "cantidad_elementos_en_layout": 1,
        "distribucion_elementos": [{"posicion": "Centro principal", "tipo_vista": "Vista principal"}],
        "tipografia_titulo": {
            "estilo_fuente": "Tipografía bold de alto impacto para merch",
            "detalles_visuales": "Sombra 3D y relieve limpio con buen contraste",
            "posicion_y_forma": "Arriba centrado",
        },
        "estilo_subtitulos_y_rotulos": "Sans-serif bold pequeña",
        "medio_y_tecnica": "Ilustración gráfica profesional para serigrafía",
        "composicion_general": "Composición centrada equilibrada",
        "tono_y_saturacion": "Contraste equilibrado y tonos naturales",
        "estilo_grafico_detalles": "Acabado de serigrafía profesional",
        "paleta_referencia_descripcion": "",
    }

    try:
        respuesta = client.models.generate_content(
            model=MODELO_VISION,
            contents=[prompt, *imagenes_referencia],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        data = _parsear_json_seguro(respuesta.text, fallback=fallback_ref)
    except Exception as e:
        print(f"   Aviso: análisis de referencias con JSON schema falló ({e}), reintentando...")
        try:
            respuesta = client.models.generate_content(
                model=MODELO_VISION,
                contents=[prompt, *imagenes_referencia],
            )
            data = _parsear_json_seguro(respuesta.text, fallback=fallback_ref)
        except Exception:
            data = fallback_ref

    data["medio_y_tecnica"] = _sanitizar_texto_de_referencia(data.get("medio_y_tecnica", ""), sujeto_nombre)
    data["composicion_general"] = _sanitizar_texto_de_referencia(data.get("composicion_general", ""), sujeto_nombre)
    data["estilo_grafico_detalles"] = _sanitizar_texto_de_referencia(data.get("estilo_grafico_detalles", ""), sujeto_nombre)
    data["estilo_subtitulos_y_rotulos"] = _sanitizar_texto_de_referencia(data.get("estilo_subtitulos_y_rotulos", ""), sujeto_nombre)
    return data


def analizar_sujeto(
    imagenes_sujeto: list[Image.Image],
    titulo: str,
    descripcion_usuario: str,
    frases_sueltas: list[str],
) -> dict:
    """
    Analiza las fotos que el usuario subió como sujeto (plantas, personas, animales, autos, etc.)
    y extrae qué es, sus rasgos clave y una lista de variaciones coherentes para poblar el diseño.
    """
    frases_str = ", ".join(frases_sueltas) if frases_sueltas else "ninguna"
    pista_sujeto = f"Descripción del usuario: '{descripcion_usuario}'" if descripcion_usuario else ""
    prompt = f"""
    Actúa como un experto en análisis visual y taxonomía.
    Analiza esta(s) imagen(es) que representan el SUJETO que debe ilustrarse en un diseño de polera.
    Título dado por el usuario: "{titulo}"
    {pista_sujeto}
    Frases secundarias: "{frases_str}"

    Identifica qué es exactamente el sujeto (planta botánica, persona, animal, vehículo, objeto, etc.), describe sus rasgos visuales clave y genera una lista de 8 a 10 variaciones o detalles concretos del MISMO sujeto para usarlos en el diseño (por ejemplo, si es una planta Monstera: hoja madura fenestrada, hoja joven desenrollándose, esqueje con raíz aérea, detalle de fenestración foliar, brote nuevo, etc.; si es una persona: distintas poses y expresiones; si es un auto: distintos ángulos).

    Responde EXCLUSIVAMENTE con un JSON válido con esta estructura:
    {{
      "categoria_sujeto": "<'planta_botanica' | 'persona_personaje' | 'animal' | 'vehiculo' | 'objeto_otro'>",
      "nombre_exacto_sujeto": "<nombre preciso: ej 'Planta Monstera Deliciosa (Costilla de Adán)', 'Cantante femenina', 'Gato atigrado'>",
      "descripcion_visual_detallada": "<descripción de colores, formas, texturas, hojas, anatomía o elementos característicos del sujeto>",
      "variaciones_visuales": [
        "<variación concreta 1 del sujeto>",
        "<variación concreta 2 del sujeto>",
        "<variación concreta 3 del sujeto>",
        "<variación concreta 4 del sujeto>",
        "<variación concreta 5 del sujeto>",
        "<variación concreta 6 del sujeto>",
        "<variación concreta 7 del sujeto>",
        "<variación concreta 8 del sujeto>"
      ]
    }}
    """

    nombre_fallback = descripcion_usuario if descripcion_usuario else (titulo if titulo else "Sujeto adjunto")
    fallback_sujeto = {
        "categoria_sujeto": "objeto_otro",
        "nombre_exacto_sujeto": nombre_fallback,
        "descripcion_visual_detallada": f"Sujeto mostrado en las imágenes adjuntas ({nombre_fallback})",
        "variaciones_visuales": [
            f"Vista principal representativa de {nombre_fallback}",
            f"Primer plano de detalle de {nombre_fallback}",
            f"Ángulo 3/4 en perspectiva de {nombre_fallback}",
            f"Vista alternativa de {nombre_fallback}",
        ],
    }

    try:
        respuesta = client.models.generate_content(
            model=MODELO_VISION,
            contents=[prompt, *imagenes_sujeto],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        return _parsear_json_seguro(respuesta.text, fallback=fallback_sujeto)
    except Exception as e:
        print(f"   Aviso: análisis de sujeto falló ({e}), usando fallback...")
        return fallback_sujeto


def quitar_fondo_por_chroma(
    imagen: Image.Image, color_chroma_hex: str, tolerancia: float = 60.0, suavizado: float = 40.0
) -> Image.Image:
    """
    Post-proceso para el diseño YA GENERADO por Gemini: como le pedimos un fondo
    de un color EXACTO y conocido (color_chroma_hex), no tiene sentido usar un
    modelo de IA (rembg) para "adivinar" qué es fondo y qué no -- eso es lo que
    estaba causando el problema de fondo inconsistente (aparecía en algunas
    zonas y en otras no), porque el modelo de segmentación de rembg está
    entrenado para fotos naturales, no para diseños gráficos con fondo plano.

    En vez de eso, esto es un chroma key clásico (la misma técnica de pantalla
    verde de cine): se mide qué tan cerca está cada pixel del color de fondo
    exacto, y se vuelve transparente todo lo que esté suficientemente cerca.
    Esto es determinístico -- si el fondo es parejo (que es lo que le pedimos
    a Gemini), el resultado también lo es, sin parches ni sorpresas.

    tolerancia: qué tan lejos del color exacto todavía se considera "fondo".
    suavizado: ancho de la transición (para que el borde no quede 100% duro).
    """
    import numpy as np
    from PIL import ImageFilter

    imagen_rgb = imagen.convert("RGB")
    arreglo = np.asarray(imagen_rgb).astype(np.float32)

    hex_limpio = color_chroma_hex.lstrip("#")
    color_objetivo = np.array(
        [int(hex_limpio[0:2], 16), int(hex_limpio[2:4], 16), int(hex_limpio[4:6], 16)],
        dtype=np.float32,
    )

    distancia = np.sqrt(((arreglo - color_objetivo) ** 2).sum(axis=2))

    # Debajo de "tolerancia" -> transparente (alpha 0); por encima de
    # tolerancia+suavizado -> opaco (alpha 255); en el medio, transición lineal.
    alpha = np.clip((distancia - tolerancia) / suavizado, 0, 1) * 255
    canal_alpha = Image.fromarray(alpha.astype("uint8"), mode="L")
    canal_alpha = canal_alpha.filter(ImageFilter.GaussianBlur(radius=1))

    r, g, b = imagen_rgb.split()
    return Image.merge("RGBA", (r, g, b, canal_alpha))


def generar_diseno_desde_imagenes(
    imagenes_referencia: list[Image.Image],
    imagenes_personaje: list[Image.Image],
    recortar_personaje: bool,
    usar_colores_referencia: bool,
    colores: list[str],
    titulo: str,
    tamano_titulo: str,
    frases_sueltas: list[str],
    modelo_imagen: str = MODELOS_IMAGEN[MODELO_IMAGEN_POR_DEFECTO],
    color_fondo_solido: str | None = None,
    descripcion_sujeto: str = "",
    tipo_sujeto: str = "Auto-detectar",
) -> Image.Image:
    """
    Genera el diseño completo combinando la plantilla abstracta de la referencia
    con el sujeto real que el usuario subió (personas, plantas, animales, autos, etc.).
    """
    print("1/5 Analizando sujeto(s) a ilustrar...")
    analisis_sujeto = analizar_sujeto(imagenes_personaje, titulo, descripcion_sujeto, frases_sueltas)
    sujeto_nombre = descripcion_sujeto.strip() if descripcion_sujeto.strip() else analisis_sujeto.get("nombre_exacto_sujeto", titulo or "Sujeto adjunto")
    sujeto_desc = analisis_sujeto.get("descripcion_visual_detallada", "")
    sujeto_cat = analisis_sujeto.get("categoria_sujeto", "objeto_otro")
    variaciones = analisis_sujeto.get("variaciones_visuales", [])

    print("2/5 Analizando referencia(s) (adaptando plantilla y estilo al sujeto)...")
    analisis_ref = analizar_referencias(imagenes_referencia, sujeto_nombre=sujeto_nombre, incluir_colores=usar_colores_referencia)
    perfil_color = extraer_perfil_color(imagenes_referencia)

    cant_elementos = max(1, int(analisis_ref.get("cantidad_elementos_en_layout", len(analisis_ref.get("distribucion_elementos", [])) or 1)))
    medio_tec = analisis_ref.get("medio_y_tecnica", "Gráfica de polera")
    layout_tipo = analisis_ref.get("tipo_layout", "layout")

    print(
        f"   Sujeto identificado: {sujeto_nombre} ({sujeto_cat}) | "
        f"Plantilla: {layout_tipo} con {cant_elementos} elemento(s) | "
        f"Técnica: {medio_tec[:30]}... | "
        f"Saturación: {perfil_color['saturacion_promedio_pct']}% ({perfil_color['categoria_tono']})"
    )

    if recortar_personaje:
        print(f"   Recortando fondo de {len(imagenes_personaje)} imagen(es) de sujeto...")
        from rembg import remove
        imagenes_personaje_final = []
        for img in imagenes_personaje:
            img_reducida = _redimensionar_si_es_muy_grande(img)
            buffer_entrada = io.BytesIO()
            img_reducida.save(buffer_entrada, format="PNG")
            resultado = remove(
                buffer_entrada.getvalue(),
                alpha_matting=True,
                alpha_matting_foreground_threshold=240,
                alpha_matting_background_threshold=10,
                alpha_matting_erode_size=5,
            )
            imagenes_personaje_final.append(Image.open(io.BytesIO(resultado)))
    else:
        imagenes_personaje_final = imagenes_personaje

    print("3/5 Armando prompt final estructurado...")
    
    # 1. Asignación de elementos/especímenes del sujeto a la cuadrícula
    distribucion = analisis_ref.get("distribucion_elementos", [])
    lineas_elementos = []
    for i in range(cant_elementos):
        var_texto = variaciones[i % len(variaciones)] if variaciones else f"Variación #{i+1} de {sujeto_nombre}"
        pos_info = distribucion[i].get("posicion", f"Posición #{i+1}") if i < len(distribucion) else f"Posición #{i+1}"
        lineas_elementos.append(
            f"  * Elemento {i+1} ({pos_info}): {var_texto} (de {sujeto_nombre})."
        )
    instrucciones_elementos_texto = "\n".join(lineas_elementos)

    # 2. Tipografía y textos
    tipo_info = analisis_ref.get("tipografia_titulo", {})
    estilo_fuente = tipo_info.get("estilo_fuente", "Tipografía bold de alto impacto para poleras")
    detalles_fuente = tipo_info.get("detalles_visuales", "Relieve y sombra definidos con contraste")
    posicion_fuente = tipo_info.get("posicion_y_forma", "Parte superior del diseño")
    estilo_subtitulos = analisis_ref.get("estilo_subtitulos_y_rotulos", "Sans-serif bold pequeña")

    if frases_sueltas:
        frases_texto = f"Frases secundarias del usuario a incluir integradas al diseño: {', '.join(frases_sueltas)}."
    else:
        frases_texto = "No incluir frases secundarias adicionales (NO escribir frases inventadas ni de la referencia)."

    # 3. Colores y Tono
    if usar_colores_referencia:
        colores_medidos_texto = ", ".join(perfil_color["colores_hex"])
        desc_paleta_ref = analisis_ref.get("paleta_referencia_descripcion", "")
        instruccion_colores = (
            f"Paleta dominante medida de la referencia: {colores_medidos_texto}.\n"
            f"Armonía cromática: {desc_paleta_ref if desc_paleta_ref else 'Aplica los colores dominantes medidos en títulos, ilustraciones y fondos respetando la estética de la referencia.'}"
        )
    else:
        colores_texto = ", ".join(colores) if colores else "#FFFFFF, #000000"
        instruccion_colores = (
            f"Paleta obligatoria elegida por el usuario: {colores_texto}.\n"
            "Aplica estos colores en los textos principales, elementos gráficos y acentos del diseño."
        )

    # 4. Fondo
    if color_fondo_solido:
        instruccion_fondo = (
            f"Fondo plano de color sólido exacto: {color_fondo_solido}. "
            "Uniforme y parejo en toda el área externa fuera del arte gráfico."
        )
    else:
        instruccion_fondo = (
            f"Fondo verde croma EXACTO: {COLOR_CHROMA_DEFECTO} (pantalla verde pura, plana y perfectamente homogénea, "
            "sin sombras, texturas ni degradados en el fondo externo, para posterior recorte transparente. "
            "Ningún elemento de las ilustraciones ni del texto debe usar este mismo verde)."
        )

    prompt_final = f"""
[ESPECIFICACIÓN MAESTRA DE DISEÑO GRÁFICO PARA POLERA / T-SHIRT ARTWORK]

1. SUJETO ÚNICO Y EXCLUSIVO DE TODA LA ILUSTRACIÓN:
- Sujeto objetivo a ilustrar: {sujeto_nombre}
- Características visuales clave de {sujeto_nombre}: {sujeto_desc}
* REGLA ABSOLUTA DE CONTENIDO: Cada una de las ilustraciones en esta pieza debe representar EXCLUSIVAMENTE a {sujeto_nombre}, basándose fielmente en las fotos adjuntas.
* PROHIBICIÓN ESTRICTA: PROHIBIDO dibujar cualquier elemento o tema ajeno a {sujeto_nombre}. PROHIBIDO dibujar fósiles, huesos, dinosaurios, personas o elementos de la imagen de referencia. La referencia es SOLO una plantilla de diagramación; el contenido visual entero es 100% {sujeto_nombre}.

2. DESGLOSE DE ELEMENTOS Y VARIACIONES A ILUSTRAR:
Genera exactamente {cant_elementos} elemento(s)/ilustración(es) en la composición, todos correspondientes a {sujeto_nombre}:
{instrucciones_elementos_texto}
* REGLA DE VARIEDAD: Cada elemento debe ser una variante, perspectiva o detalle único como se especifica arriba. No repetir la misma ilustración idéntica. Si el diseño incluye rótulos pequeños debajo de cada ilustración, estos deben nombrar detalles relevantes de {sujeto_nombre} o términos científicos/botánicos/técnicos relacionados con {sujeto_nombre}.

3. TIPOGRAFÍA Y TEXTOS (TEXTOS EXACTOS DEL USUARIO):
- Título principal: "{titulo}" (tamaño {tamano_titulo})
- Anatomía y estilo de fuente: {estilo_fuente}
- Efectos visuales del título: {detalles_fuente}
- Posición y disposición del título: {posicion_fuente}
- Textos secundarios / subtítulos: {frases_texto} (Estilo tipográfico: {estilo_subtitulos})
* REGLA DE TEXTO: Dibuja ÚNICAMENTE los textos indicados por el usuario ("{titulo}" y frases secundarias especificadas). PROHIBIDO copiar o inventar frases ajenas (como textos de memes, fósiles o frases de la referencia). Ortografía exacta y tipografía nítida.

4. TÉCNICA ARTÍSTICA Y COMPOSICIÓN:
- Técnica / Medio visual dominante: {analisis_ref.get('medio_y_tecnica', 'Ilustración gráfica profesional')} aplicada a {sujeto_nombre}
- Composición y layout: {analisis_ref.get('composicion_general', 'Composición equilibrada')}
- Efectos y texturas gráficas: {analisis_ref.get('estilo_grafico_detalles', 'Detalles de serigrafía clásica')}
* REGLA DE COHERENCIA: Aplica esta MISMA técnica artística de forma uniforme e impecable a todas las ilustraciones de {sujeto_nombre} y a los textos.

5. COLOR, TONO Y FONDO:
- {instruccion_colores}
- Tono e intensidad cromática: Saturación media {perfil_color['saturacion_promedio_pct']}% ({perfil_color['categoria_tono']}), atmósfera: {analisis_ref.get('tono_y_saturacion', 'Equilibrada')}.
- Fondo: {instruccion_fondo}
- RESTRICCIÓN DE SALIDA: Genera EXCLUSIVAMENTE el archivo de arte gráfico 2D plano, de frente, ocupando el lienzo completo. PROHIBIDO generar mockups de poleras, personas vistiendo ropa, pliegues de tela o fondos de estudio.
"""

    # Gemini solo soporta 1K/2K en modelos de imagen
    print(f"4/5 Generando diseño (modelo: {modelo_imagen}, proporción {RELACION_ASPECTO})...")
    
    respuesta_imagen = client.models.generate_content(
        model=modelo_imagen,
        contents=[prompt_final, *imagenes_personaje_final],
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=RELACION_ASPECTO,
            ),
        ),
    )

    imagen_base = None
    for parte in respuesta_imagen.candidates[0].content.parts:
        if parte.inline_data is not None:
            imagen_base = Image.open(io.BytesIO(parte.inline_data.data))
            break

    if imagen_base is None:
        raise RuntimeError("Gemini no devolvió ninguna imagen. Revisa: " + str(respuesta_imagen))

    if color_fondo_solido:
        print("5/5 Fondo sólido pedido explícitamente -> no se quita, se deja tal cual.")
        imagen_resultado = imagen_base
    else:
        print("5/5 Quitando fondo por chroma key exacto (determinístico, no IA)...")
        imagen_resultado = quitar_fondo_por_chroma(imagen_base, COLOR_CHROMA_DEFECTO)

    imagen_resultado = imagen_resultado.resize(
        (ANCHO_FINAL_PX, ALTO_FINAL_PX), Image.LANCZOS
    )

    return imagen_resultado


if __name__ == "__main__":
    # ---- EDITA ESTOS VALORES PARA TU PRUEBA POR LÍNEA DE COMANDOS ----
    # (si prefieres la interfaz visual, corre "python -m streamlit run app_visual.py")
    referencias = [Image.open("referencia.jpg")]  # agrega más rutas a la lista si quieres varias
    personajes = [Image.open("personaje.jpg")]     # ídem para varias fotos del personaje

    imagen_final = generar_diseno_desde_imagenes(
        imagenes_referencia=referencias,
        imagenes_personaje=personajes,
        recortar_personaje=True,
        usar_colores_referencia=False,
        colores=["#FF3EA5", "#00E5FF", "#FFFFFF"],
        titulo="MI GRUPO",
        tamano_titulo="grande",
        frases_sueltas=["WORLD TOUR", "2026"],
        modelo_imagen=MODELOS_IMAGEN["Lite (más barato, ideal para probar)"],
    )
    imagen_final.save("resultado.png")
    print("\n✅ Listo, revisa resultado.png")
