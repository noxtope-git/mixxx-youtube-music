# Mixxx YouTube Music

Puente para usar **YouTube Music** como fuente de pistas en
[Mixxx](https://mixxx.org). Busca, descarga y carga pistas y *mixes* de
YouTube Music directamente en los decks de Mixxx, desde una **interfaz web**.

> **Aviso legal.** YouTube Music no tiene API pública oficial para esto y sus
> términos de servicio prohíben descargar audio fuera de su app. Úsalo solo con
> contenido que tengas derecho a descargar. El código C++ es derivado de Mixxx
> (GPLv2).

---

## Qué necesitas

| Requisito | Para qué |
|-----------|----------|
| **Python 3.9+** | Ejecutar el puente |
| **ffmpeg** | Decodificar audio / incrustar etiquetas / detectar BPM |
| **Mixxx** | El programa de DJ (instalado o compilado) |

---

## Instalación (paso a paso)

### 1. Instalar dependencias de Python

```powershell
cd mixxx-youtube-music
python -m pip install -r requirements.txt
```

### 2. Instalar ffmpeg

```powershell
winget install --id Gyan.FFmpeg -e
```

> Después de instalar ffmpeg, **cierra y reabre la terminal** para que se
> actualice el `PATH`.

### 3. (Opcional) Autenticar YouTube Music

Para búsqueda nativa y tus playlists personales:

```powershell
python -m pip install ytmusicapi
ytmusicapi oauth
```

> Sin esto, la búsqueda usa YouTube normal vía `yt-dlp` (funciona igual).

### 4. Arrancar la interfaz web

```powershell
python ytmixx.py serve --port 8765
```

Abre **http://127.0.0.1:8765** en tu navegador. Escribe una canción o pega la
URL de un mix y aprieta Enter; cada resultado tiene botones **Deck 1 / Deck 2**,
miniatura y (cuando esté analizada) su BPM, con filtro de BPM.

### 5. (Necesario para que "Deck 1/2" cargue en Mixxx)

Los botones de deck escriben un archivo de comandos que Mixxx solo entiende si
tienes compilada la parte C++ (`ExternalTrackLoader`). Ver la sección
"Integración C++" más abajo.

**Sin la parte C++**: puedes usar `get` (descargar) y arrastrar el `.m4a` de la
carpeta de descargas a un deck manualmente.

---

## Uso por línea de comandos

```powershell
python ytmixx.py search "daft punk"          # buscar
python ytmixx.py get "daft punk"             # descargar y ver la ruta
python ytmixx.py load "daft punk" --deck 1   # descargar + cargar en deck 1
python ytmixx.py mix "<url-de-playlist>"     # descargar un mix entero
python ytmixx.py mix list                    # tus playlists (necesita oauth)
python ytmixx.py cache                       # ver caché
python ytmixx.py cache --clear               # limpiar caché
```

### Configuración (variables de entorno)

| Variable | Default | Descripción |
|----------|---------|-------------|
| `YTMIXX_CACHE_DIR` | `~/Music/Mixxx/YouTube Music` | Carpeta de descargas |
| `YTMIXX_COMMAND_FILE` | `<settings Mixxx>/youtube_load.json` | Archivo de comandos |

---

## Integración C++ (opcional, para carga automática en decks)

Hace que los botones **Deck 1 / Deck 2** (y `load`/`mix`) carguen la pista
directamente en Mixxx. Requiere recompilar Mixxx.

### Archivos

- `src/library/externaltrackloader.{h,cpp}` — vigila `youtube_load.json` y
  llama a `slotLoadLocationToPlayer` (añade la pista a la biblioteca, la analiza
  y la carga en el deck).
- `src/sources/soundsourceyoutubemusic.{h,cpp}` — decodifica `.ytmusic`
  (necesario para arrastrar URLs de YouTube a un deck).
- `integration.patch` — cambios sobre archivos existentes de Mixxx.

### Pasos

1. **Copia los archivos C++** a tu clon de Mixxx:
   ```
   src/library/externaltrackloader.{h,cpp}
   src/sources/soundsourceyoutubemusic.{h,cpp}
   ```
2. **Aplica el parche**:
   ```powershell
   cd <tu-clon-de-mixxx>
   git apply integration.patch
   ```
3. **Recompila Mixxx**. En Windows usa el script incluido:
   ```powershell
   tools\build-youtube.bat
   ```
   (requiere Visual Studio 2022 Build Tools, CMake y Ninja; el script los
   detecta y te avisa si faltan).

---

## Cómo encaja todo

```
Web UI (navegador)  ->  ytmixx.py serve
  Buscar           ->  yt-dlp / ytmusicapi
  Deck 1 / Deck 2  ->  descarga m4a + escribe youtube_load.json
ExternalTrackLoader (C++)  ->  detecta el JSON -> carga la pista en el deck
SoundSourceYouTubeMusic (C++) -> decodifica .ytmusic (arrastrar URLs)
```

## Solución de problemas

- **`load` no carga nada**: comprueba que Mixxx esté abierto (el puente lo
  detecta y te avisa) y que `YTMIXX_COMMAND_FILE` apunte al mismo
  `youtube_load.json` que vigila `ExternalTrackLoader`.
- **Los resultados no muestran BPM**: el BPM se calcula al **descargar** la
  pista por primera vez; después queda en caché. YouTube no lo provee de
  antemano.
- **`ytmusicapi` da 401**: ejecuta `ytmusicapi oauth`.
- **Descarga falla**: `python -m pip install -U yt-dlp`.
- **No suena por los auriculares**: en Mixxx → Preferencias → Sonido, elige
  WASAPI y tu dispositivo de salida (los Bluetooth suelen requerir 48000 Hz).
