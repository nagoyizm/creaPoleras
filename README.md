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
