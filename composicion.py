"""
Mesa de composición: modelo de capas, imagen guía y texto local.

Una "capa" es un dict con coordenadas normalizadas (0-1) respecto al lienzo:

    {"id": "img_1", "tipo": "imagen", "idx": 0,        # idx = imagen del sujeto usada
     "cx": 0.5, "cy": 0.5, "w": 0.3, "h": 0.3, "rot": 0}
    {"id": "titulo", "tipo": "texto", "rol": "titulo", "texto": "MI GRUPO",
     "cx": 0.5, "cy": 0.08, "w": 0.8, "h": 0.1, "rot": 0,
     "color": "#FFFFFF", "contorno": None}

(cx, cy) es el centro de la capa; w y h son fracción del ancho y del alto del lienzo;
rot son grados en sentido horario (igual que el lienzo interactivo).

Este módulo no depende de generador.py ni de Streamlit, para poder probarse solo.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

LADO_GUIA_PX = 1024  # lado mayor de la imagen guía enviada al modelo (barata en tokens)
MAX_ILUSTRACIONES = 12

PROMPT_DETECCION_CAJAS = """
Analiza el diseño gráfico de esta polera y localiza su estructura de composición.
Devuelve EXCLUSIVAMENTE un JSON: una lista de objetos con esta forma:
[{"tipo": "ilustracion" | "titulo" | "texto" | "rotulo", "etiqueta": "<2-4 palabras>", "box_2d": [ymin, xmin, ymax, xmax]}]

Reglas:
- Coordenadas normalizadas de 0 a 1000 respecto a la imagen completa (origen arriba-izquierda).
- "ilustracion": cada figura, retrato o dibujo individual (no agrupes varios en una sola caja).
- "titulo": el texto principal más grande (como máximo uno).
- "texto": frases o bloques de texto secundarios medianos o grandes.
- "rotulo": etiquetas diminutas bajo cada ilustración (puedes omitirlas).
- Ignora fondos, marcos y adornos. Máximo 20 objetos.
""".strip()


# ---------------------------------------------------------------------------
# Utilidades de geometría
# ---------------------------------------------------------------------------

def dimensiones_lienzo(ratio_w: int, ratio_h: int, lado_mayor: int = LADO_GUIA_PX) -> tuple[int, int]:
    """Tamaño en píxeles de un lienzo con la proporción dada, con el lado mayor = lado_mayor."""
    if ratio_w >= ratio_h:
        return lado_mayor, max(1, round(lado_mayor * ratio_h / ratio_w))
    return max(1, round(lado_mayor * ratio_w / ratio_h)), lado_mayor


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _ajustar_imagen_en_caja(
    aspecto_img: float, caja_w: float, caja_h: float, lienzo_w: int, lienzo_h: int
) -> tuple[float, float]:
    """Devuelve (w, h) normalizados de una imagen con ese aspecto (ancho/alto) contenida en la caja."""
    caja_ar_px = (caja_w * lienzo_w) / max(caja_h * lienzo_h, 1e-6)
    if aspecto_img > caja_ar_px:
        w = caja_w
        h = w * lienzo_w / aspecto_img / lienzo_h
    else:
        h = caja_h
        w = h * lienzo_h * aspecto_img / lienzo_w
    return w, h


# ---------------------------------------------------------------------------
# Detección de cajas en la referencia (parseo de la respuesta de Gemini)
# ---------------------------------------------------------------------------

def parsear_cajas(texto: str) -> list[dict]:
    """
    Convierte la respuesta JSON de visión en una lista de cajas normalizadas:
    [{"tipo", "etiqueta", "cx", "cy", "w", "h"}]. Tolerante a basura: descarta lo inválido.
    """
    limpio = (texto or "").strip()
    if "```" in limpio:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", limpio)
        if m:
            limpio = m.group(1).strip()
    try:
        data = json.loads(limpio)
    except Exception:
        return []
    if isinstance(data, dict):
        for k in ("elementos", "objetos", "cajas", "items"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
    if not isinstance(data, list):
        return []

    cajas = []
    for item in data:
        if not isinstance(item, dict):
            continue
        box = item.get("box_2d") or item.get("box")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            continue
        try:
            ymin, xmin, ymax, xmax = (float(v) / 1000.0 for v in box)
        except (TypeError, ValueError):
            continue
        ymin, ymax = _clamp(min(ymin, ymax)), _clamp(max(ymin, ymax))
        xmin, xmax = _clamp(min(xmin, xmax)), _clamp(max(xmin, xmax))
        if (xmax - xmin) < 0.02 or (ymax - ymin) < 0.01:
            continue
        tipo_raw = str(item.get("tipo", "")).lower()
        if "titul" in tipo_raw:
            tipo = "titulo"
        elif "rotul" in tipo_raw or "etiq" in tipo_raw:
            tipo = "rotulo"
        elif "text" in tipo_raw or "frase" in tipo_raw:
            tipo = "texto"
        else:
            tipo = "ilustracion"
        cajas.append({
            "tipo": tipo,
            "etiqueta": str(item.get("etiqueta", ""))[:40],
            "cx": (xmin + xmax) / 2,
            "cy": (ymin + ymax) / 2,
            "w": xmax - xmin,
            "h": ymax - ymin,
        })
    return cajas


def _orden_lectura(cajas: list[dict]) -> list[dict]:
    """Ordena arriba→abajo, izquierda→derecha agrupando por filas aproximadas."""
    if not cajas:
        return []
    alto_fila = max(0.04, sorted(c["h"] for c in cajas)[len(cajas) // 2] * 0.5)
    return sorted(cajas, key=lambda c: (round(c["cy"] / alto_fila), c["cx"]))


# ---------------------------------------------------------------------------
# Construcción del layout inicial
# ---------------------------------------------------------------------------

def _capas_texto(titulo: str, frases: list[str], cajas: list[dict], color_texto: str = "#FFFFFF") -> list[dict]:
    capas = []
    cajas_titulo = [c for c in cajas if c["tipo"] == "titulo"]
    cajas_texto = _orden_lectura([c for c in cajas if c["tipo"] == "texto"])

    if titulo.strip():
        if cajas_titulo:
            c = max(cajas_titulo, key=lambda c: c["w"] * c["h"])
            geom = {k: c[k] for k in ("cx", "cy", "w", "h")}
        else:
            geom = {"cx": 0.5, "cy": 0.08, "w": 0.8, "h": 0.10}
        capas.append({"id": "titulo", "tipo": "texto", "rol": "titulo", "texto": titulo.strip(),
                      "rot": 0, "color": color_texto, "contorno": None, **geom})

    for i, frase in enumerate(frases):
        if i < len(cajas_texto):
            c = cajas_texto[i]
            geom = {k: c[k] for k in ("cx", "cy", "w", "h")}
        else:
            # Frases sin caja detectada: se apilan hacia arriba desde el borde inferior,
            # esquivando las cajas ya ocupadas por frases detectadas.
            tope_ocupado = min([c["cy"] - c["h"] / 2 for c in cajas_texto] + [0.99])
            j = i - len(cajas_texto)
            geom = {"cx": 0.5, "cy": tope_ocupado - 0.03 - 0.055 * j, "w": 0.6, "h": 0.045}
        capas.append({"id": f"frase_{i + 1}", "tipo": "texto", "rol": "frase", "texto": frase,
                      "rot": 0, "color": color_texto, "contorno": None, **geom})
    return capas


def layout_inicial(
    aspectos_imagenes: list[float],
    titulo: str,
    frases: list[str],
    ratio_wh: tuple[int, int] = (3, 4),
    cajas: list[dict] | None = None,
    color_texto: str = "#FFFFFF",
) -> list[dict]:
    """
    Layout de partida. Si hay `cajas` (detectadas en la referencia) las ilustraciones y textos
    se ubican donde está la referencia; si no, se reparte una grilla simple.
    aspectos_imagenes: ancho/alto de cada imagen del sujeto (ya recortada).
    """
    cajas = cajas or []
    lw, lh = dimensiones_lienzo(*ratio_wh)
    n = max(1, len(aspectos_imagenes))
    aspectos = aspectos_imagenes or [1.0]

    ilus = _orden_lectura([c for c in cajas if c["tipo"] == "ilustracion"])[:MAX_ILUSTRACIONES]
    if not ilus:
        # Grilla por defecto en la zona entre el título y las frases
        top, bottom = (0.17 if titulo.strip() else 0.04), (0.88 if frases else 0.96)
        cols = math.ceil(math.sqrt(n))
        filas = math.ceil(n / cols)
        for k in range(n):
            f, c = divmod(k, cols)
            en_fila = min(cols, n - f * cols)
            ilus.append({
                "cx": (c + 0.5) / en_fila,
                "cy": top + (bottom - top) * (f + 0.5) / filas,
                "w": min(0.9, 0.9 / cols),
                "h": (bottom - top) / filas * 0.95,
            })
        if n == 1:
            ilus[0].update({"cx": 0.5, "w": 0.8})

    capas = []
    for k, c in enumerate(ilus):
        idx = k % n
        w, h = _ajustar_imagen_en_caja(aspectos[idx], c["w"], c["h"], lw, lh)
        capas.append({"id": f"img_{k + 1}", "tipo": "imagen", "idx": idx,
                      "cx": c["cx"], "cy": c["cy"], "w": w, "h": h, "rot": 0})
    # Toda imagen subida debe tener al menos una capa, aunque la referencia tenga menos
    # ilustraciones que imágenes del sujeto (si no, quedarían inalcanzables en la mesa).
    usadas = {c["idx"] for c in capas}
    for idx in range(n):
        if idx not in usadas:
            capas.append(capa_imagen_extra(capas, idx, aspectos[idx], ratio_wh))
    capas.extend(_capas_texto(titulo, frases, cajas, color_texto))
    return capas


def capa_imagen_extra(capas: list[dict], idx: int, aspecto: float, ratio_wh: tuple[int, int] = (3, 4)) -> dict:
    """Capa nueva para la imagen `idx`, en una franja inferior escalonada para no tapar a otras."""
    lw, lh = dimensiones_lienzo(*ratio_wh)
    n_img = sum(1 for c in capas if c["tipo"] == "imagen")
    w, h = _ajustar_imagen_en_caja(aspecto, 0.26, 0.26, lw, lh)
    return {"id": f"img_extra_{idx + 1}_{n_img}", "tipo": "imagen", "idx": idx,
            "cx": 0.18 + 0.21 * (n_img % 4), "cy": 0.72 + 0.07 * ((n_img // 4) % 2),
            "w": w, "h": h, "rot": 0}


def sincronizar_textos(capas: list[dict], titulo: str, frases: list[str], color_texto: str = "#FFFFFF") -> list[dict]:
    """
    Mantiene las capas de imagen tal cual y hace coincidir las capas de texto con el título/frases
    actuales (conserva posición y color de las que ya existían).
    """
    previas = {c["id"]: c for c in capas if c["tipo"] == "texto"}
    nuevas = _capas_texto(titulo, frases, [], color_texto)
    for c in nuevas:
        if c["id"] in previas:
            p = previas[c["id"]]
            for k in ("cx", "cy", "w", "h", "rot", "color", "contorno"):
                c[k] = p.get(k, c[k])
    imagenes = [c for c in capas if c["tipo"] == "imagen"]
    return imagenes + nuevas


# ---------------------------------------------------------------------------
# Texto: fuentes, ajuste a caja y render
# ---------------------------------------------------------------------------

_DIR_PROYECTO = Path(__file__).resolve().parent
_DIRS_FUENTES = [
    _DIR_PROYECTO / "fuentes",
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts"),
    Path("/Library/Fonts"),
    Path.home() / ".fonts",
]
_PREFERIDAS = ["impact", "arialbd", "georgiab", "timesbd", "bahnschrift", "segoeuib", "verdanab"]


def listar_fuentes() -> dict[str, str]:
    """{nombre legible: ruta} de fuentes TTF/OTF disponibles (carpeta fuentes/ del proyecto primero)."""
    encontradas: dict[str, str] = {}
    for d in _DIRS_FUENTES:
        try:
            if not d.exists():
                continue
            for p in d.rglob("*"):
                if p.suffix.lower() in (".ttf", ".otf") and p.stem not in encontradas:
                    encontradas[p.stem] = str(p)
                if len(encontradas) > 600:
                    break
        except OSError:
            continue

    def clave(nombre: str):
        en_proyecto = Path(encontradas[nombre]).is_relative_to(_DIRS_FUENTES[0])
        pref = _PREFERIDAS.index(nombre.lower()) if nombre.lower() in _PREFERIDAS else 99
        return (not en_proyecto, pref, nombre.lower())

    return {n: encontradas[n] for n in sorted(encontradas, key=clave)}


def cargar_fuente(ruta: str | None, tam: int) -> ImageFont.FreeTypeFont:
    tam = max(6, int(tam))
    if ruta:
        try:
            return ImageFont.truetype(ruta, tam)
        except OSError:
            pass
    return ImageFont.load_default(size=tam)


def _envolver(texto: str, fuente, ancho_max: float) -> list[str] | None:
    """Parte el texto en líneas que quepan en ancho_max. None si una palabra sola no cabe."""
    lineas, actual = [], ""
    for palabra in texto.split():
        prueba = f"{actual} {palabra}".strip()
        if fuente.getlength(prueba) <= ancho_max:
            actual = prueba
        else:
            if not actual or fuente.getlength(palabra) > ancho_max:
                return None
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


def ajustar_texto(texto: str, ancho: int, alto: int, ruta_fuente: str | None, max_lineas: int = 3, margen_trazo: float = 0.0):
    """Busca el mayor tamaño de fuente con el que el texto cabe en la caja. Devuelve (fuente, lineas)."""
    texto = " ".join(texto.split()) or " "
    lo, hi, mejor = 6, max(8, int(alto)), None
    while lo <= hi:
        medio = (lo + hi) // 2
        fuente = cargar_fuente(ruta_fuente, medio)
        sw = medio * margen_trazo
        lineas = _envolver(texto, fuente, ancho - 2 * sw)
        if lineas and len(lineas) <= max_lineas:
            bbox = fuente.getbbox("Hg")
            alto_linea = (bbox[3] - bbox[1]) * 1.18
            if alto_linea * len(lineas) + 2 * sw <= alto:
                mejor = (fuente, lineas)
                lo = medio + 1
                continue
        hi = medio - 1
    if mejor is None:
        fuente = cargar_fuente(ruta_fuente, 6)
        mejor = (fuente, [texto])
    return mejor


def renderizar_texto_capa(
    texto: str, ancho: int, alto: int, ruta_fuente: str | None,
    color: str = "#FFFFFF", contorno: str | None = None,
) -> Image.Image:
    """RGBA transparente de ancho×alto con el texto centrado y ajustado a la caja."""
    ancho, alto = max(4, int(ancho)), max(4, int(alto))
    tile = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    margen = 0.07 if contorno else 0.0
    fuente, lineas = ajustar_texto(texto, ancho, alto, ruta_fuente, margen_trazo=margen)
    sw = int(round(fuente.size * margen)) if contorno else 0
    bbox = fuente.getbbox("Hg")
    alto_linea = (bbox[3] - bbox[1]) * 1.18
    y = (alto - alto_linea * len(lineas)) / 2
    dibujo = ImageDraw.Draw(tile)
    for linea in lineas:
        x = (ancho - fuente.getlength(linea)) / 2
        dibujo.text((x, y), linea, font=fuente, fill=color,
                    stroke_width=sw, stroke_fill=contorno if contorno else None)
        y += alto_linea
    return tile


def _pegar_capa_rotada(destino: Image.Image, tile: Image.Image, cx_px: float, cy_px: float, rot: float):
    """Pega `tile` (RGBA) centrado en (cx_px, cy_px) rotado `rot`° horario sobre `destino` RGBA (in place)."""
    if rot:
        tile = tile.rotate(-rot, expand=True, resample=Image.BICUBIC)
    capa = Image.new("RGBA", destino.size, (0, 0, 0, 0))
    capa.paste(tile, (round(cx_px - tile.width / 2), round(cy_px - tile.height / 2)))
    destino.alpha_composite(capa)


def aplicar_textos_locales(
    imagen: Image.Image, capas: list[dict], fuente_titulo: str | None, fuente_frases: str | None
) -> Image.Image:
    """Dibuja título y frases con fuentes reales sobre la imagen final (conserva transparencia)."""
    resultado = imagen.convert("RGBA")
    W, H = resultado.size
    for capa in capas:
        if capa.get("tipo") != "texto" or not str(capa.get("texto", "")).strip():
            continue
        ruta = fuente_titulo if capa.get("rol") == "titulo" else fuente_frases
        tile = renderizar_texto_capa(
            capa["texto"], capa["w"] * W, capa["h"] * H, ruta,
            color=capa.get("color") or "#FFFFFF", contorno=capa.get("contorno") or None,
        )
        _pegar_capa_rotada(resultado, tile, capa["cx"] * W, capa["cy"] * H, capa.get("rot", 0))
    resultado.info.update(imagen.info)
    return resultado


# ---------------------------------------------------------------------------
# Imagen guía para el modelo
# ---------------------------------------------------------------------------

def _color_a_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def componer_guia(
    capas: list[dict],
    recortes: list[Image.Image],
    ratio_wh: tuple[int, int],
    color_fondo: str,
    incluir_texto: bool,
) -> Image.Image:
    """
    Boceto de la composición: fondo plano + recortes del sujeto en su posición/escala reales.
    Si incluir_texto, el título y las frases se dibujan (con caja tenue) en su sitio.
    Respeta el orden de `capas` como orden de apilado (la primera queda al fondo).
    """
    W, H = dimensiones_lienzo(*ratio_wh)
    guia = Image.new("RGBA", (W, H), _color_a_rgb(color_fondo) + (255,))
    for capa in capas:
        rot = capa.get("rot", 0)
        if capa["tipo"] == "imagen":
            if not recortes:
                continue
            src = recortes[capa.get("idx", 0) % len(recortes)].convert("RGBA")
            tile = src.resize((max(2, round(capa["w"] * W)), max(2, round(capa["h"] * H))), Image.LANCZOS)
            _pegar_capa_rotada(guia, tile, capa["cx"] * W, capa["cy"] * H, rot)
        elif incluir_texto and str(capa.get("texto", "")).strip():
            ancho, alto = max(4, round(capa["w"] * W)), max(4, round(capa["h"] * H))
            color = capa.get("color") or "#FFFFFF"
            tile = renderizar_texto_capa(capa["texto"], ancho, alto, None, color=color, contorno=capa.get("contorno"))
            borde = ImageDraw.Draw(tile)
            borde.rectangle([0, 0, ancho - 1, alto - 1], outline=_color_a_rgb(color) + (90,), width=1)
            _pegar_capa_rotada(guia, tile, capa["cx"] * W, capa["cy"] * H, rot)
    return guia.convert("RGB")


def _zona(cx: float, cy: float) -> str:
    v = "arriba" if cy < 0.34 else ("abajo" if cy > 0.66 else "centro")
    h = "izquierda" if cx < 0.34 else ("derecha" if cx > 0.66 else "centro")
    return "centro" if v == h == "centro" else (v if h == "centro" else (h if v == "centro" else f"{v}-{h}"))


def describir_layout(capas: list[dict], texto_local: bool) -> str:
    """Texto con coordenadas explícitas (en % del lienzo) para el prompt del modelo de imagen."""
    lineas = []
    n_img = 0
    for capa in capas:
        pos = f"centro en (x={capa['cx'] * 100:.0f}%, y={capa['cy'] * 100:.0f}%), ocupa {capa['w'] * 100:.0f}% de ancho × {capa['h'] * 100:.0f}% de alto"
        rot = f", rotada {capa['rot']:.0f}°" if abs(capa.get("rot", 0)) > 1 else ""
        zona = _zona(capa["cx"], capa["cy"])
        if capa["tipo"] == "imagen":
            n_img += 1
            lineas.append(f"- Ilustración {n_img} (imagen del sujeto #{capa.get('idx', 0) + 1} de la guía): {pos}{rot}. Zona: {zona}.")
        elif texto_local:
            lineas.append(f"- ZONA LIBRE DE TEXTO ({'título' if capa.get('rol') == 'titulo' else 'frase'}): {pos}{rot}. Zona: {zona}. Déjala sin letras.")
        else:
            rol = "Título" if capa.get("rol") == "titulo" else "Frase"
            lineas.append(f"- {rol} «{capa['texto']}»: {pos}{rot}. Zona: {zona}.")
    return "\n".join(lineas)


# ---------------------------------------------------------------------------
# Impresión
# ---------------------------------------------------------------------------

def preparar_para_impresion(imagen: Image.Image, factor: float = 2.0) -> Image.Image:
    """
    Amplía con remuestreo Lanczos + enfoque suave. NO crea detalle nuevo: sirve para llegar al
    tamaño de archivo que piden las plantillas DTG; la nitidez real depende de la imagen generada.
    """
    rgba = imagen.convert("RGBA")
    nuevo = (round(rgba.width * factor), round(rgba.height * factor))
    grande = rgba.resize(nuevo, Image.LANCZOS)
    rgb = grande.convert("RGB").filter(ImageFilter.UnsharpMask(radius=1.6, percent=60, threshold=2))
    rgb.putalpha(grande.getchannel("A"))
    return rgb
