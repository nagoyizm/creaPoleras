"""
Generador de diseños de poleras.

Flujo:
1. Valida imágenes y conexión previa.
2. Analiza el sujeto (o usa descripción directa/caché para ahorrar tokens).
3. Analiza la(s) imagen(es) de referencia (estructura/composición) -> Gemini Flash (barato).
4. Si corresponde, quita el fondo de la(s) foto(s) del personaje -> rembg (local, gratis).
5. Arma el prompt maestro final combinando: estructura + colores + texto + poses variadas.
6. Genera la imagen final con Gemini Image (Lite/Media/Pro) con inspección forense de seguridad y cuota.
7. Aplica recorte chroma determinístico o deja fondo sólido según configuración.
"""

import os
import sys
import io
import json
import re
import time
import colorsys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image
from google import genai
from google.genai import types
from colorthief import ColorThief

load_dotenv()


class ErrorProcesoPolera(Exception):
    """
    Excepción estructurada para capturar con precisión en qué fase y por qué falló el proceso,
    evitando que el usuario pierda tokens sin saber la causa.
    """
    def __init__(
        self,
        fase: str,
        mensaje: str,
        diagnostico: str = "",
        sugerencia: str = "",
        error_original: Exception | None = None,
        detalle_tecnico: str = "",
        tokens_gastados: dict | None = None,
    ):
        super().__init__(mensaje)
        self.fase = fase
        self.mensaje = mensaje
        self.diagnostico = diagnostico
        self.sugerencia = sugerencia
        self.error_original = error_original
        self.detalle_tecnico = detalle_tecnico or (str(error_original) if error_original else "")
        self.tokens_gastados = tokens_gastados or {}


def diagnosticar_error_api(e: Exception) -> tuple[str, str]:
    """
    Analiza una excepción de Google GenAI o del sistema y devuelve (diagnostico, sugerencia).
    """
    err_str = str(e).lower()

    if "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str:
        return (
            "Se ha agotado la cuota de peticiones o el límite de saldo configurado en tu cuenta de Google AI Studio.",
            "Revisa tu cuenta en https://aistudio.google.com/ para verificar el saldo de facturación o espera un minuto a que se restablezca el límite por minuto (RPM/TPM).",
        )
    if "403" in err_str or "permission_denied" in err_str or "unregistered" in err_str:
        return (
            "Problema de permisos o clave de API no válida en Google AI Studio.",
            "Revisa tu clave en el archivo .env (GEMINI_API_KEY). Asegúrate de que no tenga comillas ni espacios adicionales y que el proyecto tenga habilitada la API de Gemini.",
        )
    if "404" in err_str or "not_found" in err_str:
        return (
            "El modelo solicitado ya no está disponible o cambió de nombre en la API de Google.",
            "Google actualiza periódicamente sus modelos. Prueba seleccionando otro modelo en el menú de Calidad/Costo.",
        )
    if "400" in err_str or "invalid_argument" in err_str:
        return (
            "La petición fue rechazada por argumentos o formato de imagen no válido.",
            "Verifica que las imágenes subidas no estén dañadas o tengan formatos atípicos, o reduce la cantidad de imágenes subidas.",
        )
    if "safety" in err_str or "blocked" in err_str or "prohibited" in err_str:
        return (
            "La generación fue bloqueada por las políticas de contenido o derechos de autor de Google.",
            "Modifica el título o la descripción del sujeto para evitar nombres de celebridades, marcas con copyright fuerte o términos sensibles.",
        )
    if "memory" in err_str or "out of memory" in err_str or "killed" in err_str:
        return (
            "El servidor se quedó sin memoria RAM al procesar las imágenes.",
            "Si estás corriendo en un VPS Contabo con 2GB/4GB de RAM, desmarca la opción 'Recortar solo al personaje' o sube imágenes más livianas.",
        )
    if "connection" in err_str or "timeout" in err_str or "timed out" in err_str:
        return (
            "Tiempo de espera agotado o corte de red con los servidores de Google AI Studio.",
            "Verifica la conexión a internet de tu servidor y vuelve a intentar.",
        )

    return (
        f"Error en la llamada: {e}",
        "Revisa la consola de detalles técnicos para ver el error completo.",
    )


def obtener_cliente_gemini() -> genai.Client:
    """Devuelve una instancia configurada de genai.Client o lanza ErrorProcesoPolera si falta la key."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ErrorProcesoPolera(
            fase="Validación de Credenciales",
            mensaje="No se encontró la variable GEMINI_API_KEY.",
            diagnostico="El archivo .env no existe o no contiene una clave de API válida.",
            sugerencia="Copia .env.example como .env y coloca tu GEMINI_API_KEY de https://aistudio.google.com/.",
        )
    return genai.Client(api_key=api_key)


def validar_conexion_api() -> dict:
    """
    Verifica rápidamente la conectividad con la API de Gemini antes de procesar archivos.
    Devuelve un diccionario con el estado.
    """
    try:
        client = obtener_cliente_gemini()
        resp = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents="ping",
        )
        return {"ok": True, "mensaje": "Conexión exitosa con Google AI Studio"}
    except Exception as e:
        diag, sug = diagnosticar_error_api(e)
        return {
            "ok": False,
            "mensaje": str(e),
            "diagnostico": diag,
            "sugerencia": sug,
        }


def _notificar_progreso(
    callback,
    etapa_idx: int,
    total_etapas: int,
    fase: str,
    mensaje: str,
    estado: str = "running",
    detalle: dict | None = None,
):
    """Emite evento de progreso tanto a consola (stdout/docker logs) como al callback de UI."""
    timestamp = datetime.now().strftime("%H:%M:%S")
    simbolo = {"running": "⏳", "success": "✅", "warning": "⚠️", "error": "❌", "info": "ℹ️"}.get(estado, "•")
    print(f"[{timestamp}] {simbolo} [{etapa_idx}/{total_etapas}] {fase}: {mensaje}", flush=True)
    if callback:
        try:
            callback(etapa_idx, total_etapas, fase, mensaje, estado, detalle or {})
        except Exception as e:
            print(f"   [Aviso callback]: {e}", flush=True)


# Modelo usado para analizar imágenes (rápido y económico)
MODELO_VISION = "gemini-3.5-flash-lite"
MODELO_VISION_FALLBACK = "gemini-3.6-flash"

# Modelos de generación de imagen disponibles en la interfaz
MODELOS_IMAGEN = {
    "Lite (más barato, ideal para probar)": "gemini-3.1-flash-lite-image",
    "Media (balance calidad/precio)": "gemini-3.1-flash-image",
    "Pro (mejor calidad, más caro)": "gemini-3-pro-image",
}
MODELO_IMAGEN_POR_DEFECTO = "Lite (más barato, ideal para probar)"

ANCHO_FINAL_PX = 1620
ALTO_FINAL_PX = 2160
RELACION_ASPECTO = "3:4"

DIMENSIONES_POR_PROPORCION = {
    "3:4": (1620, 2160),
    "1:1": (2048, 2048),
    "9:16": (1215, 2160),
    "4:3": (2160, 1620),
}

COLOR_CHROMA_DEFECTO = "#00FF7F"

# Reducción optimizada:
# 768px es ideal para análisis de visión (reduce drásticamente tokens de entrada vs imágenes crudas)
LADO_MAXIMO_VISION = 768
# 1024px para acondicionamiento y recorte
LADO_MAXIMO_PARA_RECORTE = 1024


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
    Extrae objetivamente la paleta dominante y saturación promedio sin costo de tokens.
    """
    todos_los_colores = []
    for imagen in imagenes_referencia:
        img_optimizada = _redimensionar_si_es_muy_grande(imagen, 400)
        buffer_entrada = io.BytesIO()
        img_optimizada.convert("RGB").save(buffer_entrada, format="PNG")
        buffer_entrada.seek(0)
        try:
            color_thief = ColorThief(buffer_entrada)
            paleta = color_thief.get_palette(color_count=cantidad_colores, quality=1)
            todos_los_colores.extend(paleta)
        except Exception:
            pass

    if not todos_los_colores:
        todos_los_colores = [(255, 255, 255), (0, 0, 0)]

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
    callback_progreso=None,
    etapa_idx: int = 3,
    total_etapas: int = 6,
) -> tuple[dict, dict]:
    """
    Analiza la(s) polera(s) de referencia para adaptar su técnica visual y diagramación
    al sujeto objetivo (sujeto_nombre).
    Devuelve (datos_analisis, metadatos_tokens).
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
      "trazo_y_linea": "<grosor y tipo de contorno: ej 'Línea de tinta negra gruesa e imperfecta estilo serigrafía analógica', 'Trazo fino y nítido de pluma técnica estilo grabado', 'Sin contornos exteriores, figuras delimitadas por color plano'>",
      "metodo_sombreado": "<técnica exacta de sombreado: ej 'Trama de puntos de semitono / halftone serigráfico', 'Achurado lineal cruzado fino (cross-hatching) a tinta', 'Sombras planas de dos tonos cel-shading', 'Degradado suave continuo'>",
      "tratamiento_tintas": "<aplicación del color: ej 'Tintas planas y opacas limitadas a 3-4 colores estilo serigrafía textil', 'Semitono CMYK superpuesto con ganancia de punto vintage', 'Aguadas tenues translúcidas'>",
      "textura_y_sustrato": "<acabado superficial y desgaste: ej 'Grano de papel envejecido y manchas sutiles', 'Efecto de tela lavada ácida desgastada (vintage wash)', 'Acabado liso y nítido'>",
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
        "trazo_y_linea": "Trazo definido con línea nítida de serigrafía",
        "metodo_sombreado": "Sombreado gráfico de alto contraste",
        "tratamiento_tintas": "Tintas planas bien contrastadas",
        "textura_y_sustrato": "Textura gráfica limpia para estampado",
        "composicion_general": "Composición centrada equilibrada",
        "tono_y_saturacion": "Contraste equilibrado y tonos naturales",
        "estilo_grafico_detalles": "Acabado de serigrafía profesional",
        "paleta_referencia_descripcion": "",
    }

    # Redimensionar a max 768px para visión (ahorro masivo de tokens)
    imagenes_optimizadas_vision = [_redimensionar_si_es_muy_grande(img, LADO_MAXIMO_VISION) for img in imagenes_referencia]

    client = obtener_cliente_gemini()
    tokens_meta = {"prompt": 0, "candidates": 0, "total": 0}

    try:
        respuesta = client.models.generate_content(
            model=MODELO_VISION,
            contents=[prompt, *imagenes_optimizadas_vision],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        if hasattr(respuesta, "usage_metadata") and respuesta.usage_metadata:
            tokens_meta["prompt"] = getattr(respuesta.usage_metadata, "prompt_token_count", 0) or 0
            tokens_meta["candidates"] = getattr(respuesta.usage_metadata, "candidates_token_count", 0) or 0
            tokens_meta["total"] = getattr(respuesta.usage_metadata, "total_token_count", 0) or 0

        data = _parsear_json_seguro(respuesta.text, fallback=fallback_ref)
    except Exception as e:
        diag, sug = diagnosticar_error_api(e)
        # Si fue error de cuota o permisos, no quemar reintentos
        if "429" in str(e) or "quota" in str(e).lower() or "403" in str(e):
            raise ErrorProcesoPolera(
                fase="Análisis de Referencias",
                mensaje=f"Error en Google AI Studio al analizar referencias: {e}",
                diagnostico=diag,
                sugerencia=sug,
                error_original=e,
            )
        _notificar_progreso(callback_progreso, etapa_idx, total_etapas, "Análisis de Referencias", f"Aviso: Falló análisis estructurado ({e}), usando modelo fallback...", "warning")
        try:
            respuesta = client.models.generate_content(
                model=MODELO_VISION_FALLBACK,
                contents=[prompt, *imagenes_optimizadas_vision],
            )
            data = _parsear_json_seguro(respuesta.text, fallback=fallback_ref)
        except Exception:
            data = fallback_ref

    data["medio_y_tecnica"] = _sanitizar_texto_de_referencia(data.get("medio_y_tecnica", ""), sujeto_nombre)
    data["trazo_y_linea"] = _sanitizar_texto_de_referencia(data.get("trazo_y_linea", ""), sujeto_nombre)
    data["metodo_sombreado"] = _sanitizar_texto_de_referencia(data.get("metodo_sombreado", ""), sujeto_nombre)
    data["tratamiento_tintas"] = _sanitizar_texto_de_referencia(data.get("tratamiento_tintas", ""), sujeto_nombre)
    data["textura_y_sustrato"] = _sanitizar_texto_de_referencia(data.get("textura_y_sustrato", ""), sujeto_nombre)
    data["composicion_general"] = _sanitizar_texto_de_referencia(data.get("composicion_general", ""), sujeto_nombre)
    data["estilo_grafico_detalles"] = _sanitizar_texto_de_referencia(data.get("estilo_grafico_detalles", ""), sujeto_nombre)
    data["estilo_subtitulos_y_rotulos"] = _sanitizar_texto_de_referencia(data.get("estilo_subtitulos_y_rotulos", ""), sujeto_nombre)

    return data, tokens_meta


def analizar_sujeto(
    imagenes_sujeto: list[Image.Image],
    titulo: str,
    descripcion_usuario: str,
    frases_sueltas: list[str],
    callback_progreso=None,
    etapa_idx: int = 2,
    total_etapas: int = 6,
) -> tuple[dict, dict]:
    """
    Analiza las fotos del sujeto con visión económica de Gemini.
    Devuelve (datos_sujeto, metadatos_tokens).
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

    # Redimensionar fotos a max 768px para visión (ahorro de tokens y memoria)
    imagenes_optimizadas_vision = [_redimensionar_si_es_muy_grande(img, LADO_MAXIMO_VISION) for img in imagenes_sujeto]

    client = obtener_cliente_gemini()
    tokens_meta = {"prompt": 0, "candidates": 0, "total": 0}

    try:
        respuesta = client.models.generate_content(
            model=MODELO_VISION,
            contents=[prompt, *imagenes_optimizadas_vision],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        if hasattr(respuesta, "usage_metadata") and respuesta.usage_metadata:
            tokens_meta["prompt"] = getattr(respuesta.usage_metadata, "prompt_token_count", 0) or 0
            tokens_meta["candidates"] = getattr(respuesta.usage_metadata, "candidates_token_count", 0) or 0
            tokens_meta["total"] = getattr(respuesta.usage_metadata, "total_token_count", 0) or 0

        return _parsear_json_seguro(respuesta.text, fallback=fallback_sujeto), tokens_meta
    except Exception as e:
        diag, sug = diagnosticar_error_api(e)
        if "429" in str(e) or "quota" in str(e).lower() or "403" in str(e):
            raise ErrorProcesoPolera(
                fase="Análisis de Sujeto",
                mensaje=f"Error en Google AI Studio al analizar el sujeto: {e}",
                diagnostico=diag,
                sugerencia=sug,
                error_original=e,
            )
        _notificar_progreso(callback_progreso, etapa_idx, total_etapas, "Análisis de Sujeto", f"Aviso: Análisis falló ({e}), usando descripción proporcionada...", "warning")
        return fallback_sujeto, tokens_meta


def quitar_fondo_por_chroma(
    imagen: Image.Image, color_chroma_hex: str, tolerancia: float = 60.0, suavizado: float = 40.0
) -> Image.Image:
    """
    Chroma key determinístico local: separa el fondo plano exacto sin IA ni costo de API.
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
    tipo_sujeto: str = "Auto-detectar con IA",
    pose_personalizada: str = "",
    encuadre: str = "Auto",
    expresion: str = "Auto",
    distribucion_figuras: str = "Auto",
    tecnica_artistica: str = "Auto",
    acabado_textil: str = "Auto",
    bordes: str = "Auto",
    iluminacion: str = "Auto",
    acentos_graficos: list[str] | None = None,
    instrucciones_extra: str = "",
    elementos_a_evitar: str = "",
    proporcion_estampado: str = "3:4",
    nivel_estilizacion: str = "Transformación artística total (Recomendado: máxima fidelidad de estilo)",
    callback_progreso=None,
    cache_analisis: dict | None = None,
    modo_ahorro_tokens: bool = False,
) -> Image.Image:
    """
    Genera el diseño completo combinando la plantilla abstracta de la referencia
    con el sujeto real del usuario.
    
    Incluye:
    - Callback de progreso y logging en tiempo real para la consola Streamlit.
    - Manejo de excepciones detallado (ErrorProcesoPolera).
    - Caché inteligente de análisis para 0 consumo de tokens en repeticiones.
    - Medición exacta de tokens y costo de Google AI Studio.
    """
    total_etapas = 6
    tiempo_inicio_global = time.time()
    
    registro_tokens = {
        "analisis_sujeto": {"prompt": 0, "candidates": 0, "total": 0, "desde_cache": False},
        "analisis_referencias": {"prompt": 0, "candidates": 0, "total": 0, "desde_cache": False},
        "generacion_imagen": {"prompt": 0, "candidates": 0, "total": 0},
        "total_tokens": 0,
    }

    # =========================================================================
    # ETAPA 1: Validación Previa
    # =========================================================================
    _notificar_progreso(callback_progreso, 1, total_etapas, "Validación previa", "Verificando credenciales, imágenes y parámetros...", "running")
    
    if not imagenes_referencia or not imagenes_personaje:
        raise ErrorProcesoPolera(
            fase="Validación Previa",
            mensaje="Faltan imágenes requeridas.",
            diagnostico="Se requiere al menos una imagen de referencia y al menos una foto del sujeto.",
            sugerencia="Sube al menos un archivo en el paso 1 y en el paso 2 de la interfaz.",
        )

    if not titulo or not titulo.strip():
        raise ErrorProcesoPolera(
            fase="Validación Previa",
            mensaje="El título principal está vacío.",
            diagnostico="El diseño necesita al menos un título para la tipografía central.",
            sugerencia="Escribe un título en el campo de texto (Paso 4).",
        )

    client = obtener_cliente_gemini()
    _notificar_progreso(callback_progreso, 1, total_etapas, "Validación previa", "Imágenes y credenciales verificadas correctamente.", "success")

    # =========================================================================
    # ETAPA 2: Análisis del Sujeto (con Caché y Modo Ahorro)
    # =========================================================================
    t0 = time.time()
    analisis_sujeto = None

    # Verificar si está en caché
    if cache_analisis and "analisis_sujeto" in cache_analisis:
        analisis_sujeto = cache_analisis["analisis_sujeto"]
        registro_tokens["analisis_sujeto"]["desde_cache"] = True
        _notificar_progreso(
            callback_progreso, 2, total_etapas, "Análisis de Sujeto",
            f"⚡ [Caché Activa] Sujeto reutilizado ({analisis_sujeto.get('nombre_exacto_sujeto')}). ¡0 tokens consumidos!",
            "success",
        )
    elif modo_ahorro_tokens and descripcion_sujeto.strip() and tipo_sujeto != "Auto-detectar con IA":
        # Modo ahorro: omitir llamada a visión si el usuario ya explicó qué es
        analisis_sujeto = {
            "categoria_sujeto": tipo_sujeto,
            "nombre_exacto_sujeto": descripcion_sujeto.strip(),
            "descripcion_visual_detallada": f"{tipo_sujeto}: {descripcion_sujeto.strip()}",
            "variaciones_visuales": [
                f"Vista principal de {descripcion_sujeto.strip()}",
                f"Detalle en primer plano de {descripcion_sujeto.strip()}",
                f"Ángulo dinámico de {descripcion_sujeto.strip()}",
                f"Composición secundaria de {descripcion_sujeto.strip()}",
            ],
        }
        registro_tokens["analisis_sujeto"]["desde_cache"] = True
        _notificar_progreso(
            callback_progreso, 2, total_etapas, "Análisis de Sujeto",
            f"⚡ [Modo Ahorro] Omitida llamada a IA de visión. Usando descripción directa ({descripcion_sujeto.strip()}). ¡0 tokens!",
            "success",
        )
    else:
        _notificar_progreso(callback_progreso, 2, total_etapas, "Análisis de Sujeto", "Analizando taxonomía y rasgos visuales del sujeto con Gemini Vision...", "running")
        try:
            analisis_sujeto, tokens_suj = analizar_sujeto(
                imagenes_sujeto=imagenes_personaje,
                titulo=titulo,
                descripcion_usuario=descripcion_sujeto,
                frases_sueltas=frases_sueltas,
                callback_progreso=callback_progreso,
                etapa_idx=2,
                total_etapas=total_etapas,
            )
            registro_tokens["analisis_sujeto"] = tokens_suj
            dt = round(time.time() - t0, 1)
            _notificar_progreso(
                callback_progreso, 2, total_etapas, "Análisis de Sujeto",
                f"Sujeto identificado: '{analisis_sujeto.get('nombre_exacto_sujeto')}' ({dt}s, {tokens_suj.get('total', 0)} tokens).",
                "success",
            )
        except Exception as e:
            if isinstance(e, ErrorProcesoPolera):
                raise
            diag, sug = diagnosticar_error_api(e)
            raise ErrorProcesoPolera(
                fase="Análisis de Sujeto",
                mensaje=f"Fallo al analizar las fotos del sujeto: {e}",
                diagnostico=diag,
                sugerencia=sug,
                error_original=e,
            )

    sujeto_nombre = descripcion_sujeto.strip() if descripcion_sujeto.strip() else analisis_sujeto.get("nombre_exacto_sujeto", titulo or "Sujeto adjunto")
    sujeto_desc = analisis_sujeto.get("descripcion_visual_detallada", "")
    sujeto_cat = analisis_sujeto.get("categoria_sujeto", "objeto_otro")
    variaciones = analisis_sujeto.get("variaciones_visuales", [])

    # =========================================================================
    # ETAPA 3: Análisis de Referencias y Paleta (con Caché)
    # =========================================================================
    t0 = time.time()
    analisis_ref = None
    perfil_color = None

    if cache_analisis and "analisis_ref" in cache_analisis and "perfil_color" in cache_analisis:
        analisis_ref = cache_analisis["analisis_ref"]
        perfil_color = cache_analisis["perfil_color"]
        registro_tokens["analisis_referencias"]["desde_cache"] = True
        _notificar_progreso(
            callback_progreso, 3, total_etapas, "Análisis de Referencias",
            f"⚡ [Caché Activa] Estructura y paleta reutilizadas ({analisis_ref.get('tipo_layout')}). ¡0 tokens consumidos!",
            "success",
        )
    else:
        _notificar_progreso(callback_progreso, 3, total_etapas, "Análisis de Referencias", "Extrayendo técnica visual, diagramación y paleta de la polera de referencia...", "running")
        try:
            analisis_ref, tokens_ref = analizar_referencias(
                imagenes_referencia=imagenes_referencia,
                sujeto_nombre=sujeto_nombre,
                incluir_colores=usar_colores_referencia,
                callback_progreso=callback_progreso,
                etapa_idx=3,
                total_etapas=total_etapas,
            )
            perfil_color = extraer_perfil_color(imagenes_referencia)
            registro_tokens["analisis_referencias"] = tokens_ref
            dt = round(time.time() - t0, 1)
            _notificar_progreso(
                callback_progreso, 3, total_etapas, "Análisis de Referencias",
                f"Plantilla: {analisis_ref.get('tipo_layout')} | Saturación: {perfil_color['saturacion_promedio_pct']}% ({dt}s, {tokens_ref.get('total', 0)} tokens).",
                "success",
            )
        except Exception as e:
            if isinstance(e, ErrorProcesoPolera):
                raise
            diag, sug = diagnosticar_error_api(e)
            raise ErrorProcesoPolera(
                fase="Análisis de Referencias",
                mensaje=f"Fallo al analizar la referencia de diseño: {e}",
                diagnostico=diag,
                sugerencia=sug,
                error_original=e,
            )

    cant_elementos = max(1, int(analisis_ref.get("cantidad_elementos_en_layout", len(analisis_ref.get("distribucion_elementos", [])) or 1)))
    if distribucion_figuras and not distribucion_figuras.startswith("Auto"):
        if "1 sola figura" in distribucion_figuras:
            cant_elementos = 1
        elif "2-3 figuras" in distribucion_figuras:
            cant_elementos = 3
        elif "Collage múltiple" in distribucion_figuras:
            cant_elementos = 5
        elif "Cuadrícula / Catálogo" in distribucion_figuras:
            cant_elementos = max(cant_elementos, 6)

    # =========================================================================
    # ETAPA 4: Procesamiento y Recorte de Fondo del Personaje (Local, Gratis)
    # =========================================================================
    t0 = time.time()
    imagenes_personaje_final = []

    if recortar_personaje:
        _notificar_progreso(
            callback_progreso, 4, total_etapas, "Recorte de Sujeto",
            f"Recortando fondo de {len(imagenes_personaje)} imagen(es) de forma local (sin coste de tokens)...",
            "running",
        )
        try:
            from rembg import remove
            for idx, img in enumerate(imagenes_personaje):
                img_reducida = _redimensionar_si_es_muy_grande(img, LADO_MAXIMO_PARA_RECORTE)
                buffer_entrada = io.BytesIO()
                img_reducida.save(buffer_entrada, format="PNG")
                try:
                    resultado_recorte = remove(buffer_entrada.getvalue())
                    imagenes_personaje_final.append(Image.open(io.BytesIO(resultado_recorte)))
                except Exception as err_rem:
                    _notificar_progreso(
                        callback_progreso, 4, total_etapas, "Recorte de Sujeto",
                        f"Aviso en recorte #{idx+1} ({err_rem}), usando imagen sin recortar para evitar detención...",
                        "warning",
                    )
                    imagenes_personaje_final.append(img_reducida)
            dt = round(time.time() - t0, 1)
            _notificar_progreso(callback_progreso, 4, total_etapas, "Recorte de Sujeto", f"Recorte completado exitosamente ({dt}s).", "success")
        except Exception as e:
            _notificar_progreso(
                callback_progreso, 4, total_etapas, "Recorte de Sujeto",
                f"Aviso: librería rembg no disponible o con poca RAM ({e}). Continuando con imágenes directas...",
                "warning",
            )
            imagenes_personaje_final = [_redimensionar_si_es_muy_grande(img, LADO_MAXIMO_PARA_RECORTE) for img in imagenes_personaje]
    else:
        _notificar_progreso(callback_progreso, 4, total_etapas, "Recorte de Sujeto", "Se conservan fondos originales según configuración elegida.", "info")
        imagenes_personaje_final = [_redimensionar_si_es_muy_grande(img, LADO_MAXIMO_PARA_RECORTE) for img in imagenes_personaje]

    # =========================================================================
    # ETAPA 5: Construcción de Prompt y Generación de Imagen Final (Gemini)
    # =========================================================================
    _notificar_progreso(callback_progreso, 5, total_etapas, "Generación con IA", "Sintetizando especificación maestra y enviando solicitud a Gemini...", "running")
    
    # 1. Asignación de elementos a la cuadrícula
    distribucion = analisis_ref.get("distribucion_elementos", [])
    lineas_elementos = []
    for i in range(cant_elementos):
        var_texto = variaciones[i % len(variaciones)] if variaciones else f"Variación #{i+1} de {sujeto_nombre}"
        pos_info = distribucion[i].get("posicion", f"Posición #{i+1}") if i < len(distribucion) else f"Posición #{i+1}"
        lineas_elementos.append(f"  * Elemento {i+1} ({pos_info}): {var_texto} (de {sujeto_nombre}).")
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

    directrices_usuario = []
    if pose_personalizada and pose_personalizada.strip():
        directrices_usuario.append(f"- POSE Y ACCIÓN OBLIGATORIA DEL SUJETO: {pose_personalizada.strip()}")
    if encuadre and not encuadre.startswith("Auto"):
        directrices_usuario.append(f"- ENCUADRE / TIPO DE PLANO: {encuadre}")
    if expresion and not expresion.startswith("Auto"):
        directrices_usuario.append(f"- EXPRESIÓN Y ACTITUD DEL SUJETO: {expresion}")
    if distribucion_figuras and not distribucion_figuras.startswith("Auto"):
        directrices_usuario.append(f"- COMPOSICIÓN Y DISTRIBUCIÓN: {distribucion_figuras}")
    if tecnica_artistica and not tecnica_artistica.startswith("Auto"):
        directrices_usuario.append(f"- TÉCNICA ARTÍSTICA OBLIGATORIA: {tecnica_artistica}")
    if acabado_textil and not acabado_textil.startswith("Auto"):
        directrices_usuario.append(f"- ACABADO / TEXTURA DE ESTAMPADO SERIGRÁFICO: {acabado_textil}")
    if bordes and not bordes.startswith("Auto"):
        directrices_usuario.append(f"- INTEGRACIÓN DE BORDES: {bordes}")
    if iluminacion and not iluminacion.startswith("Auto"):
        directrices_usuario.append(f"- ILUMINACIÓN Y ATMÓSFERA: {iluminacion}")
    if acentos_graficos:
        directrices_usuario.append(f"- ACENTOS Y ELEMENTOS GRÁFICOS A INCLUIR: {', '.join(acentos_graficos)}")
    if instrucciones_extra and instrucciones_extra.strip():
        directrices_usuario.append(f"- INSTRUCCIONES ESPECÍFICAS ADICIONALES: {instrucciones_extra.strip()}")

    if "total" in nivel_estilizacion.lower():
        directrices_usuario.append(
            "- DIRECTIVA DE ESTILIZACIÓN (TRANSFORMACIÓN ARTÍSTICA TOTAL): Dibuja al sujeto completamente desde cero "
            "utilizando la misma técnica manual, trazo, entintado y pigmento del ilustrador de la imagen de referencia. "
            "PROHIBIDO incrustar o recortar una foto realista del sujeto sobre el diseño: el sujeto debe ser "
            "reilustrado íntegramente en la técnica visual observada para que luzca 100% nativo al arte."
        )
    elif "fisonom" in nivel_estilizacion.lower():
        directrices_usuario.append(
            "- DIRECTIVA DE ESTILIZACIÓN (CONSERVAR FISONOMÍA): Mantén con alto realismo y fidelidad fotográfica "
            "los rasgos faciales y proporciones originales del sujeto, integrando el estilo visual, "
            "la paleta, la tipografía y la diagramación del diseño a su alrededor."
        )
    else:
        directrices_usuario.append(
            "- DIRECTIVA DE ESTILIZACIÓN (EQUILIBRADO): Logra una armonía perfecta entre la reconocibilidad "
            "fotográfica del sujeto y la técnica artística de la referencia, adaptando texturas y luces."
        )

    bloque_directrices_usuario = ""
    if directrices_usuario:
        bloque_directrices_usuario = (
            "\n0. PREFERENCIAS Y REQUERIMIENTOS PRIORITARIOS DEFINIDOS POR EL USUARIO:\n"
            + "\n".join(directrices_usuario) + "\n"
        )

    bloque_negativos = ""
    if elementos_a_evitar and elementos_a_evitar.strip():
        bloque_negativos = (
            f"\n* ELEMENTOS ESTRICTAMENTE PROHIBIDOS / NEGATIVOS (QUÉ EVITAR A TODA COSTA): {elementos_a_evitar.strip()}\n"
        )

    tecnica_dominante = (
        tecnica_artistica 
        if (tecnica_artistica and not tecnica_artistica.startswith("Auto")) 
        else analisis_ref.get('medio_y_tecnica', 'Ilustración gráfica profesional')
    )
    efectos_graficos_texto = analisis_ref.get('estilo_grafico_detalles', 'Detalles de serigrafía clásica')
    if acabado_textil and not acabado_textil.startswith("Auto"):
        efectos_graficos_texto += f". Acabado textil: {acabado_textil}"
    if bordes and not bordes.startswith("Auto"):
        efectos_graficos_texto += f". Tratamiento de bordes: {bordes}"

    atmosfera_texto = analisis_ref.get('tono_y_saturacion', 'Equilibrada')
    if iluminacion and not iluminacion.startswith("Auto"):
        atmosfera_texto += f". Iluminación: {iluminacion}"

    prompt_final = f"""
[ESPECIFICACIÓN MAESTRA DE DISEÑO GRÁFICO PARA POLERA / T-SHIRT ARTWORK]
{bloque_directrices_usuario}
1. SUJETO ÚNICO Y EXCLUSIVO DE TODA LA ILUSTRACIÓN:
- Sujeto objetivo a ilustrar: {sujeto_nombre}
- Características visuales clave de {sujeto_nombre}: {sujeto_desc}
* REGLA ABSOLUTA DE CONTENIDO: Cada una de las ilustraciones en esta pieza debe representar EXCLUSIVAMENTE a {sujeto_nombre}, basándose fielmente en las fotos adjuntas.
* PROHIBICIÓN ESTRICTA: PROHIBIDO dibujar cualquier elemento o tema ajeno a {sujeto_nombre}. PROHIBIDO dibujar fósiles, huesos, dinosaurios, personas o elementos de la imagen de referencia. La referencia es SOLO una plantilla de diagramación; el contenido visual entero es 100% {sujeto_nombre}.
{bloque_negativos}
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

4. TÉCNICA ARTÍSTICA Y MICRO-ESTILO (IMITACIÓN FORENSE DE LA REFERENCIA):
- Técnica visual dominante: {tecnica_dominante} aplicada a {sujeto_nombre}
- Trazo y contorno: {analisis_ref.get('trazo_y_linea', 'Línea nítida y definida')}
- Método de sombreado: {analisis_ref.get('metodo_sombreado', 'Sombreado gráfico contrastado')}
- Tratamiento de color y tintas: {analisis_ref.get('tratamiento_tintas', 'Tintas serigráficas equilibradas')}
- Textura y acabado de superficie: {efectos_graficos_texto}. {analisis_ref.get('textura_y_sustrato', '')}
- Composición y layout: {analisis_ref.get('composicion_general', 'Composición equilibrada')}
* PROHIBICIÓN ESTRICTA DE LOOK DIGITAL GENÉRICO: PROHIBIDO generar acabados tipo render 3D glossy, sombreados CGI sintéticos, plásticos lisos, figuras de cera o vectores corporativos genéricos. La pieza debe emular fielmente la técnica artística tradicional/textil de la referencia.
* REGLA DE COHERENCIA: Aplica esta MISMA técnica artística de forma uniforme e impecable a todas las ilustraciones de {sujeto_nombre} y a los textos.

5. COLOR, TONO Y FONDO:
- {instruccion_colores}
- Tono e intensidad cromática: Saturación media {perfil_color['saturacion_promedio_pct']}% ({perfil_color['categoria_tono']}), atmósfera: {atmosfera_texto}.
- Fondo: {instruccion_fondo}
- RESTRICCIÓN DE SALIDA: Genera EXCLUSIVAMENTE el archivo de arte gráfico 2D plano, de frente, ocupando el lienzo completo. PROHIBIDO generar mockups de poleras, personas vistiendo ropa, pliegues de tela o fondos de estudio.
"""

    ratio_str = "3:4"
    for r in ["1:1", "9:16", "4:3", "3:4"]:
        if proporcion_estampado.startswith(r):
            ratio_str = r
            break

    ancho_px, alto_px = DIMENSIONES_POR_PROPORCION.get(ratio_str, (ANCHO_FINAL_PX, ALTO_FINAL_PX))

    # Optimización de referencias para la llamada multimodal:
    # Se toman máximo las 2 referencias principales a 1024px para evitar desbordar tokens de entrada en el modelo de imagen
    imagenes_ref_optimizadas = [_redimensionar_si_es_muy_grande(img, 1024) for img in imagenes_referencia[:2]]

    contenidos_multimodales = [
        "### [REFERENCIA(S) VISUAL(ES) DE ESTILO, TÉCNICA Y DIAGRAMACIÓN]:\n"
        "Inspecciona con extrema atención la técnica de entintado, el grosor de línea, el sombreado y la textura gráfica de estas imágenes de polera de referencia. "
        "Debes transferir y replicar exactamente este mismo estilo artístico sobre el sujeto objetivo:",
        *imagenes_ref_optimizadas,
        "\n### [IMAGEN(ES) DEL SUJETO / PERSONAJE OBJETIVO]:\n"
        "Este es el sujeto objetivo que debes ilustrar adoptando por completo el estilo y técnica de las referencias anteriores:",
        *imagenes_personaje_final,
        f"\n### [ESPECIFICACIÓN MAESTRA DE DISEÑO GRÁFICO]:\n{prompt_final}",
    ]

    _notificar_progreso(
        callback_progreso, 5, total_etapas, "Generación con IA",
        f"Esperando respuesta del modelo de imagen '{modelo_imagen}' ({ratio_str})...",
        "running",
    )

    t0_imagen = time.time()
    try:
        respuesta_imagen = client.models.generate_content(
            model=modelo_imagen,
            contents=contenidos_multimodales,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=ratio_str,
                ),
            ),
        )
    except Exception as e:
        diag, sug = diagnosticar_error_api(e)
        raise ErrorProcesoPolera(
            fase="Generación con IA (Modelo de Imagen)",
            mensaje=f"Error en la llamada a la API de Imagen: {e}",
            diagnostico=diag,
            sugerencia=sug,
            error_original=e,
            tokens_gastados=registro_tokens,
        )

    # Registro de tokens de imagen si están disponibles
    if hasattr(respuesta_imagen, "usage_metadata") and respuesta_imagen.usage_metadata:
        registro_tokens["generacion_imagen"]["prompt"] = getattr(respuesta_imagen.usage_metadata, "prompt_token_count", 0) or 0
        registro_tokens["generacion_imagen"]["candidates"] = getattr(respuesta_imagen.usage_metadata, "candidates_token_count", 0) or 0
        registro_tokens["generacion_imagen"]["total"] = getattr(respuesta_imagen.usage_metadata, "total_token_count", 0) or 0

    # Inspección forense de la respuesta (seguridad, rechazos o falta de imagen)
    if hasattr(respuesta_imagen, "prompt_feedback") and respuesta_imagen.prompt_feedback:
        fb = respuesta_imagen.prompt_feedback
        if getattr(fb, "block_reason", None):
            raise ErrorProcesoPolera(
                fase="Generación con IA (Modelo de Imagen)",
                mensaje=f"Google bloqueó la solicitud por política de contenido: {fb.block_reason}",
                diagnostico="El prompt o alguna de las fotos de entrada activó un filtro de seguridad antes de procesar.",
                sugerencia="Modifica el título, evita mencionar figuras públicas o nombres de marcas comerciales.",
                detalle_tecnico=str(fb),
                tokens_gastados=registro_tokens,
            )

    candidatos = getattr(respuesta_imagen, "candidates", None)
    if not candidatos or len(candidatos) == 0:
        raise ErrorProcesoPolera(
            fase="Generación con IA (Modelo de Imagen)",
            mensaje="Gemini no devolvió ningún candidato de respuesta.",
            diagnostico="La API de Google terminó la petición sin devolver candidatos de imagen.",
            sugerencia="Verifica tu cuota de facturación en Google AI Studio o prueba seleccionando el modelo 'Lite'.",
            detalle_tecnico=str(respuesta_imagen),
            tokens_gastados=registro_tokens,
        )

    candidato = candidatos[0]
    finish_reason = getattr(candidato, "finish_reason", None)
    finish_reason_str = str(finish_reason).upper() if finish_reason else ""

    if finish_reason and "STOP" not in finish_reason_str:
        diagnostico = f"La generación se detuvo por el motivo: {finish_reason}"
        sugerencia = "Modifica los textos o fotos para evitar bloqueos por políticas de IA."
        if "SAFETY" in finish_reason_str:
            ratings = getattr(candidato, "safety_ratings", [])
            ratings_str = ", ".join([f"{r.category}: {r.probability}" for r in ratings if getattr(r, 'probability', None)])
            diagnostico = f"Bloqueado por filtros de seguridad de Gemini ({ratings_str or 'SAFETY'})."
            sugerencia = "La IA detectó contenido sensible, posibles derechos de autor o parecido a personas protegidas. Prueba cambiando el título o usando otra imagen del sujeto."
        elif "RECITATION" in finish_reason_str:
            diagnostico = "Bloqueado por posible reproducción no autorizada de material protegido (RECITATION)."
            sugerencia = "Evita textos o marcas registradas en el título o frases secundarias."

        raise ErrorProcesoPolera(
            fase="Generación con IA (Modelo de Imagen)",
            mensaje=f"Generación rechazada por la IA (Motivo: {finish_reason})",
            diagnostico=diagnostico,
            sugerencia=sugerencia,
            detalle_tecnico=f"finish_reason={finish_reason}, safety_ratings={getattr(candidato, 'safety_ratings', 'N/A')}",
            tokens_gastados=registro_tokens,
        )

    # Extraer la imagen de las partes
    imagen_base = None
    texto_respuesta = ""
    if hasattr(candidato, "content") and candidato.content and hasattr(candidato.content, "parts"):
        for parte in candidato.content.parts:
            if getattr(parte, "inline_data", None) is not None:
                imagen_base = Image.open(io.BytesIO(parte.inline_data.data))
                break
            elif getattr(parte, "text", None):
                texto_respuesta += parte.text

    if imagen_base is None:
        raise ErrorProcesoPolera(
            fase="Generación con IA (Modelo de Imagen)",
            mensaje="Gemini no devolvió datos de imagen en su respuesta.",
            diagnostico=f"El modelo respondió solo con texto: '{texto_respuesta}'" if texto_respuesta else "No se recibieron bytes de imagen válidos.",
            sugerencia="Prueba con otra proporción o revisa si el modelo seleccionado está disponible.",
            detalle_tecnico=str(candidato),
            tokens_gastados=registro_tokens,
        )

    dt_img = round(time.time() - t0_imagen, 1)
    _notificar_progreso(callback_progreso, 5, total_etapas, "Generación con IA", f"Imagen generada exitosamente en {dt_img}s.", "success")

    # =========================================================================
    # ETAPA 6: Postprocesamiento y Acabado Chroma
    # =========================================================================
    _notificar_progreso(callback_progreso, 6, total_etapas, "Postprocesamiento", "Aplicando recorte de fondo y reescalando a resolución final...", "running")
    
    t0_post = time.time()
    try:
        if color_fondo_solido:
            imagen_resultado = imagen_base
            _notificar_progreso(callback_progreso, 6, total_etapas, "Postprocesamiento", "Fondo sólido conservado.", "info")
        else:
            imagen_resultado = quitar_fondo_por_chroma(imagen_base, COLOR_CHROMA_DEFECTO)
            _notificar_progreso(callback_progreso, 6, total_etapas, "Postprocesamiento", "Fondo transparente aplicado vía chroma key.", "info")

        imagen_resultado = imagen_resultado.resize((ancho_px, alto_px), Image.LANCZOS)
    except Exception as e:
        raise ErrorProcesoPolera(
            fase="Postprocesamiento y Recorte",
            mensaje=f"Error al procesar la imagen generada: {e}",
            diagnostico="Fallo al aplicar la transparencia o redimensionar la imagen final.",
            sugerencia="Revisa los recursos de memoria del servidor.",
            error_original=e,
            tokens_gastados=registro_tokens,
        )

    # Calcular totales de tokens
    total_tokens = sum(
        registro_tokens[k].get("total", 0) for k in ["analisis_sujeto", "analisis_referencias", "generacion_imagen"]
    )
    registro_tokens["total_tokens"] = total_tokens
    tiempo_total = round(time.time() - tiempo_inicio_global, 1)

    # Adjuntar metadatos al resultado para que la interfaz los consuma
    imagen_resultado.info["prompt_final"] = prompt_final
    imagen_resultado.info["registro_tokens"] = registro_tokens
    imagen_resultado.info["tiempo_total_segundos"] = tiempo_total
    imagen_resultado.info["cache_generada"] = {
        "analisis_sujeto": analisis_sujeto,
        "analisis_ref": analisis_ref,
        "perfil_color": perfil_color,
    }

    _notificar_progreso(
        callback_progreso, 6, total_etapas, "Postprocesamiento",
        f"¡Diseño completado en {tiempo_total}s! (Tokens totales: {total_tokens:,})",
        "success",
        detalle={"tokens": registro_tokens, "tiempo": tiempo_total},
    )

    return imagen_resultado


if __name__ == "__main__":
    if not os.path.exists("referencia.jpg") or not os.path.exists("personaje.jpg"):
        print("\n[INFO] No se encontraron los archivos 'referencia.jpg' y/o 'personaje.jpg' en la carpeta.")
        print("       Si prefieres usar la interfaz visual (recomendado), ejecuta:")
        print("       python -m streamlit run app_visual.py\n")
        sys.exit(0)

    referencias = [Image.open("referencia.jpg")]
    personajes = [Image.open("personaje.jpg")]

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
