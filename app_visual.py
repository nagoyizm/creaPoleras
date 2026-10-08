"""
Interfaz visual para el generador de diseños de poleras con consola en vivo,
diagnóstico forense de errores y optimización de tokens.

Correr con:
    python -m streamlit run app_visual.py
"""

import io
import sys
import time
import hashlib
import traceback
from datetime import datetime

import streamlit as st
from PIL import Image
import pillow_avif  # noqa: F401 -- con solo importarlo, Pillow aprende a abrir archivos .avif

import generador
from generador import (
    generar_diseno_desde_imagenes,
    validar_conexion_api,
    ErrorProcesoPolera,
    MODELOS_IMAGEN,
    MODELO_IMAGEN_POR_DEFECTO,
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
    """Calcula un hash MD5 de los archivos para saber si cambiaron o se pueden reusar desde caché."""
    if not archivos:
        return ""
    h = hashlib.md5()
    for a in archivos:
        h.update(f"{a.name}_{a.size}".encode("utf-8"))
    return h.hexdigest()


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
st.caption("El título y las frases los dibuja Gemini como parte del diseño.")

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
            )

            # Guardar análisis en caché para reutilizar en siguientes intentos
            if hasattr(resultado, "info") and "cache_generada" in resultado.info:
                st.session_state.cache_analisis = resultado.info["cache_generada"]
                st.session_state.cache_hash = hash_actual

            # Mostrar resultado con éxito
            st.success("🎉 ¡Diseño generado exitosamente!")
            st.image(resultado, caption="Diseño final de polera", use_container_width=True)

            # Métricas de tiempo y consumo de tokens
            if hasattr(resultado, "info"):
                info = resultado.info
                reg_tokens = info.get("registro_tokens", {})
                tiempo = info.get("tiempo_total_segundos", 0)

                col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                with col_m1:
                    st.metric("⏱️ Tiempo Total", f"{tiempo}s")
                with col_m2:
                    suj_tok = reg_tokens.get("analisis_sujeto", {})
                    txt_suj = "0 (Caché)" if suj_tok.get("desde_cache") else f"{suj_tok.get('total', 0):,}"
                    st.metric("👤 Sujeto", txt_suj)
                with col_m3:
                    ref_tok = reg_tokens.get("analisis_referencias", {})
                    txt_ref = "0 (Caché)" if ref_tok.get("desde_cache") else f"{ref_tok.get('total', 0):,}"
                    st.metric("🎨 Referencias", txt_ref)
                with col_m4:
                    tot = reg_tokens.get("total_tokens", 0)
                    st.metric("🪙 Total Tokens", f"{tot:,}")

                if "prompt_final" in info:
                    with st.expander("🔍 Ver prompt e instrucciones enviadas a la IA"):
                        st.text(info["prompt_final"])

            buffer = io.BytesIO()
            resultado.save(buffer, format="PNG")
            st.download_button(
                "⬇️ Descargar diseño en alta resolución",
                data=buffer.getvalue(),
                file_name="diseno_polera.png",
                mime="image/png",
                use_container_width=True,
            )

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
            # Panel forense de diagnóstico
            st.error(f"🛑 Falló en la fase: **{err.fase}**")

            with st.container():
                st.markdown(f"### 🚨 {err.mensaje}")
                if err.diagnostico:
                    st.warning(f"**🔍 Causa identificada:** {err.diagnostico}")
                if err.sugerencia:
                    st.info(f"**💡 ¿Cómo solucionarlo y no perder dinero?**\n\n{err.sugerencia}")

                # Reportar tokens gastados antes de la falla
                if err.tokens_gastados:
                    tot_gastados = err.tokens_gastados.get("total_tokens", 0)
                    if tot_gastados > 0:
                        st.caption(f"🪙 Tokens consumidos antes de la interrupción: {tot_gastados:,}")
                    else:
                        st.caption("⚡ La falla ocurrió antes o durante la petición de imagen, no se gastó cuota adicional.")

                with st.expander("📋 Ver detalle técnico para soporte o depuración"):
                    st.code(err.detalle_tecnico or str(err.error_original), language="text")

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
