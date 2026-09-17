# Mixxx YouTube Music

Puente e integración (opcional) para usar **YouTube Music** como fuente de
pistas en [Mixxx](https://mixxx.org), el software de DJ libre. Permite buscar,
descargar y cargar pistas y *mixes* de YouTube Music en los decks de Mixxx.

## Componentes

| Pieza | Tipo | Qué hace | ¿Recompilar Mixxx? |
|-------|------|----------|--------------------|
| `ytmixx.py` | Python | Buscar, descargar y pedir carga de pistas/mixes | No |
| `src/library/externaltrackloader.{h,cpp}` | C++ | Vigila un archivo JSON y carga la pista en un deck | Sí |
| `src/sources/soundsourceyoutubemusic.{h,cpp}` | C++ | Decodifica pistas `.ytmusic` de forma perezosa + arrastrar URLs | Sí |

> **Aviso legal y técnico.** YouTube Music no tiene API pública oficial para
> esto y sus términos de servicio prohíben descargar/extraer audio fuera de su
> app. Proyecto **personal/experimental**: úsalo solo con contenido que tengas
> derecho a descargar, y ten en cuenta que puede romperse cuando YouTube cambie
> sus endpoints. El código C++ es derivado de Mixxx (GPLv2).

---

## 1. Puente Python (sin recompilar)

### Instalación

```powershell
python -m pip install -r requirements.txt

# ffmpeg (necesario para incrustar etiquetas y algunos formatos)
winget install --id Gyan.FFmpeg -e
```

> `ytmusicapi` (búsqueda nativa y tus playlists de YouTube Music) necesita
> autenticación. Ejecuta `ytmusicapi oauth` y sigue los pasos. Sin ello, se usa
> la búsqueda de YouTube normal vía `yt-dlp`.

### Uso

```powershell
# Buscar
python ytmixx.py search "daft punk around the world"

# Descargar y ver la ruta local
python ytmixx.py get "daft punk around the world"

# Descargar y cargar en el deck 2 con autoplay
python ytmixx.py load "daft punk" --deck 2 --autoplay

# Descargar una playlist/mix entera y cargarla en decks sucesivos
python ytmixx.py mix "https://music.youtube.com/playlist?list=PL..." --start-deck 1

# Listar tus playlists personales (requiere `ytmusicapi oauth`)
python ytmixx.py mix list

# Interfaz web local
python ytmixx.py serve --port 8765

# Caché
python ytmixx.py cache
python ytmixx.py cache --clear
```

### Configuración

| Variable | Default | Descripción |
|----------|---------|-------------|
| `YTMIXX_CACHE_DIR` | `~/Music/Mixxx/YouTube Music` | Carpeta de descargas |
| `YTMIXX_COMMAND_FILE` | `<settings Mixxx>/youtube_load.json` | Archivo de comandos |

---

## 2. Integración completa (C++)

Requiere recompilar Mixxx. Los archivos nuevos están en `src/`; las
modificaciones a archivos existentes de Mixxx están en `integration.patch`.

### Pasos

1. **Copia los archivos C++** a tu árbol de Mixxx:
   ```
   src/library/externaltrackloader.{h,cpp}
   src/sources/soundsourceyoutubemusic.{h,cpp}
   ```
2. **Aplica el parche** que modifica `CMakeLists.txt`, `src/sources/soundsourceproxy.cpp`,
   `src/coreservices.{h,cpp}` y `src/util/dnd.cpp`:
   ```powershell
   cd <tu-clon-de-mixxx>
   git apply integration.patch
   ```
3. **Recompila Mixxx** con tu flujo habitual (Qt 6 + vcpkg).

### Qué aporta cada pieza

- **`ExternalTrackLoader`**: vigila `youtube_load.json` en la carpeta de
  settings de Mixxx y, al detectar un cambio, llama a
  `PlayerManager::slotLoadLocationToPlayer(...)`. Esto añade la pista a la
  biblioteca, dispara su análisis (BPM/clave) y la carga en el deck. Soporta
  una pista única o una lista `tracks` (mixes).

- **`SoundSourceProviderYouTubeMusic`**: registra la extensión `.ytmusic`.
  Un archivo `.ytmusic` es un *sidecar* (nombre = ID del vídeo, o archivo de
  texto con la URL). Al abrirlo, descarga el audio con `yt-dlp` a una caché y
  delega la decodificación en FFmpeg, manteniendo seek/loops/keylock.

- **Arrastrar y soltar URLs**: con el parche aplicado, puedes **arrastrar un
  enlace de YouTube / YouTube Music y soltarlo directamente en un deck**; Mixxx
  crea el *sidecar* y lo carga de forma perezosa (`src/util/dnd.cpp`).

---

## Cómo encaja todo

```
ytmixx.py search "..."           -> lista resultados
ytmixx.py load "..." --deck 1    -> yt-dlp descarga m4a a la caché
                                    escribe youtube_load.json (atómico)
ytmixx.py mix "<playlist>"       -> descarga todas las pistas
                                    escribe {"tracks":[...]} (decks sucesivos)

ExternalTrackLoader (C++)        -> detecta el cambio del JSON
                                    -> PlayerManager::slotLoadLocationToPlayer()
SoundSourceYouTubeMusic (C++)    -> (opcional) decodifica .ytmusic bajo demanda
dnd.cpp                          -> acepta URLs de YouTube arrastradas al deck
```

## Solución de problemas

- **`load` no carga nada**: verifica que `YTMIXX_COMMAND_FILE` apunta al mismo
  `youtube_load.json` que vigila `ExternalTrackLoader` (en la carpeta de
  settings de Mixxx).
- **`ytmusicapi` da 401**: ejecuta `ytmusicapi oauth`; si no, usa el fallback de
  búsqueda de YouTube.
- **Descarga falla**: actualiza `python -m pip install -U yt-dlp`.
- **El `.ytmusic` no decodifica**: comprueba que `ffmpeg` y `yt-dlp` están en el
  `PATH` y que Mixxx se compiló con `__FFMPEG__`.
