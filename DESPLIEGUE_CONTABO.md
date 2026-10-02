# Guía de Despliegue en Contabo (Ubuntu / Debian con Docker)

Esta guía te explica paso a paso cómo subir y dejar corriendo **creaPoleras** en tu VPS de Contabo de forma permanente.

---

## 1. Conectarte a tu VPS de Contabo

Abre una terminal (PowerShell, CMD o Terminal) en tu computadora y conéctate por SSH:

```bash
ssh root@TU_IP_DE_CONTABO
```
*(Reemplaza `TU_IP_DE_CONTABO` con la IP pública de tu servidor de Contabo).*

---

## 2. Instalar Docker y Docker Compose (si aún no los tienes)

En la terminal de tu servidor Contabo, ejecuta el script oficial de instalación de Docker:

```bash
# Actualizar repositorios
apt-get update && apt-get upgrade -y

# Instalar Docker automáticamente
curl -fsSL https://get.docker.com | sh

# Verificar que Docker y Docker Compose estén instalados
docker --version
docker compose version
```

---

## 3. Subir el proyecto al VPS

Tienes dos alternativas fáciles:

### Opción A: Vía Git (Recomendada)
1. En tu máquina local, sube tus últimos cambios a GitHub:
   ```bash
   git add .
   git commit -m "Preparar dockerfile y configuracion de despliegue"
   git push origin main
   ```
2. En tu servidor Contabo:
   ```bash
   cd /root
   git clone https://github.com/nagoyizm/creaPoleras.git
   cd creaPoleras
   ```

### Opción B: Copiar los archivos directamente desde tu PC (vía SCP)
En la consola de tu computadora local (desde la carpeta `d:\programa poleras`):
```bash
scp -r "D:\programa poleras" root@TU_IP_DE_CONTABO:/root/creaPoleras
```

---

## 4. Configurar tu API Key (.env)

En tu servidor, dentro de la carpeta `/root/creaPoleras`:

```bash
# Copia la plantilla
cp .env.example .env

# Edita el archivo con nano
nano .env
```

Pega tu clave de Gemini:
```env
GEMINI_API_KEY=tu_api_key_aqui
```
*Guarda con `Ctrl + O`, presiona `Enter`, y sal con `Ctrl + X`.*

---

## 5. Levantar la aplicación con Docker Compose

Ejecuta el siguiente comando para construir la imagen y arrancar el contenedor en segundo plano:

```bash
docker compose up -d --build
```

> **Nota:** La primera vez demorará unos minutos mientras descarga las librerías de Python e instala dependencias como OpenCV y rembg.

---

## 6. Abrir el puerto en el Firewall (UFW)

Para que puedas acceder desde tu navegador, asegúrate de que el puerto `8501` esté permitido:

```bash
ufw allow 8501/tcp
ufw reload
```

*(Si utilizas algún panel de Cloud Firewall dentro del panel de cliente de Contabo, asegúrate de permitir el puerto 8501 TCP de entrada).*

---

## 7. Acceder a tu aplicación

Abre cualquier navegador web y entra a:

```text
http://TU_IP_DE_CONTABO:8501
```

¡Listo! Ya tienes la interfaz de Streamlit funcionando en la nube 24/7.

---

## 8. Comandos útiles de mantenimiento

- **Ver logs en tiempo real:**
  ```bash
  docker compose logs -f
  ```
- **Reiniciar la app:**
  ```bash
  docker compose restart
  ```
- **Detener la app:**
  ```bash
  docker compose down
  ```
- **Actualizar a la última versión (cuando subas cambios a GitHub):**
  ```bash
  git pull
  docker compose up -d --build
  ```

---

## 9. (Opcional) Configurar Dominio y HTTPS con Nginx

Si tienes un dominio (ej: `poleras.tudominio.com`) apuntando a la IP de Contabo y quieres HTTPS gratis:

1. Instala Nginx y Certbot:
   ```bash
   apt-get install -y nginx certbot python3-certbot-nginx
   ```
2. Crea el archivo de configuración de Nginx:
   ```bash
   nano /etc/nginx/sites-available/creapoleras
   ```
3. Pega la configuración con soporte de WebSockets para Streamlit:
   ```nginx
   server {
       listen 80;
       server_name poleras.tudominio.com;

       location / {
           proxy_pass http://127.0.0.1:8501;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
           proxy_read_timeout 86400;
       }
   }
   ```
4. Actívalo y reinicia Nginx:
   ```bash
   ln -s /etc/nginx/sites-available/creapoleras /etc/nginx/sites-enabled/
   nginx -t && systemctl restart nginx
   ```
5. Obtén el certificado SSL gratis:
   ```bash
   certbot --nginx -d poleras.tudominio.com
   ```
