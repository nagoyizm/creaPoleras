FROM python:3.11-slim

# Evitar prompts interactivos y buffer de salida
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    U2NET_HOME=/root/.u2net

# Instalar dependencias del sistema requeridas para Pillow, OpenCV y rembg
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar e instalar requerimientos primero para aprovechar la cache de Docker
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código del proyecto
COPY . .

# Exponer el puerto por defecto de Streamlit
EXPOSE 8501

# Healthcheck para verificar que Streamlit responda correctamente
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl --fail http://localhost:8501/_stcore/health || exit 1

# Iniciar la app de Streamlit
CMD ["streamlit", "run", "app_visual.py", "--server.port=8501", "--server.address=0.0.0.0"]
