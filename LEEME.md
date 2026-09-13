# Generador de diseños de poleras — prototipo de prueba

## Instalación

```bash
cd poleras-generator
python -m venv venv
source venv/bin/activate   # en Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Configurar tu API key

```bash
cp .env.example .env
```

Abre `.env` y pega tu API key de Google AI Studio (ai.google.dev).

## Preparar imágenes de prueba

Pon en esta misma carpeta:
- `referencia.jpg` → una foto/captura de una polera existente que te guste como estructura
- `personaje.jpg` → la foto del personaje que quieres usar

## Correr con interfaz visual (recomendado)

```bash
python -m streamlit run app_visual.py
```

Esto abre una pestaña en tu navegador (todo local, en tu propio computador) con
un formulario: subir imagen(es) de referencia, subir imagen(es) del personaje,
checkbox de recorte, colores (o checkbox de usar los de la referencia), fondo,
título y frases, y al final el selector de modelo (Lite/Media/Pro). Apretas
"Generar diseño" y te muestra el resultado ahí mismo, con botón de descarga.

## Correr por línea de comandos (alternativa sin interfaz)

```bash
python generador.py
```

Va a imprimir el progreso paso a paso y al final te deja `resultado.png` en la carpeta.
Para esta modalidad sí tienes que editar los valores directamente en el archivo
`generador.py`, en la sección `if __name__ == "__main__":`.

## Qué editar para tus pruebas

Al final del archivo `generador.py`, dentro de `if __name__ == "__main__":`,
puedes cambiar:
- `colores` → lista de colores en hexadecimal
- `titulo` y `tamano_titulo` → "grande", "mediano" o "chico"
- `frases_sueltas` → lista de frases chicas, o `[]` si no quieres ninguna
- `recortar_personaje` → `True` para quitar el fondo, `False` para usar la foto completa

## Abrir sin usar la consola manualmente

Ya dejé un archivo `Abrir Generador de Poleras.bat` en la carpeta. Haz doble
clic ahí en vez de escribir comandos: activa el venv solo y abre la app en el
navegador. Puedes crear un acceso directo a ese `.bat` en tu escritorio
(clic derecho → Enviar a → Escritorio) para que quede como un "programa" normal.

Esto sigue abriendo una ventana negra de consola detrás (es necesaria, ahí vive
el servidor de la app mientras la usas) — no es un .exe empaquetado de verdad,
pero elimina por completo el tener que escribir comandos cada vez.

Si más adelante quieres un .exe real sin ventana de consola visible, la vía es
empaquetar con PyInstaller, pero Streamlit específicamente da varios dolores de
cabeza al empaquetarse (por cómo maneja sus propios procesos internos) — para
llegar a ese nivel de pulido, en ese punto conviene directamente migrar la
interfaz a la app de escritorio en Tauri de la que hablamos al principio, que
sí compila a un .exe nativo limpio.

## Múltiples fotos del personaje

Ahora puedes subir más de una foto del mismo personaje en la interfaz visual
(arrastra o selecciona varios archivos a la vez en el paso 2). Todas se le
pasan juntas a Gemini para que entienda mejor su apariencia desde distintos
ángulos. El checkbox de "recortar personaje" se aplica a todas por igual.

## Colores, fondo y poses del personaje

- Checkbox "usar colores de la referencia": si lo activas, el diseño replica
  la paleta de colores de la imagen de referencia (Gemini la analiza y la
  imita). Si lo dejas apagado, tú eliges los colores manualmente.
- Los colores manuales ahora se agregan de a uno con el botón "+ Agregar
  color" (puedes tener 1, 2, 3 o los que necesites), y cada uno se puede
  quitar con el botón "✕".
- Fondo del diseño: por defecto queda transparente. Como Gemini no puede
  entregar canal alfa (transparencia real) aunque se lo pidas, el flujo le
  pide que use un fondo sólido de buen contraste y DESPUÉS se lo quita
  automáticamente con rembg (la misma herramienta que se usa para el
  personaje). Si en cambio activas "quiero un color de fondo sólido
  específico", ese post-proceso se salta y el fondo queda tal cual lo
  generó Gemini.
- Si la referencia sugiere varias instancias del personaje (collage), el
  prompt ahora le pide explícitamente a Gemini que varíe la pose/ángulo en
  cada una, para que no salga repetida.

## Fuentes del título

Se volvió a la generación directa: el título y las frases sueltas los dibuja
Gemini como parte del diseño (se quitó el sistema de fuentes superpuestas
porque los resultados salían feos). Si notas que el texto sale mal escrito o
deformado seguido, prueba regenerar, simplificar el título, o subir de modelo
(Media o Pro suelen acertar mejor el texto que Lite).

## Selector de modelo (Lite / Media / Pro)

Último paso antes de generar: eliges qué tan barato o bueno quieres el
resultado.
- **Lite** (`gemini-3.1-flash-lite-image`) — el más barato, ideal para probar
  variantes rápido mientras afinas el prompt/estilo.
- **Media** (`gemini-3.1-flash-image`) — balance entre calidad y precio.
- **Pro** (`gemini-3-pro-image`) — mejor calidad, más caro por imagen.

Recomendación: prueba y afina con Lite, y solo pasa a Media o Pro cuando ya
tengas el diseño casi como lo quieres, para no gastar de más iterando.

## Múltiples imágenes de referencia

Ahora también puedes subir más de una polera de referencia a la vez (paso 1).
Si subes varias, Gemini las analiza en conjunto y arma una sola estructura
combinada con lo común entre ellas, en vez de copiar una sola al azar.

## Formatos de imagen aceptados

La interfaz visual acepta JPG, PNG, WEBP y AVIF. WEBP lo lee Pillow de forma
nativa; AVIF necesita el paquete extra `pillow-avif-plugin` (ya está en
`requirements.txt`, se instala solo).

## Notas

- La primera llamada (analizar referencia) es prácticamente gratis.
- La segunda llamada (generar la imagen final) tiene costo por imagen — revisa
  ai.google.dev/pricing para el valor vigente del modelo que estés usando.
- Si el SDK tira error de método no encontrado, es probable que Google haya
  actualizado el paquete `google-genai` — revisa la documentación oficial para
  ajustar los nombres de método/modelo.
