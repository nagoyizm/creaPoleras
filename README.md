# creaPoleras

Generador interactivo de diseños de poleras utilizando Inteligencia Artificial (Google Gemini).

## Instalación y Requisitos

1. Crear y activar el entorno virtual:
```bash
python -m venv venv

# En Windows:
venv\Scripts\activate

# En Linux / macOS:
source venv/bin/activate
```

2. Instalar dependencias:
```bash
pip install -r requirements.txt
```

## Configuración de variables de entorno

Crea un archivo `.env` basándote en `.env.example`:
```bash
cp .env.example .env
```

Añade tu API Key de Google AI Studio en `.env`:
```env
GEMINI_API_KEY=tu_api_key_aqui
```

## Uso

### Interfaz Web (Streamlit)
```bash
python -m streamlit run app_visual.py
```
*(En Windows también puedes hacer doble clic en `Abrir Generador de Poleras.bat`)*

### Modo Consola / Script
```bash
python generador.py
```

## Mesa de composición (ubicar imágenes y textos)

En la interfaz, el paso **4.2** (opcional) abre un lienzo interactivo con Konva.js con la referencia de fondo (tenue) y tus
imágenes ya recortadas. Arrástralas con drag & drop, escálalas proporcionalmente desde los anclajes de las 4 esquinas y rótalas donde quieras; el título y las frases son
cajas movibles. Al generar, la IA recibe un boceto con esas posiciones y coordenadas explícitas.

- **Proponer ubicaciones desde la referencia**: una llamada barata de visión detecta dónde están
  las ilustraciones y textos de la referencia y los pre-ubica.
- **Duplicar / Quitar** capas (útil para catálogos con una sola foto), **Al frente / Atrás**, flechas
  del teclado para ajuste fino.
- **Dibujar el texto con fuente real**: la IA deja libres las zonas de texto y las letras se componen
  después (ortografía exacta, color y fuente cambiables sin pagar otra generación). Puedes dejar
  fuentes `.ttf/.otf` en `fuentes/` o subirlas desde la app.
- Después de generar: **Recomponer texto** (gratis), **Cambio puntual con IA** sobre el resultado,
  historial de versiones y archivo **2x / 300 ppp** para impresión.
- Todo lo generado se guarda en `salidas/` (ignorada por git).

Si no activas la mesa, la app funciona exactamente como antes.

## Nuevas Funcionalidades y Mejoras Recientes

### ✂️ Vista previa y depuración de recorte del sujeto (Local • 0 tokens)
En el **Paso 2**, cada foto del sujeto subida se procesa localmente con `rembg` (o se respeta su canal alfa si ya venía recortada) y se visualiza en una comparativa interactiva:
- **Vista individual por imagen**: Comparación lado a lado de la foto original vs la silueta recortada (con pestañas para múltiples imágenes).
- **Fondos de inspección**: Alterna entre *Ajedrezado transparente*, *Fondo oscuro (#181818)* para detectar halos claros, *Fondo blanco (#FFFFFF)* para sombras, o *Transparente puro (PNG)*.
- **Depuración ultra rápida (~5ms)**:
  - **Tolerancia / Umbral alfa (1-80)**: Limpia niebla semitransparente o píxeles dispersos.
  - **Reducción de halos / Erosión (0-3px)**: Aplica `ImageFilter.MinFilter(3)` para encoger el perímetro y eliminar el color residual del fondo original.
- **Descarga individual**: Botón para descargar el PNG transparente recortado de cada sujeto.
- **Reutilización instantánea**: Los recortes depurados se transfieren directamente a la mesa y a la generación final sin volver a ejecutar `rembg`.

### 🚫 Diseños sin título principal o 100% libres de texto
En el **Paso 4 (Textos)**, puedes activar la casilla `No incluir título principal`.
- La IA recibe una instrucción estricta que prohíbe inventar títulos, tipografías o palabras grandes, asignando el 100% del espacio a las ilustraciones y ornamentos gráficos.
- Si tampoco ingresas frases secundarias, el diseño se genera completamente libre de letras.
- La mesa de composición y el motor gráfico ajustan automáticamente la cuadrícula hacia la parte superior.

### 🧠 Memoria de aprendizaje persistente (`aprendizajes.json`)
Bajo el diseño generado, encuentras la sección de **Retroalimentación y Aprendizaje**:
- **¿Qué cosas salieron mal en este diseño?**: Permite describir errores (textos inventados, fósiles de la referencia colados, fisonomía incorrecta, etc.).
- **Guardar como aprendizaje permanente**: Guarda la regla en `aprendizajes.json` (montado en Docker en Contabo y local en PC) y la inyecta automáticamente en el prompt maestro de todas las futuras generaciones.
- **Corregir y Regenerar ahora**: Aplica la corrección de forma inmediata con prioridad máxima.
- **Panel en la barra lateral**: Permite activar, desactivar o eliminar reglas aprendidas en cualquier momento.

### 🧹 Botón de Reiniciar y Limpieza de Disco
- Botón en la cabecera y en la barra lateral que restablece el formulario, limpia las fotos subidas, vacía la caché de memoria y opcionalmente borra los archivos temporales generados en `salidas/`.

## Compatibilidad Multiplataforma (PC Windows y Contabo VPS)
Todos los cambios están desarrollados para ejecutarse idénticamente tanto en tu computador local como en servidores Linux bajo Docker (Contabo). Consulta [DESPLIEGUE_CONTABO.md](DESPLIEGUE_CONTABO.md) para detalles de despliegue y actualización.
