"""
Interfaz visual de prueba para el generador de diseños de poleras.

Correr con:
    python -m streamlit run app_visual.py

Esto abre una pestaña en tu navegador (local, en tu propio computador,
no sube nada a internet salvo las llamadas a la API de Gemini) con
todos los inputs como formulario.
"""

import io
import importlib

import streamlit as st
from PIL import Image
import pillow_avif  # noqa: F401 -- con solo importarlo, Pillow aprende a abrir archivos .avif

import generador
importlib.reload(generador)
from generador import generar_diseno_desde_imagenes, MODELOS_IMAGEN, MODELO_IMAGEN_POR_DEFECTO

st.set_page_config(page_title="Generador de diseños de poleras", layout="centered")

TIPOS_IMAGEN_ACEPTADOS = ["jpg", "jpeg", "png", "webp", "avif"]

st.title("👕 Generador de diseños de poleras")
st.caption("Sube referencias, tu personaje, elige colores y texto — Gemini arma el diseño.")

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
    help="Si lo desmarcas, se usa la(s) foto(s) completa(s) tal cual, con su fondo original. Se aplica a todas por igual.",
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
            st.write("")  # espaciador para alinear el botón con el color picker
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

# --- Modelo de IA a usar (último paso, define el costo) ---
st.subheader("5. Calidad / costo de la generación")
nombre_modelo = st.select_slider(
    "Elige qué tan barato o bueno quieres el resultado",
    options=list(MODELOS_IMAGEN.keys()),
    value=MODELO_IMAGEN_POR_DEFECTO,
)
st.caption("Lite es lo más barato para probar variantes rápido. Pro da mejor calidad pero cuesta más por imagen.")

st.divider()

# --- Botón de generación ---
if st.button("✨ Generar diseño", type="primary", use_container_width=True):
    if not archivos_referencia or not archivos_personaje:
        st.error("Necesitas subir al menos una imagen de referencia y al menos una foto del personaje o sujeto.")
    elif not titulo:
        st.error("Escribe al menos el título principal.")
    else:
        imagenes_referencia = [Image.open(archivo) for archivo in archivos_referencia]
        imagenes_personaje = [Image.open(archivo) for archivo in archivos_personaje]
        frases_sueltas = [
            f.strip() for f in frases_sueltas_texto.split(",") if f.strip()
        ]

        with st.spinner("Generando... (analiza referencias, procesa personaje/sujeto y genera diseño)"):
            try:
                resultado = generar_diseno_desde_imagenes(
                    imagenes_referencia=imagenes_referencia,
                    imagenes_personaje=imagenes_personaje,
                    recortar_personaje=recortar_personaje,
                    usar_colores_referencia=usar_colores_referencia,
                    colores=st.session_state.lista_colores,
                    titulo=titulo,
                    tamano_titulo=tamano_titulo,
                    frases_sueltas=frases_sueltas,
                    modelo_imagen=MODELOS_IMAGEN[nombre_modelo],
                    color_fondo_solido=color_fondo_solido,
                    descripcion_sujeto=descripcion_sujeto,
                    tipo_sujeto=tipo_sujeto,
                )
                st.success("¡Listo!")
                st.image(resultado, caption="Diseño generado", use_container_width=True)

                if hasattr(resultado, "info") and "prompt_final" in resultado.info:
                    with st.expander("🔍 Ver prompt e instrucciones enviadas a la IA"):
                        st.text(resultado.info["prompt_final"])

                buffer = io.BytesIO()
                resultado.save(buffer, format="PNG")
                st.download_button(
                    "⬇️ Descargar diseño",
                    data=buffer.getvalue(),
                    file_name="diseno_polera.png",
                    mime="image/png",
                )
            except Exception as e:
                st.error(f"Algo falló: {e}")
