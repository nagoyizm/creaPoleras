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

En la interfaz, el paso **4.2** (opcional) abre un lienzo con la referencia de fondo (tenue) y tus
imágenes ya recortadas. Arrástralas, escálalas y rótalas donde quieras; el título y las frases son
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
