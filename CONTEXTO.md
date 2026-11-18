# Contexto del Proyecto: creaPoleras

Este documento resume la arquitectura técnica, estado del proyecto, decisiones de diseño y las funcionalidades implementadas para mantener el contexto operativo entre sesiones y desarrollos.

---

## 1. Visión General del Proyecto

**creaPoleras** es un generador inteligente y profesional de diseños para estampados de poleras/camisetas que utiliza los modelos de visión y generación de imágenes de **Google Gemini** (a través del SDK oficial `google-genai`).

El sistema toma como entrada:
1. Una o varias **imágenes de referencia** (poleras existentes con estilos gráficos atractivos: bootleg rap tees 90s, láminas científicas vintage, serigrafía, anime, etc.).
2. Una o varias **fotos del sujeto objetivo** (un personaje, mascota, planta, vehículo u objeto).
3. Parámetros de diseño (paleta de colores, fondo transparente o sólido, formato de proporción, textos opcionales y directrices de estilización).

A través de un análisis forense de la referencia (técnica artística, tramado, sombreado y diagramación), la IA sintetiza una especificación maestra y genera el nuevo arte aplicado exclusivamente al sujeto deseado, con costos mínimos de API (0 tokens en procesos locales).

---

## 2. Arquitectura de Archivos y Módulos

```
programa poleras/
├── app_visual.py              # Interfaz web principal con Streamlit (flujo en 6 pasos y consola en vivo)
├── generador.py               # Lógica de prompts, llamadas a Gemini, recorte rembg y caché de visión
├── composicion.py             # Geometría del lienzo, detección de cajas, composición de guía y texto local
├── componente_mesa/           # Componente web personalizado para Streamlit (Konva.js)
│   ├── index.html             # Mesa de composición interactiva (Konva.js) con drag & drop y transformadores
│   └── konva.min.js           # Konva 9.3.6 incluido en el repo (sin npm ni CDN)
├── datos/aprendizajes.json    # Memoria de lecciones y reglas (la crea la app; fuera de git; volumen en Docker)
├── fuentes/                   # Fuentes tipográficas (.ttf / .otf) para composición local de texto
├── salidas/                   # Directorio donde se guardan diseños, historiales y archivos de impresión
├── docker-compose.yml         # Orquestación de contenedores para Contabo con volúmenes persistentes
├── Dockerfile                 # Imagen Docker basada en Python 3.11-slim para Linux VPS
├── .env / .env.example        # Clave GEMINI_API_KEY y configuración de entorno
├── requirements.txt           # Dependencias Python (google-genai, streamlit, pillow, rembg, colorthief, etc.)
├── Abrir Generador de Poleras.bat # Launcher de un clic para usuarios Windows
├── README.md                  # Documentación general y guía rápida
├── LEEME.md                   # Manual detallado de usuario en español
├── DESPLIEGUE_CONTABO.md      # Guía paso a paso de instalación y mantenimiento en VPS Contabo
└── CONTEXTO.md                # Este documento de referencia y memoria técnica
```

---

## 3. Funcionalidades Recientes Implementadas

### A. Vista Previa y Depuración de Recortes del Sujeto (Paso 2)
* **Objetivo**: Permitir al usuario inspeccionar y pulir el recorte de silueta de cada foto subida antes de generar la polera, corriendo **100% en la PC local a 0 tokens de API**.
* **Visualización individual**: Muestra la foto original vs la silueta recortada para cada archivo (en columnas para 1 imagen o en pestañas organizadas para múltiples imágenes).
* **Fondos de inspección seleccionables**:
  - `Ajedrezado transparente`: Patrón clásico para verificar transparencia neutra.
  - `Fondo oscuro (#181818)`: Revela inmediatamente halos claros o rebordes blancos del fondo original.
  - `Fondo blanco (#FFFFFF)`: Revela sombras oscuras o bordes sucios.
  - `Transparente puro (PNG)`.
* **Depuración ultra veloz (~5ms) con caché en 2 capas**:
  - `obtener_recorte_base`: Ejecuta `rembg` una sola vez por imagen y cachea en memoria por hash MD5.
  - `depurar_recorte`:
    - **Umbral / Tolerancia alfa (slider 1 a 80)**: Elimina niebla semitransparente residual.
    - **Reducción de halos / Erosión (slider 0 a 3px)**: Aplica `ImageFilter.MinFilter(3)` de PIL sobre el canal alfa para encoger de forma perimetral el borde 1, 2 o 3 píxeles, quitando cualquier sangrado de color del fondo original.
* **Descarga directa**: Botón individual para descargar el PNG transparente recortado.
* **Opción "Mi sujeto ya viene recortado"**: Si la imagen ya es un PNG sin fondo (Canva/Photoshop), el sistema preserva su canal alfa original al 100% sin degradarlo con rembg.
* **Reutilización instantánea**: Los recortes depurados se guardan en `st.session_state.recortes_depurados` y pasan directamente a la mesa de composición y a la generación final sin volver a calcular.

### B. Diseños Sin Título Principal o 100% Libres de Texto (Paso 4)
* **Objetivo**: Permitir poleras donde el arte gráfico y las ilustraciones sean las protagonistas, sin obligar al usuario a colocar un título tipográfico.
* **Control UI**: Checkbox `[ ] 🚫 No incluir título principal (diseño solo con ilustraciones o frases)`.
* **Prompt Maestro**: En `generador.py`, si no hay título, Gemini recibe una prohibición explícita de inventar títulos, palabras o tipografías grandes.
  - Si hay frases secundarias: solo dibuja esas frases en su estilo secundario.
  - Si no hay frases secundarias: la pieza se declara **100% libre de texto**, reservando todo el espacio a las ilustraciones y ornamentos gráficos del estilo.
* **Mesa de Composición**: En `composicion.py`, al detectar `titulo == ""`, omite la capa de título y expande el área superior de las ilustraciones (desde `0.04` en lugar de `0.17`).

### C. Memoria de Aprendizaje y Retroalimentación Persistente (`aprendizajes.json`)
* **Objetivo**: Permitir al usuario enseñarle a la aplicación qué cosas salieron mal en un diseño para que la IA aprenda y no repita errores.
* **Sección post-generación**: Caja de comentarios tras generar: "¿Qué cosas salieron mal en este diseño?".
* **Acciones disponibles**:
  - **"🧠 Guardar como aprendizaje permanente"**: Almacena la lección en `aprendizajes.json`. Esta regla se inyecta como directriz obligatoria en el prompt maestro de todas las generaciones futuras.
  - **"🔄 Corregir y Regenerar ahora con este feedback"**: Vuelve a generar inmediatamente inyectando el feedback como directriz urgente #0 de máxima prioridad.
* **Panel de administración**: Expander en la barra lateral izquierda que permite ver todas las reglas aprendidas, activarlas/desactivarlas con checkboxes, borrarlas o añadir reglas manuales.

### D. Mesa de Composición Interactiva (Konva.js)
* **Lienzo visual (Paso 4.2)**: Permite arrastrar libremente imágenes del sujeto y cajas de texto sobre la referencia tenue.
* **Transformadores corregidos**:
  - Imágenes: 4 anclajes de esquina, escala proporcional. Texto: 8 anclajes.
  - El área clicable de cada imagen es su rectángulo completo (se quitó el clic por silueta `hitPorAlfa`: falló en la PC del usuario, probablemente por la escala de pantalla de Windows, y no se pudo reproducir en Chrome headless).
  - **Pendiente / conocido**: el usuario reportó que no podía arrastrar con el ratón todas las imágenes (con teclado y con los botones de la lista de capas sí) y pidió escalar desde los bordes. Tras quitar `hitPorAlfa` no se ha reconfirmado con el usuario. Los bordes de las imágenes están deshabilitados (evitan deformar al sujeto); reactivarlos es cambiar `enabledAnchors` en `seleccionar()`.
* **Opciones avanzadas**: "Reilustrar en el estilo" vs "Respetar mis imágenes tal cual", y "Dibujar textos localmente con fuentes reales (.ttf/.otf)".

### E. Botón de Reiniciar y Limpieza de Disco
* **Objetivo**: Limpiar el estado de la sesión, restablecer archivos subidos y vaciar cachés.
* **Función `reiniciar_todo`**:
  - Incrementa `uploader_reset_id` para vaciar los componentes de subida de archivos de Streamlit.
  - Borra claves de sesión, historiales, cachés de rembg y mesas de composición.
  - Casilla opcional, desmarcada por defecto (para no borrar diseños sin querer), que borra también la carpeta `salidas/` del disco.
  - Accesible desde la barra lateral ("🔄 Reiniciar Proceso y Limpiar Imágenes") y desde la cabecera ("🧹 Limpiar todo").

### F. Fondo transparente robusto (`quitar_fondo_adaptativo`)
* **Problema**: el chroma key clásico solo funciona si el modelo devuelve EXACTAMENTE el verde `#00FF7F` plano. Si dibujaba otro fondo (blanco, crema, viñeta) la imagen quedaba sin transparencia y sin aviso.
* **Ahora** se mide el color real del borde de la imagen generada:
  - Borde homogéneo y verde croma → chroma key global (con despill).
  - Borde homogéneo de otro color → se recorta ese color solo desde los bordes (`scipy.ndimage.label`), así un blanco interior del diseño (ojos, letras) se conserva.
  - Borde irregular (el modelo dibujó una escena o prenda) → `rembg` local como plan B + aviso.
* El diagnóstico (`metodo`, `transparente_pct`, `aviso`) viaja en `imagen.info["diagnostico_fondo"]` y la app muestra una advertencia amarilla bajo el diseño. Costo de API: 0.

### G. Referencia con forma de polera → solo el estampado
* El análisis de referencias (misma llamada de siempre, sin costo extra) devuelve `referencia_es_prenda` y `areas_estampado` (caja 0-1000).
* `preparar_referencias_para_modelo` recorta la foto al estampado antes de enviarla al modelo, así nunca ve cuello, mangas ni silueta. El perfil de color también se calcula sobre el recorte (no sobre el color de la tela).
* El prompt exige "SOLO el estampado, nunca la prenda" siempre, y refuerza si se detectó una prenda.
* Límites: si la caja es absurda (muy chica o casi toda la foto) no se recorta. Un análisis viejo en caché no trae estos campos (no recorta). La mesa de composición todavía usa la foto completa de la referencia como fondo y para detectar cajas.

### H. Memoria de aprendizaje: detalles de implementación
* Archivo: `datos/aprendizajes.json` (variable `DATOS_DIR` para cambiarlo). Si no existe, migra el `aprendizajes.json` antiguo de la raíz o siembra `REGLAS_BASE` desde el código.
* En el prompt entran las reglas base activas + las 10 lecciones activas más recientes (`MAX_REGLAS_EN_PROMPT`), para que el prompt no crezca sin límite.
* Las lecciones son GLOBALES: aplican a todo sujeto. Redactarlas en general ("no dibujar texto extra") y no sobre un caso ("el dinosaurio salió feo").
* PC y servidor tienen memorias separadas.

### I. Modo Reilustrar de la mesa: la guía NO lleva las fotos
* **Problema detectado**: la guía enviada al modelo tenía los recortes reales pegados en su posición; el modelo los copiaba y devolvía la referencia con una foto encima (0 % reilustrado).
* **Ahora** (`composicion.componer_guia(..., modo=...)`): en `reilustrar` la guía solo trae siluetas grises NUMERADAS (`_silueta_numerada`, sin ningún píxel de la foto) que marcan posición, tamaño y rotación; la identidad del sujeto viene de las fotos que se envían aparte. En `respetar` se siguen pegando los recortes reales.
* **Prompt**: bloque `ROL` de director de arte (siempre) + directiva de reilustración (solo mesa+reilustrar): redibujar desde cero con trazo, trama, sombreado y paleta de la referencia, quitar ruido fotográfico, integrar elementos, comprobación final "si parece foto o pegatina, está mal", y no copiar personajes ni composición de la referencia.
* **Composición guiada, no molde** (Reilustrar): la guía expresa la intención del usuario ("más o menos dónde va cada cosa"). El prompt conserva cuántos elementos hay, su zona general y la jerarquía de tamaños, pero autoriza al modelo a ajustar posiciones/escalas/ángulos (~15 %), solapar y añadir ornamentación de enlace, y le pide reinterpretar el conjunto como un todo. La regla rígida "no cambies su ubicación ni su escala" queda solo en modo Respetar. Antes la tenían los dos modos y contradecía la integración.
* **Pose libre por defecto**: casilla "Conservar la pose y la silueta de mi foto" (clave `mesa_conservar_pose`, desmarcada). Desmarcada, la guía usa ÓVALOS neutros (solo zona y tamaño) y el prompt dice que la pose/ángulo/encuadre no tienen que ser los de la foto, conservando la identidad (rasgos, colores, marcas). Marcada, la guía usa la silueta del recorte y el prompt pide mantener la pose. Parámetro `conservar_pose` de `generar_diseno_desde_imagenes` y `componer_guia`.
* **Sin verificar con el modelo real**: se probó con un Gemini falso (la guía no contiene la foto; el prompt trae las reglas). Si aun así copia, el siguiente paso es un flujo en dos pasadas: redibujar cada sujeto por separado (1 llamada por elemento) y componer/integrar al final. Cuesta N+1 llamadas.

---

## 4. Compatibilidad Multiplataforma (PC Windows y Contabo VPS)

El proyecto está diseñado bajo la regla estricta de que **todo cambio debe funcionar de forma idéntica en ambos entornos**:

1. **PC Local (Windows)**:
   - Rutas compatibles con `pathlib.Path`.
   - Launcher `Abrir Generador de Poleras.bat`.
   - Manejo seguro de caracteres UTF-8 en consola para evitar errores `cp1252` en Windows.

2. **VPS Contabo (Linux Debian/Ubuntu con Docker)**:
   - Contenedor Docker basado en `python:3.11-slim` con dependencias nativas instaladas (`libgl1`, `libglib2.0-0`).
   - `docker-compose.yml` monta los siguientes volúmenes persistentes:
     - `u2net-models:/root/.u2net`: Evita volver a descargar el modelo de IA de `rembg` cada vez que se reinicia el contenedor.
     - `./salidas:/app/salidas`: Persiste todos los diseños generados e imprimibles en el host.
     - `./datos:/app/datos`: Persiste `aprendizajes.json` entre reinicios y redeploys. Se monta una CARPETA (no un archivo suelto: Docker crearía un directorio si el archivo no existe y git chocaría con los cambios del servidor). Está en `.gitignore`.
     - Variable `REMBG_MODELO=u2net` en el `Dockerfile`: el modelo por defecto actual de rembg (`bria-rmbg`) agota la RAM al construir/usar en un VPS de 8 GB. En la PC no está definida y usa el valor por defecto de la librería.
     - Acceso: la app escucha solo en `127.0.0.1:8501`; se publica por HTTPS a través de Caddy. `APP_PASSWORD` en el `.env` activa el login (si no existe, la app abre sin login, como en la PC).

### Comandos de actualización en Contabo:
```bash
git pull
docker compose up -d --build
```
> La reconstrucción tarda varios minutos. Si se lanza por SSH conviene correrla con `nohup` y revisar el log: una conexión cortada no la detiene, pero sí te deja sin ver el resultado.

---

## 5. Modelos de Gemini Utilizados

| Tarea | Modelo | Justificación |
|---|---|---|
| Análisis de referencias | `gemini-3.5-flash-lite` | Rápido, económico y preciso para extraer técnica y diagramación |
| Análisis de sujeto (visión) | `gemini-3.5-flash-lite` | Identifica taxonomía y rasgos visuales a muy bajo costo de tokens |
| Detección de cajas en mesa | `gemini-3.5-flash-lite` | Extrae bounding boxes con una sola llamada |
| Generación de imagen (Lite) | `gemini-3.1-flash-lite-image` | Ideal para pruebas rápidas y económicas |
| Generación de imagen (Media) | `gemini-3.1-flash-image` | Balance estándar entre calidad y costo |
| Generación de imagen (Pro) | `gemini-3-pro-image` | Máxima fidelidad y resolución artística |
| Recorte de fondos | `rembg` (u2net local) | 0 costo de tokens, ejecutado 100% en el servidor/PC |
