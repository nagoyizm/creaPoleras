"""
Interfaz visual para el generador de diseños de poleras con consola en vivo,
diagnóstico forense de errores y optimización de tokens.

Correr con:
    python -m streamlit run app_visual.py
"""

import io
import sys
import time
import json
import base64
import hashlib
import traceback
from datetime import datetime
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image
import pillow_avif  # noqa: F401 -- con solo importarlo, Pillow aprende a abrir archivos .avif

import generador
import composicion
from generador import (
    generar_diseno_desde_imagenes,
    validar_conexion_api,
    detectar_composicion_referencia,
    recortar_sujeto,
    editar_diseno,
    ErrorProcesoPolera,
    MODELOS_IMAGEN,
    MODELO_IMAGEN_POR_DEFECTO,
)

DIR_SALIDAS = Path(__file__).resolve().parent / "salidas"
MAX_HISTORIAL = 8

# Lienzo interactivo (Konva) como componente Streamlit sin build
_mesa_componente = components.declare_component(
    "mesa_composicion", path=str(Path(__file__).resolve().parent / "componente_mesa")
)

st.set_page_config(page_title="Generador de Poleras con IA", layout="centered", page_icon="👕")

TIPOS_IMAGEN_ACEPTADOS = ["jpg", "jpeg", "png", "webp", "avif"]

# Estilos CSS para la consola oscura interactiva
st.markdown(
    """
    <style>
    .terminal-box {
        background-color: #0d1117;
        color: #e6edf3;
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 14px;
        font-family: 'Consolas', 'Courier New', monospace;
        font-size: 13px;
        line-height: 1.5;
        max-height: 320px;
        overflow-y: auto;
        box-shadow: inset 0 0 10px rgba(0, 0, 0, 0.5);
        margin-bottom: 12px;
    }
    .terminal-line { margin: 2px 0; }
    .term-time { color: #8b949e; margin-right: 6px; }
    .term-step { color: #58a6ff; font-weight: bold; margin-right: 6px; }
    .term-msg { color: #c9d1d9; }
    .term-success { color: #3fb950; font-weight: bold; }
    .term-warning { color: #d29922; font-weight: bold; }
    .term-error { color: #f85149; font-weight: bold; }
    .term-info { color: #a371f7; }
    </style>
    """,
    unsafe_allow_html=True,
)


def _calcular_hash_archivos(archivos) -> str:
    """Hash MD5 del CONTENIDO de los archivos (no solo nombre y tamaño) para saber si cambiaron."""
    if not archivos:
        return ""
    h = hashlib.md5()
    for a in archivos:
        h.update(a.getvalue())
        h.update(b"|")
    return h.hexdigest()


def _a_data_url(imagen: Image.Image, lado_max: int = 640, formato: str = "PNG") -> str:
    """Imagen reducida como data URL para enviarla al lienzo interactivo."""
    im = imagen.copy()
    im.thumbnail((lado_max, lado_max), Image.LANCZOS)
    if formato == "JPEG":
        im = im.convert("RGB")
    buf = io.BytesIO()
    im.save(buf, format=formato, **({"quality": 85} if formato == "JPEG" else {}))
    mime = "jpeg" if formato == "JPEG" else "png"
    return f"data:image/{mime};base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _png_bytes(imagen: Image.Image, **kwargs) -> bytes:
    buf = io.BytesIO()
    imagen.save(buf, format="PNG", **kwargs)
    return buf.getvalue()


def _contraste(hex_color: str) -> str:
    """Negro o blanco, el que mejor se lea sobre hex_color."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return "#111111" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#FFFFFF"


def _panel_error(err: ErrorProcesoPolera):
    """Panel de diagnóstico para errores controlados."""
    st.error(f"🛑 Falló en la fase: **{err.fase}**")
    st.markdown(f"### 🚨 {err.mensaje}")
    if err.diagnostico:
        st.warning(f"**🔍 Causa identificada:** {err.diagnostico}")
    if err.sugerencia:
        st.info(f"**💡 ¿Cómo solucionarlo y no perder dinero?**\n\n{err.sugerencia}")
    if err.tokens_gastados:
        tot = err.tokens_gastados.get("total_tokens", 0)
        if tot > 0:
            st.caption(f"🪙 Tokens consumidos antes de la interrupción: {tot:,}")
        else:
            st.caption("⚡ La falla ocurrió antes o durante la petición de imagen, no se gastó cuota adicional.")
    with st.expander("📋 Ver detalle técnico para soporte o depuración"):
        st.code(err.detalle_tecnico or str(err.error_original), language="text")


def _miniatura(imagen: Image.Image, lado: int = 320) -> Image.Image:
    mini = imagen.convert("RGBA")
    mini.thumbnail((lado, lado), Image.LANCZOS)
    fondo = Image.new("RGBA", mini.size, (200, 200, 200, 255))
    fondo.alpha_composite(mini)
    return fondo.convert("RGB")


def _registrar_version(resultado: Image.Image, meta: dict) -> dict:
    """Guarda el resultado en el historial de la sesión y en disco (salidas/). Devuelve la entrada."""
    ahora = datetime.now()
    entrada = {
        "id": ahora.strftime("%Y%m%d_%H%M%S_%f"),
        "hora": ahora.strftime("%H:%M:%S"),
        "png": _png_bytes(resultado),
        "cruda": _png_bytes(resultado.info["imagen_cruda"]) if "imagen_cruda" in resultado.info else None,
        "mini": _png_bytes(_miniatura(resultado)),
        "prompt": resultado.info.get("prompt_final") or meta.get("prompt", ""),
        "tokens": resultado.info.get("registro_tokens", {}),
        "tiempo": resultado.info.get("tiempo_total_segundos", 0),
        "ratio": resultado.info.get("ratio_str", "3:4"),
        **meta,
    }
    historial = st.session_state.setdefault("historial", [])
    historial.insert(0, entrada)
    del historial[MAX_HISTORIAL:]
    try:
        carpeta = DIR_SALIDAS / entrada["id"]
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / "diseno.png").write_bytes(entrada["png"])
        if entrada["cruda"]:
            (carpeta / "cruda.png").write_bytes(entrada["cruda"])
        (carpeta / "prompt.txt").write_text(entrada["prompt"] or "", encoding="utf-8")
        (carpeta / "info.json").write_text(json.dumps({
            "titulo": meta.get("titulo"), "modelo": meta.get("modelo"), "ratio": entrada["ratio"],
            "layout": meta.get("layout_completo"), "nota": meta.get("nota", ""),
            "tokens": entrada["tokens"],
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except OSError:
        pass  # guardar en disco es un extra: nunca debe impedir mostrar el resultado
    st.session_state.ultimo = entrada
    return entrada


def renderizar_consola(placeholder, logs, etapa_actual, total_etapas, estado_final="running"):
    """Dibuja en pantalla la consola de terminal con scroll y eventos en tiempo real."""
    porcentaje = int((etapa_actual / total_etapas) * 100) if total_etapas else 0
    porcentaje = min(100, max(0, porcentaje))

    color_estado = "#3fb950" if estado_final == "success" else ("#f85149" if estado_final == "error" else "#58a6ff")
    texto_estado = "COMPLETADO" if estado_final == "success" else ("ERROR" if estado_final == "error" else "EN EJECUCIÓN")

    html_lines = []
    for log in logs:
        cls_estado = "term-msg"
        if log["estado"] == "success":
            cls_estado = "term-success"
        elif log["estado"] == "warning":
            cls_estado = "term-warning"
        elif log["estado"] == "error":
            cls_estado = "term-error"
        elif log["estado"] == "info":
            cls_estado = "term-info"

        html_lines.append(
            f'<div class="terminal-line">'
            f'<span class="term-time">[{log["timestamp"]}]</span>'
            f'<span class="term-step">[{log["fase"]}]</span>'
            f'<span class="{cls_estado}">{log["mensaje"]}</span>'
            f'</div>'
        )

    contenido_terminal = "\n".join(html_lines)

    with placeholder.container():
        col_t1, col_t2 = st.columns([4, 1])
        with col_t1:
            st.markdown(f"**🖥️ Consola de Ejecución en Vivo** `(Paso {etapa_actual}/{total_etapas} - {porcentaje}%)`")
        with col_t2:
            st.markdown(f"<span style='color:{color_estado}; font-weight:bold; float:right;'>● {texto_estado}</span>", unsafe_allow_html=True)

        st.progress(porcentaje / 100.0)
        st.markdown(f'<div class="terminal-box">{contenido_terminal}</div>', unsafe_allow_html=True)


# --- Barra Lateral: Diagnóstico de Conexión y Control de Tokens ---
with st.sidebar:
    st.header("⚡ Optimización y Estado")
    st.caption("Herramientas para evitar consumo innecesario de tokens y diagnosticar errores.")

    if st.button("🔌 Probar conexión con Google AI Studio", use_container_width=True):
        with st.spinner("Verificando API Key y cuota..."):
            res = validar_conexion_api()
            if res["ok"]:
                st.success("✅ Conexión exitosa: Tu clave API está activa y responde correctamente.")
            else:
                st.error(f"❌ Error de conexión: {res['mensaje']}")
                st.warning(f"💡 {res['diagnostico']}")
                st.info(f"👉 {res['sugerencia']}")

    st.divider()
    st.subheader("🪙 Control de Tokens")
    reutilizar_cache = st.checkbox(
        "Activar caché inteligente de análisis",
        value=True,
        help="Si marcas esto y vuelves a generar (o si falló la imagen), la IA NO volverá a gastar tokens en analizar las mismas fotos.",
    )
    modo_ahorro_sujeto = st.checkbox(
        "Modo económico: Omitir visión si describí el sujeto",
        value=False,
        help="Si escribes claramente qué es tu sujeto, no llamará a la IA de visión para reconocerlo, ahorrando una llamada completa.",
    )

    if "cache_analisis" in st.session_state and st.session_state.cache_analisis:
        st.success("🟢 Datos de análisis guardados en caché.")
        if st.button("🔄 Borrar caché (Forzar nuevo análisis)", use_container_width=True):
            st.session_state.cache_analisis = None
            st.session_state.cache_hash = None
            st.info("Caché limpiada. La próxima generación analizará desde cero.")
            st.rerun()
    else:
        st.caption("ℹ️ No hay análisis en caché aún. Se creará al generar el primer diseño.")


st.title("👕 Generador de diseños de poleras")
st.caption("Sube referencias, tu personaje, elige colores y texto — Gemini arma el diseño con monitoreo en vivo.")

# --- Imagen(es) de referencia ---
st.subheader("1. Imagen(es) de referencia (estructura del diseño)")
archivos_referencia = st.file_uploader(
    "Sube una o más poleras existentes cuyo layout/estructura quieres replicar",
    type=TIPOS_IMAGEN_ACEPTADOS,
    key="referencia",
    accept_multiple_files=True,
)
st.caption("Si subes varias, Gemini combina lo común entre ellas en una sola estructura.")

# --- Imagen(es) del personaje / sujeto ---
st.subheader("2. Sujeto del diseño (Personaje, Planta, Animal, Objeto)")
col_suj1, col_suj2 = st.columns([3, 2])
with col_suj1:
    descripcion_sujeto = st.text_input(
        "¿Qué o quién es el sujeto?",
        placeholder="ej: Planta Monstera Deliciosa / Cantante / Auto deportivo / Gato...",
        help="Indica qué es tu sujeto para que la IA sepa con 100% de precisión qué ilustrar y no confunda el tema con la polera de referencia.",
    )
with col_suj2:
    tipo_sujeto = st.selectbox(
        "Categoría del sujeto",
        ["Auto-detectar con IA", "Planta / Botánica", "Persona / Personaje", "Animal / Mascota", "Vehículo / Objeto", "Otro"],
    )

archivos_personaje = st.file_uploader(
    "Sube una o más fotos del sujeto que va en la polera",
    type=TIPOS_IMAGEN_ACEPTADOS,
    key="personaje",
    accept_multiple_files=True,
)
st.caption("Puedes subir varias fotos del mismo sujeto (distintos ángulos ayudan a que salga más reconocible).")
recortar_personaje = st.checkbox(
    "Recortar solo al personaje/sujeto (quitar fondo)",
    value=True,
    help="Si lo desmarcas, se usa la(s) foto(s) completa(s) tal cual, con su fondo original. Desmárcalo si tu servidor tiene poca memoria RAM.",
)

# --- Colores ---
st.subheader("3. Colores a usar")

usar_colores_referencia = st.checkbox(
    "Usar los colores y la estética de la(s) imagen(es) de referencia",
    value=False,
    help="Si lo marcas, el diseño replica la paleta de colores de la referencia. Si lo dejas sin marcar, eliges tú los colores abajo.",
)

if "lista_colores" not in st.session_state:
    st.session_state.lista_colores = ["#FF3EA5"]

if not usar_colores_referencia:
    st.caption("Agrega uno o más colores para el diseño (no se usarán los de la referencia)")

    for i in range(len(st.session_state.lista_colores)):
        col_color, col_quitar = st.columns([5, 1])
        with col_color:
            st.session_state.lista_colores[i] = st.color_picker(
                f"Color {i + 1}",
                st.session_state.lista_colores[i],
                key=f"color_{i}",
            )
        with col_quitar:
            st.write("")
            if len(st.session_state.lista_colores) > 1:
                if st.button("✕", key=f"quitar_{i}", help="Quitar este color"):
                    st.session_state.lista_colores.pop(i)
                    st.rerun()

    if st.button("+ Agregar color"):
        st.session_state.lista_colores.append("#FFFFFF")
        st.rerun()
else:
    st.caption("Los colores se toman directamente de la(s) imagen(es) de referencia.")

# --- Fondo del diseño ---
st.subheader("3.1 Fondo del diseño")
quiere_fondo_solido = st.checkbox(
    "Quiero un color de fondo sólido específico (en vez de transparente)",
    value=False,
)
color_fondo_solido = None
if quiere_fondo_solido:
    color_fondo_solido = st.color_picker("Color de fondo", "#FFFFFF")
else:
    st.caption("Por defecto el fondo se deja transparente (se quita automáticamente después de generar).")

# --- Textos ---
st.subheader("4. Textos")
titulo = st.text_input("Título principal", placeholder="ej: MI GRUPO")
tamano_titulo = st.select_slider(
    "Tamaño del título",
    options=["chico", "mediano", "grande"],
    value="grande",
)
frases_sueltas_texto = st.text_input(
    "Frases sueltas pequeñas (separadas por coma)",
    placeholder="ej: WORLD TOUR, 2026",
)
st.caption("El título y las frases los dibuja Gemini como parte del diseño (o tú, con fuente real, en la mesa de composición).")
frases_sueltas = [f.strip() for f in frases_sueltas_texto.split(",") if f.strip()]

# --- Formato del estampado ---
st.subheader("4.1 Formato del estampado")
proporcion_estampado = st.selectbox(
    "Formato / Proporción del estampado",
    [
        "3:4 (Vertical polera clásico - 1620x2160)",
        "1:1 (Cuadrado de pecho - 2048x2048)",
        "9:16 (Vertical largo / Back print - 1215x2160)",
        "4:3 (Horizontal de pecho - 2160x1620)",
    ],
    index=0,
)
ratio_wh = tuple(int(x) for x in proporcion_estampado.split(" ")[0].split(":"))


@st.cache_data(show_spinner=False)
def _fuentes_disponibles(version: int = 0) -> dict:
    return composicion.listar_fuentes()


def _aplicar_valor_mesa(layout: list[dict], capas_val: list[dict]) -> list[dict]:
    """Fusiona lo que devolvió el lienzo (posiciones, orden, duplicados, quitados) con el layout guardado."""
    previos = {c["id"]: c for c in layout}
    resultado, vistos = [], set()
    for it in capas_val:
        base = previos.get(it.get("id"))
        if base is None and it.get("tipo") == "imagen":
            base = {"id": it["id"], "tipo": "imagen", "idx": int(it.get("idx") or 0)}
        if base is None:
            continue
        geom = {k: float(it[k]) for k in ("cx", "cy", "w", "h", "rot") if k in it}
        resultado.append({**base, **geom})
        vistos.add(it["id"])
    resultado.extend(c for c in layout if c["tipo"] == "texto" and c["id"] not in vistos)
    return resultado


def _seccion_mesa(archivos_ref, archivos_pers, recortar, titulo, frases, ratio_wh, color_fondo_solido):
    """
    Dibuja la mesa de composición y devuelve (layout, modo_guia, texto_local, config_texto, recortes).
    El layout vive en st.session_state.mesa_layout.
    """
    hash_ref_total = _calcular_hash_archivos(archivos_ref)
    hash_pers = _calcular_hash_archivos(archivos_pers)
    color_texto = _contraste(color_fondo_solido) if color_fondo_solido else "#FFFFFF"

    # 1. Recortes del sujeto (locales y gratis), cacheados por contenido
    clave_rec = (hash_pers, bool(recortar))
    if st.session_state.get("mesa_rec_clave") != clave_rec:
        recortes = []
        with st.spinner("Recortando el fondo de tus imágenes (local, sin costo)..."):
            for a in archivos_pers:
                img = Image.open(a)
                img.load()
                try:
                    if recortar:
                        rec = recortar_sujeto(img)
                        caja = rec.getchannel("A").getbbox()
                        recortes.append(rec.crop(caja) if caja else rec)
                    else:
                        recortes.append(generador._redimensionar_si_es_muy_grande(img.convert("RGBA")))
                except Exception as e:
                    st.warning(f"No se pudo recortar '{a.name}' ({e}). Se usa la imagen completa.")
                    recortes.append(generador._redimensionar_si_es_muy_grande(img.convert("RGBA")))
        st.session_state.mesa_recortes = recortes
        st.session_state.mesa_urls = [_a_data_url(r, 420) for r in recortes]
        st.session_state.mesa_rec_clave = clave_rec
    recortes = st.session_state.mesa_recortes
    aspectos = [r.width / r.height for r in recortes]

    # 2. Referencia que se ve de fondo
    if len(archivos_ref) > 1:
        i_ref = st.selectbox("Referencia que se ve de fondo", range(len(archivos_ref)),
                             format_func=lambda i: archivos_ref[i].name, key="mesa_ref_idx")
    else:
        i_ref = 0
    ref_arch = archivos_ref[i_ref]
    hash_ref = hashlib.md5(ref_arch.getvalue()).hexdigest()
    fondos = st.session_state.setdefault("mesa_fondos", {})
    if hash_ref not in fondos:
        ref_img = Image.open(ref_arch)
        ref_img.load()
        fondos[hash_ref] = (_a_data_url(ref_img, 800, "JPEG"), ref_img.convert("RGB"))
    fondo_url, ref_img = fondos[hash_ref]

    # 3. Layout inicial (se rearma solo si cambian imágenes, referencia o proporción)
    clave = (hash_pers, hash_ref, ratio_wh, bool(recortar))
    cajas_guardadas = st.session_state.setdefault("mesa_cajas", {})
    if st.session_state.get("mesa_clave") != clave:
        st.session_state.mesa_layout = composicion.layout_inicial(
            aspectos, titulo, frases, ratio_wh, cajas_guardadas.get(hash_ref), color_texto)
        st.session_state.mesa_clave = clave
        st.session_state.mesa_reset = st.session_state.get("mesa_reset", 0) + 1

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🪄 Proponer ubicaciones desde la referencia (IA, 1 llamada barata)", use_container_width=True):
            try:
                with st.spinner("Analizando la composición de la referencia..."):
                    cajas, tok = detectar_composicion_referencia([ref_img])
                cajas_guardadas[hash_ref] = cajas
                if not any(c["tipo"] == "ilustracion" for c in cajas):
                    st.warning("No detecté ilustraciones separadas en la referencia; se usa una distribución simple.")
                st.session_state.mesa_layout = composicion.layout_inicial(aspectos, titulo, frases, ratio_wh, cajas, color_texto)
                st.session_state.mesa_reset += 1
                st.rerun()
            except ErrorProcesoPolera as err:
                _panel_error(err)
    with col_b:
        if st.button("↺ Distribución simple (sin IA)", use_container_width=True):
            st.session_state.mesa_layout = composicion.layout_inicial(aspectos, titulo, frases, ratio_wh, None, color_texto)
            st.session_state.mesa_reset += 1
            st.rerun()

    layout = composicion.sincronizar_textos(st.session_state.mesa_layout, titulo, frases, color_texto)
    firma = hashlib.md5(json.dumps(
        [hash_pers, hash_ref, ratio_wh, bool(recortar), st.session_state.mesa_reset,
         [(c["id"], c.get("texto"), c.get("color")) for c in layout if c["tipo"] == "texto"]],
        default=str).encode()).hexdigest()

    # 4. Lienzo interactivo
    valor = _mesa_componente(
        firma=firma, fondo=fondo_url, ratio_w=ratio_wh[0], ratio_h=ratio_wh[1],
        imagenes=st.session_state.mesa_urls, capas=layout, ancho_max=560, alto_max=680,
        key="mesa_composicion", default=None,
    )
    if isinstance(valor, dict) and valor.get("firma") == firma and isinstance(valor.get("capas"), list):
        layout = _aplicar_valor_mesa(layout, valor["capas"])
    st.session_state.mesa_layout = layout

    usadas = {c["idx"] for c in layout if c["tipo"] == "imagen"}
    faltan = [i for i in range(len(recortes)) if i not in usadas]
    if faltan:
        nombres = ", ".join(f"#{i + 1} ({archivos_pers[i].name})" for i in faltan)
        st.warning(f"Estas imágenes no están en la mesa: {nombres}.")
        if st.button("➕ Agregar a la mesa las imágenes que faltan", use_container_width=True):
            for i in faltan:
                layout.append(composicion.capa_imagen_extra(layout, i, aspectos[i], ratio_wh))
            st.session_state.mesa_layout = layout
            st.session_state.mesa_reset += 1
            st.rerun()

    # 5. Cómo se usan las imágenes y el texto
    modo = st.radio(
        "Cómo usar tus imágenes",
        ["Reilustrar en el estilo de la referencia (recomendado)", "Respetar mis imágenes tal cual (solo integrarlas)"],
        horizontal=True,
        help="Reilustrar: la IA redibuja cada imagen en la técnica de la referencia, en la posición que marcaste. "
             "Respetar: conserva el aspecto de tus imágenes y solo les aplica tinta, trama y bordes.",
    )
    modo_guia = "respetar" if modo.startswith("Respetar") else "reilustrar"

    texto_local = st.checkbox(
        "Dibujar el título y las frases yo mismo con una fuente real",
        value=False,
        help="La IA deja libres las zonas de texto y las letras se componen después: ortografía perfecta, "
             "posición exacta y transparencia limpia. Cambias la fuente o el color sin volver a pagar una generación.",
    )
    config_texto = None
    if texto_local:
        fuentes = _fuentes_disponibles(st.session_state.get("fuentes_version", 0))
        opciones = ["(fuente por defecto)"] + list(fuentes)
        c1, c2 = st.columns(2)
        with c1:
            f_tit = st.selectbox("Fuente del título", opciones, index=1 if len(opciones) > 1 else 0, key="fuente_titulo")
        with c2:
            f_fra = st.selectbox("Fuente de las frases", opciones, index=min(2, len(opciones) - 1), key="fuente_frases")
        config_texto = {"fuente_titulo": fuentes.get(f_tit), "fuente_frases": fuentes.get(f_fra)}
        subida = st.file_uploader("¿Quieres otra fuente? Sube un .ttf/.otf", type=["ttf", "otf"], key="subir_fuente")
        if subida is not None and not (composicion._DIRS_FUENTES[0] / subida.name).exists():
            composicion._DIRS_FUENTES[0].mkdir(exist_ok=True)
            (composicion._DIRS_FUENTES[0] / subida.name).write_bytes(subida.getvalue())
            st.session_state.fuentes_version = st.session_state.get("fuentes_version", 0) + 1
            st.rerun()
        for capa in layout:
            if capa["tipo"] != "texto":
                continue
            cc1, cc2, cc3 = st.columns([3, 2, 2])
            etiqueta = ("Título" if capa["rol"] == "titulo" else "Frase") + f": {capa['texto'][:24]}"
            with cc1:
                capa["color"] = st.color_picker(f"Color · {etiqueta}", capa.get("color") or "#FFFFFF", key=f"col_{capa['id']}")
            with cc2:
                con_contorno = st.checkbox("Contorno", value=bool(capa.get("contorno")), key=f"chk_{capa['id']}")
            with cc3:
                capa["contorno"] = (
                    st.color_picker("Color de contorno", capa.get("contorno") or "#000000", key=f"cont_{capa['id']}")
                    if con_contorno else None
                )
    return layout, modo_guia, texto_local, config_texto, recortes


# --- Mesa de composición (opcional) ---
st.subheader("4.2 Mesa de composición (opcional)")
usar_mesa = st.checkbox(
    "Ubicar yo mismo las imágenes y los textos sobre la referencia",
    value=False,
    help="Abre un lienzo con la referencia al fondo (tenue) y tus imágenes ya recortadas. Las arrastras a donde quieres "
         "que vayan y la IA respeta esa composición. Si lo dejas apagado, todo funciona como antes.",
)
layout_final, modo_guia, texto_local, config_texto, recortes_pre = None, "reilustrar", False, None, None
if usar_mesa:
    if not archivos_referencia or not archivos_personaje or not (titulo or "").strip():
        st.info("Sube la referencia (paso 1), las imágenes del sujeto (paso 2) y escribe el título (paso 4) para armar la composición.")
    else:
        layout_final, modo_guia, texto_local, config_texto, recortes_pre = _seccion_mesa(
            archivos_referencia, archivos_personaje, recortar_personaje,
            titulo.strip(), frases_sueltas, ratio_wh, color_fondo_solido,
        )

# --- Ajustes avanzados opcionales de diseño y pose ---
with st.expander("🎨 5. Ajustes avanzados de diseño y pose (Opcionales)", expanded=False):
    st.caption("Si dejas estos campos en 'Auto' o vacíos, la IA seguirá libremente la referencia y el sujeto.")

    st.markdown("##### 👤 Sujeto: Pose, Encuadre y Expresión")
    col_p1, col_p2 = st.columns(2)
    with col_p1:
        pose_personalizada = st.text_input(
            "Pose o acción del sujeto",
            placeholder="ej: Brazos cruzados mirando al frente / Saltando...",
            help="Describe la postura o acción exacta en la que quieres que aparezca el sujeto.",
        )
        encuadre = st.selectbox(
            "Encuadre / Tipo de plano",
            [
                "Auto (según referencia)",
                "Primer plano dramático (Close-up / Rostro)",
                "Plano medio (Busto / Pecho arriba)",
                "Cuerpo entero (Full body)",
            ],
        )
    with col_p2:
        expresion = st.selectbox(
            "Expresión / Actitud",
            [
                "Auto",
                "Seria / Desafiante / Badass",
                "Sonriente / Alegre",
                "Melancólica / Nostálgica",
                "Grito / Euforia / Rock",
                "Misteriosa / Neutra",
            ],
        )
        distribucion_figuras = st.selectbox(
            "Distribución y cantidad de figuras",
            [
                "Auto (según referencia)",
                "1 sola figura dominante (Hero central)",
                "2-3 figuras (Doble exposición / Principal + secundarias)",
                "Collage múltiple (4 a 6 elementos)",
                "Cuadrícula / Catálogo de especímenes",
            ],
        )

    st.markdown("##### 🖌️ Estilo, Técnica y Acabado Textil")
    nivel_estilizacion = st.selectbox(
        "Nivel de estilización / Fidelidad al estilo",
        [
            "Transformación artística total (Recomendado: máxima fidelidad de estilo)",
            "Equilibrado (Fusión entre estilo y foto original)",
            "Conservar fisonomía fotográfica",
        ],
        index=0,
        help="Define qué tanto adapta la IA al sujeto al estilo del dibujo de referencia frente a mantener su foto realista.",
    )

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        tecnica_artistica = st.selectbox(
            "Técnica artística (Sobrescribir referencia)",
            [
                "Auto (según referencia)",
                "Vintage Bootleg Rap Tee 90s",
                "Grabado botánico / Ilustración científica vintage",
                "Ilustración vectorial nítida / Anime",
                "Psicodélico retro años 70",
                "Estilo Tatuaje tradicional / Neo-tradicional",
                "Streetwear minimalista / Cyberpunk Y2K",
            ],
        )
        acabado_textil = st.selectbox(
            "Acabado / Textura de estampado",
            [
                "Auto",
                "Sin desgaste (Digital limpio)",
                "Desgaste vintage suave (Distressed)",
                "Trama de puntos de serigrafía (Halftone dots)",
                "Desgaste grunge / ácido 90s marcado",
            ],
        )
    with col_t2:
        bordes = st.selectbox(
            "Integración de bordes",
            [
                "Auto",
                "Bordes difuminados / fundidos suaves (Fade)",
                "Enmarcado en recuadro / caja (Box print)",
                "Bordes rotos / rasgados vintage",
            ],
        )
        iluminacion = st.selectbox(
            "Iluminación y atmósfera",
            [
                "Auto",
                "Luz de estudio suave y neutra",
                "Contraste dramático / Claroscuro",
                "Iluminación de concierto / Neones y reflectores",
                "Luz dorada cálida (Golden hour)",
            ],
        )

    st.markdown("##### ✨ Acentos, Instrucciones y Formato")
    acentos_graficos = st.multiselect(
        "Acentos gráficos secundarios a incluir",
        [
            "Destellos y estrellas 90s (Sparkles)",
            "Rayos / Relámpagos",
            "Fuego / Llamas estilizadas",
            "Flores / Elementos botánicos",
            "Sellos / Códigos y etiquetas técnicas",
            "Humo / Niebla de fondo",
        ],
    )

    col_i1, col_i2 = st.columns(2)
    with col_i1:
        instrucciones_extra = st.text_area(
            "Instrucciones puntuales adicionales",
            placeholder="ej: Que tenga lentes de sol oscuros puestos, relieve cromado brillante en las letras...",
            height=80,
        )
    with col_i2:
        elementos_a_evitar = st.text_area(
            "Cosas que NO quieres ver (Negativo)",
            placeholder="ej: Sin marcos cuadrados, sin deformaciones en las manos, sin logos ajenos...",
            height=80,
        )

# --- Modelo de IA a usar (Costo / Calidad) ---
st.subheader("6. Calidad / costo de la generación")
nombre_modelo = st.select_slider(
    "Elige qué tan barato o bueno quieres el resultado",
    options=list(MODELOS_IMAGEN.keys()),
    value=MODELO_IMAGEN_POR_DEFECTO,
)
st.caption("Lite es lo más barato para probar variantes rápido. Pro da mejor calidad pero cuesta más por imagen.")

st.divider()

# --- Botón de generación con consola interactiva ---
if st.button("✨ Generar diseño", type="primary", use_container_width=True):
    if not archivos_referencia or not archivos_personaje:
        st.error("Necesitas subir al menos una imagen de referencia y al menos una foto del personaje o sujeto.")
    elif not titulo or not titulo.strip():
        st.error("Escribe al menos el título principal.")
    else:
        # Contenedor dinámico de la consola en vivo
        consola_placeholder = st.empty()
        logs_sesion = []

        def callback_interfaz(etapa_num, total_etapas, nombre_fase, mensaje, estado, metadata):
            hora = datetime.now().strftime("%H:%M:%S")
            logs_sesion.append({
                "timestamp": hora,
                "etapa": etapa_num,
                "total": total_etapas,
                "fase": nombre_fase,
                "mensaje": mensaje,
                "estado": estado,
            })
            renderizar_consola(consola_placeholder, logs_sesion, etapa_num, total_etapas, estado)

        imagenes_referencia = [Image.open(archivo) for archivo in archivos_referencia]
        imagenes_personaje = [Image.open(archivo) for archivo in archivos_personaje]
        frases_sueltas = [f.strip() for f in frases_sueltas_texto.split(",") if f.strip()]

        # Gestión de caché de análisis
        hash_actual = (
            _calcular_hash_archivos(archivos_referencia)
            + "_"
            + _calcular_hash_archivos(archivos_personaje)
            + "_"
            + descripcion_sujeto.strip()
        )
        cache_a_usar = None
        if reutilizar_cache and "cache_analisis" in st.session_state and st.session_state.cache_analisis:
            if st.session_state.get("cache_hash") == hash_actual:
                cache_a_usar = st.session_state.cache_analisis

        try:
            resultado = generar_diseno_desde_imagenes(
                imagenes_referencia=imagenes_referencia,
                imagenes_personaje=imagenes_personaje,
                recortar_personaje=recortar_personaje,
                usar_colores_referencia=usar_colores_referencia,
                colores=st.session_state.lista_colores,
                titulo=titulo.strip(),
                tamano_titulo=tamano_titulo,
                frases_sueltas=frases_sueltas,
                modelo_imagen=MODELOS_IMAGEN[nombre_modelo],
                color_fondo_solido=color_fondo_solido,
                descripcion_sujeto=descripcion_sujeto,
                tipo_sujeto=tipo_sujeto,
                pose_personalizada=pose_personalizada,
                encuadre=encuadre,
                expresion=expresion,
                distribucion_figuras=distribucion_figuras,
                tecnica_artistica=tecnica_artistica,
                acabado_textil=acabado_textil,
                bordes=bordes,
                iluminacion=iluminacion,
                acentos_graficos=acentos_graficos,
                instrucciones_extra=instrucciones_extra,
                elementos_a_evitar=elementos_a_evitar,
                proporcion_estampado=proporcion_estampado,
                nivel_estilizacion=nivel_estilizacion,
                callback_progreso=callback_interfaz,
                cache_analisis=cache_a_usar,
                modo_ahorro_tokens=modo_ahorro_sujeto,
                layout=layout_final if usar_mesa else None,
                modo_guia=modo_guia,
                texto_local=bool(usar_mesa and texto_local),
                config_texto=config_texto if (usar_mesa and texto_local) else None,
                recortes_precalculados=recortes_pre if usar_mesa else None,
            )

            # Guardar análisis en caché para reutilizar en siguientes intentos
            if hasattr(resultado, "info") and "cache_generada" in resultado.info:
                st.session_state.cache_analisis = resultado.info["cache_generada"]
                st.session_state.cache_hash = hash_actual

            _registrar_version(resultado, {
                "titulo": titulo.strip(),
                "modelo": nombre_modelo,
                "nota": "Generación inicial",
                "solid": color_fondo_solido,
                "layout_texto": layout_final if (usar_mesa and texto_local) else None,
                "config_texto": config_texto if (usar_mesa and texto_local) else None,
                "layout_completo": layout_final if usar_mesa else None,
            })

        except ErrorProcesoPolera as err:
            # Registrar el error en la consola en vivo
            callback_interfaz(
                err.fase,
                6,
                err.fase,
                f"FALLO: {err.mensaje}",
                "error",
                {},
            )
            _panel_error(err)

        except Exception as e:
            tb = traceback.format_exc()
            callback_interfaz(
                6,
                6,
                "Error General",
                f"Error inesperado: {e}",
                "error",
                {},
            )
            st.error(f"🛑 Error no controlado: {e}")
            with st.expander("📋 Ver traceback completo"):
                st.code(tb, language="python")


# --- Resultado (persiste entre interacciones: ajustar, recomponer texto, versiones) ---
def _mostrar_ultimo():
    ult = st.session_state.get("ultimo")
    if not ult:
        return
    st.divider()
    st.success(f"🎉 {ult.get('nota', 'Diseño listo')}")

    img = Image.open(io.BytesIO(ult["png"]))
    fondo_vista = st.radio("Ver sobre", ["Transparente", "Blanco", "Negro", "Gris"], horizontal=True, key="vista_fondo",
                           help="Solo cambia cómo lo ves aquí; el archivo descargado conserva su transparencia.")
    vista = img
    if fondo_vista != "Transparente":
        color = {"Blanco": (255, 255, 255), "Negro": (0, 0, 0), "Gris": (128, 128, 128)}[fondo_vista]
        base = Image.new("RGBA", img.size, color + (255,))
        base.alpha_composite(img.convert("RGBA"))
        vista = base.convert("RGB")
    st.image(vista, caption="Diseño final de polera", use_container_width=True)

    reg_tokens = ult.get("tokens", {})
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    with col_m1:
        st.metric("⏱️ Tiempo Total", f"{ult.get('tiempo', 0)}s")
    with col_m2:
        suj = reg_tokens.get("analisis_sujeto", {})
        st.metric("👤 Sujeto", "0 (Caché)" if suj.get("desde_cache") else f"{suj.get('total', 0):,}")
    with col_m3:
        ref = reg_tokens.get("analisis_referencias", {})
        st.metric("🎨 Referencias", "0 (Caché)" if ref.get("desde_cache") else f"{ref.get('total', 0):,}")
    with col_m4:
        st.metric("🪙 Total Tokens", f"{reg_tokens.get('total_tokens', 0):,}")

    if ult.get("prompt"):
        with st.expander("🔍 Ver prompt e instrucciones enviadas a la IA"):
            st.text(ult["prompt"])

    st.download_button("⬇️ Descargar diseño (PNG)", data=ult["png"], file_name="diseno_polera.png",
                       mime="image/png", use_container_width=True)
    st.caption(f"Guardado también en la carpeta salidas/{ult['id']}")

    # Archivo de impresión (se calcula solo si se pide: es pesado)
    if st.button("🖨️ Preparar archivo para impresión (2x, 300 ppp)", use_container_width=True):
        with st.spinner("Ampliando..."):
            grande = composicion.preparar_para_impresion(img, 2.0)
            st.session_state.impresion = {"id": ult["id"], "data": _png_bytes(grande, dpi=(300, 300)), "tam": grande.size}
    imp = st.session_state.get("impresion")
    if imp and imp["id"] == ult["id"]:
        st.download_button(f"⬇️ Descargar para impresión ({imp['tam'][0]}x{imp['tam'][1]} px, 300 ppp)", data=imp["data"],
                           file_name="diseno_polera_impresion.png", mime="image/png", use_container_width=True)
        st.caption("Es una ampliación con enfoque suave: sirve para llegar al tamaño de archivo que pide la imprenta, "
                   "pero no crea detalle nuevo. Si necesitas más nitidez real, conviene un reescalador dedicado (p. ej. Real-ESRGAN).")

    st.markdown("### ✏️ Ajustar este diseño")
    nueva_nota = None
    if ult.get("layout_texto") and ult.get("cruda") and usar_mesa and texto_local and layout_final:
        if st.button("🔤 Recomponer el texto con los ajustes actuales (gratis, no llama a la IA)", use_container_width=True,
                     help="Aplica de nuevo título y frases con la posición, fuente y color que tienes ahora en la mesa."):
            cruda = Image.open(io.BytesIO(ult["cruda"]))
            ancho, alto = generador.DIMENSIONES_POR_PROPORCION[ult["ratio"]]
            nuevo = generador._postprocesar(cruda, ult.get("solid"), ancho, alto, layout_final, config_texto)
            nuevo.info.update({"imagen_cruda": cruda, "ratio_str": ult["ratio"], "prompt_final": ult.get("prompt", "")})
            _registrar_version(nuevo, {
                "titulo": ult.get("titulo"), "modelo": ult.get("modelo"), "nota": "Texto recompuesto (sin costo)",
                "solid": ult.get("solid"), "layout_texto": layout_final, "config_texto": config_texto,
                "layout_completo": layout_final,
            })
            st.rerun()

    instruccion = st.text_area(
        "Cambio puntual con IA (cuesta una generación de imagen)",
        placeholder="ej: hazle los lentes más grandes / cambia el fondo del recuadro a azul / quita la sombra bajo la figura",
        height=70, key="instr_edicion",
    )
    if st.button("✨ Aplicar este cambio", use_container_width=True):
        if not ult.get("cruda"):
            st.warning("Esta versión no tiene la imagen original del modelo guardada; genera de nuevo.")
        elif not instruccion.strip():
            st.warning("Escribe qué quieres cambiar.")
        else:
            try:
                with st.spinner("Editando el diseño con la IA..."):
                    nuevo = editar_diseno(
                        Image.open(io.BytesIO(ult["cruda"])), instruccion, MODELOS_IMAGEN[nombre_modelo],
                        ult["ratio"], ult.get("solid"), ult.get("layout_texto"), ult.get("config_texto"),
                    )
                _registrar_version(nuevo, {
                    "titulo": ult.get("titulo"), "modelo": nombre_modelo, "nota": f"Edición: {instruccion.strip()[:70]}",
                    "prompt": f"[EDICIÓN] {instruccion.strip()}", "solid": ult.get("solid"),
                    "layout_texto": ult.get("layout_texto"), "config_texto": ult.get("config_texto"),
                    "layout_completo": ult.get("layout_texto"),
                })
                st.rerun()
            except ErrorProcesoPolera as err:
                _panel_error(err)

    historial = st.session_state.get("historial", [])
    if len(historial) > 1:
        with st.expander(f"🕘 Versiones de esta sesión ({len(historial)})"):
            cols = st.columns(4)
            for i, h in enumerate(historial):
                with cols[i % 4]:
                    st.image(h.get("mini") or h["png"], caption=f"{h['hora']} · {h.get('nota', '')[:32]}", use_container_width=True)
                    if st.button("Ver esta", key=f"ver_{h['id']}", disabled=(h["id"] == ult["id"])):
                        st.session_state.ultimo = h
                        st.rerun()


_mostrar_ultimo()
