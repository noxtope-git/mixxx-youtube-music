#!/usr/bin/env python3
"""
ytmixx — YouTube Music bridge for Mixxx.

Search YouTube Music, download tracks to a local cache, and load them into a
Mixxx deck automatically (requires the optional C++ `ExternalTrackLoader`
module, or you can load the downloaded files manually from the library).

Commands:
    search  <query>            List matching tracks.
    get     <query|url|id>     Download a track and print its local path.
    load    <query|url|id>     Download + send a load command to Mixxx.
    mix     <url|id>           Download a playlist/mix and load it into decks.
    mix     list               List your YouTube Music playlists (needs auth).
    serve   [--port N]         Start a tiny local web UI (search + load).
    cache   [--clear]          Show or clear the download cache.

Examples:
    python ytmixx.py search "daft punk around the world"
    python ytmixx.py load "daft punk around the world" --deck 1 --autoplay
    python ytmixx.py get "https://music.youtube.com/watch?v=dQw4w9WgXcQ"
    python ytmixx.py mix "https://music.youtube.com/playlist?list=PL..." --start-deck 1
    python ytmixx.py serve --port 8765

Options (available before or after the subcommand):
    --command-file PATH   Override the load-command JSON path.
    YTMIXX_CACHE_DIR      Override the download cache directory.
    YTMIXX_COMMAND_FILE   Override the load-command JSON path.

Disclaimer: only use with content you have the rights to download. YouTube
Music has no official API for this and may change at any time.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

try:
    import yt_dlp
except ImportError:
    sys.exit("Falta yt-dlp. Instalalo con:  python -m pip install yt-dlp")

# Optional: nicer search results straight from YouTube Music.
try:
    from ytmusicapi import YTMusic
except Exception:  # pragma: no cover - optional dependency
    YTMusic = None


# --------------------------------------------------------------------------- #
# Paths / config
# --------------------------------------------------------------------------- #

def _default_cache_dir() -> Path:
    env = os.environ.get("YTMIXX_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Music" / "Mixxx" / "YouTube Music"


def _default_command_file() -> Path:
    env = os.environ.get("YTMIXX_COMMAND_FILE")
    if env:
        return Path(env).expanduser()
    home = Path.home()
    if sys.platform == "win32":
        localappdata = os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local"))
        return Path(localappdata) / "Mixxx" / "youtube_load.json"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Mixxx" / "youtube_load.json"
    return home / ".mixxx" / "youtube_load.json"


CACHE_DIR = _default_cache_dir()
COMMAND_FILE = _default_command_file()

# A YouTube video ID is exactly 11 chars of [A-Za-z0-9_-].
_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
_URL_ID_RE = re.compile(
    r"(?:youtube\.com|youtu\.be|music\.youtube\.com)/.*[?&]v=([A-Za-z0-9_-]{11})"
)


def _extract_id(text: str) -> Optional[str]:
    """Return a bare video ID if `text` looks like one or a YouTube URL."""
    m = _URL_ID_RE.search(text)
    if m:
        return m.group(1)
    if re.fullmatch(_ID_RE, text):
        return text
    return None


def _sanitize(name: str) -> str:
    """Make a string safe for use in a filename."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().strip(".")
    return name[:160] or "unknown"


def _cache_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def is_mixxx_running() -> bool:
    """Return True if Mixxx is currently running.

    Loading a track into a deck requires Mixxx to be open (the
    ExternalTrackLoader module inside Mixxx watches the command file).
    If it isn't running the command would be silently lost or stale.
    """
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                    ["tasklist", "/FI", "IMAGENAME eq mixxx.exe", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=5)
            return r.returncode == 0 and "mixxx.exe" in r.stdout.lower()
        except Exception:
            return False
    try:
        r = subprocess.run(
                ["pgrep", "-x", "mixxx"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _thumbnail_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


# --- BPM detection + cache ----------------------------------------------------

_bpm_cache: Optional[dict] = None


def _bpm_cache_path() -> Path:
    return _cache_dir() / "bpm_cache.json"


def _get_bpm_cache() -> dict:
    global _bpm_cache
    if _bpm_cache is None:
        p = _bpm_cache_path()
        if p.exists():
            try:
                _bpm_cache = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                _bpm_cache = {}
        else:
            _bpm_cache = {}
    return _bpm_cache


def _save_bpm_cache() -> None:
    try:
        _bpm_cache_path().write_text(json.dumps(_get_bpm_cache()), encoding="utf-8")
    except Exception:
        pass


def get_cached_bpm(video_id: str) -> Optional[float]:
    return _get_bpm_cache().get(video_id)


def set_cached_bpm(video_id: str, bpm: Optional[float]) -> None:
    if bpm is None:
        return
    _get_bpm_cache()[video_id] = bpm
    _save_bpm_cache()


def _detect_bpm(path: Path) -> Optional[float]:
    """Estimate BPM from the first minute of audio via onset autocorrelation."""
    try:
        import numpy as np
    except ImportError:
        return None
    if not shutil.which("ffmpeg"):
        return None
    try:
        proc = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(path),
                 "-f", "f32le", "-ac", "1", "-ar", "22050", "-t", "60", "-"],
                capture_output=True,
                timeout=120)
    except Exception:
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        samples = np.frombuffer(proc.stdout, dtype=np.float32)
    except Exception:
        return None
    sr = 22050
    hop = 1024
    n = (samples.size // hop) * hop
    if n < sr * 5:  # need at least ~5 seconds of audio
        return None
    frames = samples[:n].reshape(-1, hop)
    energy = (frames * frames).mean(axis=1)
    onset = np.diff(energy)
    onset[onset < 0] = 0
    if onset.sum() == 0:
        return None
    onset = onset - onset.mean()
    corr = np.correlate(onset, onset, mode="full")
    corr = corr[corr.size // 2:]
    frame_rate = sr / hop
    lo = int(frame_rate * 60 / 200)  # 200 BPM (shortest period)
    hi = int(frame_rate * 60 / 60)   # 60 BPM (longest period)
    if hi >= corr.size:
        hi = corr.size - 1
    if lo >= hi:
        return None
    best = lo + int(np.argmax(corr[lo:hi]))
    if best <= 0:
        return None
    bpm = frame_rate * 60 / best
    # Collapse common half/double-tempo errors into a sane range.
    while bpm > 190:
        bpm /= 2
    while bpm < 65:
        bpm *= 2
    return round(float(bpm), 1)


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

def _search_ytmusic(query: str, limit: int) -> list:
    """Search using ytmusicapi (needs auth: `ytmusicapi oauth`)."""
    if YTMusic is None:
        return []
    try:
        yt = YTMusic()
        raw = yt.search(query, filter="songs", limit=limit)
    except Exception:
        return []
    out = []
    for item in raw:
        vid = item.get("videoId")
        if not vid:
            continue
        artists = ", ".join(a.get("name", "") for a in item.get("artists", []))
        out.append({
            "id": vid,
            "title": item.get("title") or "Unknown",
            "artist": artists,
            "duration": item.get("duration"),
        })
    return out


def _search_ytdlp(query: str, limit: int) -> list:
    """Fallback search using yt-dlp (regular YouTube search)."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    entries = info.get("entries") or []
    out = []
    for e in entries:
        if not e:
            continue
        out.append({
            "id": e.get("id"),
            "title": e.get("title") or "Unknown",
            "artist": e.get("channel") or e.get("uploader") or "",
            "duration": e.get("duration"),
        })
    return out


def search(query: str, limit: int = 10) -> list:
    results = _search_ytmusic(query, limit)
    if not results:
        results = _search_ytdlp(query, limit)
    return results[:limit]


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #

def _embed_tags(path: Path, title: str, artist: str) -> None:
    """Best-effort: embed title/artist tags using ffmpeg (in place, no re-encode)."""
    if not shutil.which("ffmpeg"):
        return
    # Keep the original extension on the temp file so ffmpeg can pick a muxer.
    tmp = path.with_name(path.stem + ".embed" + path.suffix)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(path), "-map", "0", "-c", "copy",
        "-metadata", f"title={title}",
        "-metadata", f"artist={artist}",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=120)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)


def download(identifier: str, embed: bool = True) -> Optional[dict]:
    """Download audio for `identifier` (query / URL / ID) and return info."""
    cache = _cache_dir()
    # If it's not a URL or bare video ID, treat it as a search query and
    # download the top result.
    target = identifier if _extract_id(identifier) else f"ytsearch1:{identifier}"
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(cache / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "paths": {"home": str(cache)},
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 60,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(target, download=True)
            if info.get("_type") == "playlist":
                entries = info.get("entries") or []
                info = entries[0] if entries else {}
            path = Path(ydl.prepare_filename(info))
    except yt_dlp.utils.DownloadError as e:
        print(f"Error de descarga: {e}", file=sys.stderr)
        return None
    if not path.exists():
        # ext may have differed (e.g. webm fallback) -> glob by id.
        matches = list(cache.glob(f"{info.get('id', '*')}.*"))
        path = matches[0] if matches else None
    if not path:
        return None
    title = info.get("title") or path.stem
    artist = (info.get("artist") or info.get("uploader") or info.get("channel") or "")
    if embed:
        _embed_tags(path, title, artist)
    video_id = info.get("id") or ""
    bpm = get_cached_bpm(video_id)
    if bpm is None:
        bpm = _detect_bpm(path)
        set_cached_bpm(video_id, bpm)
    return {
        "id": video_id,
        "title": title,
        "artist": artist,
        "path": str(path),
        "duration": info.get("duration"),
        "bpm": bpm,
        "thumbnail": _thumbnail_url(video_id),
    }


# --------------------------------------------------------------------------- #
# Load command (ExternalTrackLoader)
# --------------------------------------------------------------------------- #

def _deck_group(deck: int) -> str:
    return f"[Channel{max(1, deck)}]"


def write_load_command(path: str, deck: int = 1, autoplay: bool = False,
                       group: Optional[str] = None,
                       command_file: Optional[Path] = None) -> Path:
    """Write the command JSON consumed by Mixxx's ExternalTrackLoader."""
    cf = command_file or COMMAND_FILE
    cf.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "path": str(path),
        "group": group or _deck_group(deck),
        "autoplay": bool(autoplay),
    }
    # Atomic write (temp + replace) so the watcher never sees a partial file.
    fd, tmp = tempfile.mkstemp(dir=str(cf.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, cf)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return cf


def load(identifier: str, deck: int = 1, autoplay: bool = False) -> Optional[dict]:
    info = download(identifier)
    if not info:
        return None
    write_load_command(info["path"], deck=deck, autoplay=autoplay)
    return info


# --------------------------------------------------------------------------- #
# Playlists / mixes
# --------------------------------------------------------------------------- #

def _is_playlist_url(text: str) -> bool:
    return "list=" in text or "/playlist?" in text or "/sets/" in text


def mix_tracks(identifier: str, limit: int = 0) -> list:
    """Return the list of tracks (id, title, artist, duration) in a playlist/mix.

    Works with YouTube Music playlists, personal playlists AND the
    auto-generated "Mix - ..." / radio playlists made by YouTube.
    """
    if not identifier.startswith(("http://", "https://")):
        # Bare playlist ID -> build a YouTube Music URL.
        identifier = f"https://music.youtube.com/playlist?list={identifier}"

    # YouTube "Mix"/radio lists (list=RD<id>) are only reachable via the watch
    # URL. Convert "playlist?list=RD<id>" -> "watch?v=<id>&list=RD<id>".
    m = re.search(r"list=(RD[A-Za-z0-9_-]{11})", identifier)
    if m and "watch?" not in identifier:
        vid = m.group(1)[2:]
        identifier = f"https://www.youtube.com/watch?v={vid}&list={m.group(1)}"

    opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(identifier, download=False)
    except yt_dlp.utils.DownloadError:
        return []
    entries = info.get("entries") or []
    out = []
    for e in entries:
        if not e or not e.get("id"):
            continue
        out.append({
            "id": e.get("id"),
            "title": e.get("title") or "Unknown",
            "artist": e.get("channel") or e.get("uploader") or "",
            "duration": e.get("duration"),
        })
        if limit and len(out) >= limit:
            break
    return out


def list_personal_playlists(limit: int = 50) -> list:
    """List the user's YouTube Music playlists (requires `ytmusicapi oauth`)."""
    if YTMusic is None:
        return []
    try:
        yt = YTMusic()
        raw = yt.get_library_playlists(limit=limit)
    except Exception:
        return []
    return [{
        "id": p.get("playlistId"),
        "title": p.get("title") or "Unknown",
        "count": p.get("count") or p.get("trackCount"),
    } for p in raw if p.get("playlistId")]


def write_tracks_command(tracks: list, command_file: Optional[Path] = None) -> Path:
    """Write a multi-track command JSON (loads into successive decks)."""
    cf = command_file or COMMAND_FILE
    cf.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "tracks": tracks}
    fd, tmp = tempfile.mkstemp(dir=str(cf.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, cf)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return cf


def mix(identifier: str, start_deck: int = 1, limit: int = 0) -> Optional[list]:
    """Download every track of a playlist/mix and queue them into decks."""
    tracks = mix_tracks(identifier)
    if limit > 0:
        tracks = tracks[:limit]
    if not tracks:
        return None
    commands = []
    infos = []
    for i, t in enumerate(tracks):
        info = download(t["id"])
        if not info:
            continue
        commands.append({"path": info["path"], "group": _deck_group(start_deck + i)})
        infos.append(info)
    if commands:
        write_tracks_command(commands)
    return infos


# --------------------------------------------------------------------------- #
# Playlists (guardar canciones con su info + BPM)
# --------------------------------------------------------------------------- #

_playlists: Optional[dict] = None


def _playlists_path() -> Path:
    return _cache_dir() / "playlists.json"


def _get_playlists() -> dict:
    global _playlists
    if _playlists is None:
        p = _playlists_path()
        if p.exists():
            try:
                _playlists = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                _playlists = {}
        else:
            _playlists = {}
    return _playlists


def _save_playlists() -> None:
    try:
        _playlists_path().write_text(
                json.dumps(_get_playlists(), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def list_playlists() -> list:
    return [{"name": n, "count": len(t)}
            for n, t in _get_playlists().items()]


def get_playlist_tracks(name: str) -> list:
    return _get_playlists().get(name, [])


def add_track_to_playlist(name: str, track: dict) -> None:
    pl = _get_playlists()
    tracks = pl.setdefault(name, [])
    # Replace any existing entry with the same video id.
    pl[name] = [t for t in tracks if t.get("id") != track.get("id")]
    pl[name].append(track)
    _save_playlists()


def remove_track_from_playlist(name: str, track_id: str) -> None:
    pl = _get_playlists()
    if name in pl:
        pl[name] = [t for t in pl[name] if t.get("id") != track_id]
        _save_playlists()


def save_track_to_playlist(name: str, video_id: str) -> Optional[dict]:
    """Download a track (with BPM) and store it in a playlist."""
    info = download(video_id)
    if not info:
        return None
    entry = {
        "id": info.get("id"),
        "title": info.get("title"),
        "artist": info.get("artist"),
        "duration": info.get("duration"),
        "bpm": info.get("bpm"),
        "thumbnail": info.get("thumbnail"),
        "path": info.get("path"),
    }
    add_track_to_playlist(name, entry)
    return entry


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _print_results(results: list) -> None:
    for i, r in enumerate(results):
        dur = r.get("duration")
        dur_s = f"{dur // 60}:{dur % 60:02d}" if isinstance(dur, int) else "?:??"
        print(f"[{i:2d}] {r['title']}  -  {r['artist']}  ({dur_s})  id={r['id']}")


def _fmt_dur(seconds) -> str:
    if not isinstance(seconds, int):
        return "?:??"
    return f"{seconds // 60}:{seconds % 60:02d}"


def cmd_search(args) -> int:
    results = search(args.query, args.limit)
    if not results:
        print("Sin resultados.")
        return 1
    _print_results(results)
    return 0


def cmd_get(args) -> int:
    info = download(args.query, embed=not args.no_embed)
    if not info:
        print("Error al descargar.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(info, ensure_ascii=False))
    else:
        print(info["path"])
    return 0


def cmd_load(args) -> int:
    if not is_mixxx_running():
        print("Error: Mixxx no esta abierto. Abre Mixxx antes de cargar pistas.", file=sys.stderr)
        return 1
    info = load(args.query, deck=args.deck, autoplay=args.autoplay)
    if not info:
        print("Error al descargar.", file=sys.stderr)
        return 1
    print(f"Cargado en {_deck_group(args.deck)}: {info['title']} - {info['artist']}")
    print(f"  archivo: {info['path']}")
    print(f"  comando: {COMMAND_FILE}")
    return 0


def cmd_mix(args) -> int:
    if args.query == "list":
        pls = list_personal_playlists()
        if not pls:
            print("Sin playlists (ejecuta 'ytmusicapi oauth' para autenticarte).")
            return 1
        for i, p in enumerate(pls):
            print(f"[{i:2d}] {p['title']}  ({p['count']} pistas)  id={p['id']}")
        print("\nPara descargar una:  ytmixx.py mix <id>")
        return 0
    if not is_mixxx_running():
        print("Error: Mixxx no esta abierto. Abre Mixxx antes de cargar pistas.", file=sys.stderr)
        return 1
    infos = mix(args.query, start_deck=args.start_deck, limit=args.limit)
    if not infos:
        print("No se pudo descargar la playlist/mix.", file=sys.stderr)
        return 1
    n = len(infos)
    print(f"Mix descargado: {n} pistas -> decks {args.start_deck}..{args.start_deck + n - 1}")
    for i, info in enumerate(infos):
        print(f"  {_deck_group(args.start_deck + i)}: {info['title']} - {info['artist']}")
    print(f"  comando: {COMMAND_FILE}")
    return 0


def cmd_cache(args) -> int:
    cache = _cache_dir()
    files = list(cache.glob("*"))
    total = sum(f.stat().st_size for f in files if f.is_file())
    if args.clear:
        for f in files:
            if f.is_file():
                f.unlink()
        print(f"Cache limpiada ({len(files)} archivos).")
        return 0
    print(f"Cache: {cache}")
    print(f"{len(files)} archivos, {total / 1_048_576:.1f} MB")
    for f in sorted(files):
        if f.is_file():
            print(f"  {f.name}")
    return 0


# --------------------------------------------------------------------------- #
# Minimal local web UI (serve)
# --------------------------------------------------------------------------- #

_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>ytmixx</title>
<style>
body{font-family:system-ui,sans-serif;max-width:720px;margin:1.5rem auto;padding:0 1rem;background:#111;color:#eee}
h1{font-size:1.4rem;margin:.3rem 0;color:#f90}
.banner{padding:.7rem 1rem;border-radius:6px;margin:.6rem 0;font-weight:bold;text-align:center;font-size:1rem}
.banner.on{background:#0a7a33;color:#fff}
.banner.off{background:#b33030;color:#fff}
.section{margin:1rem 0}
.section h2{font-size:1rem;color:#f90;border-bottom:1px solid #333;padding-bottom:.2rem;margin:.4rem 0}
input,select,button{font-size:.95rem;padding:.5rem;border-radius:4px;border:0}
input{width:100%;box-sizing:border-box;background:#222;color:#eee;margin:.3rem 0}
select{flex:1;background:#222;color:#eee}
.btn{background:#f90;color:#111;cursor:pointer;font-weight:bold}
.deck1{background:#e91e63;color:#fff;cursor:pointer}
.deck2{background:#2196f3;color:#fff;cursor:pointer}
.box{display:flex;gap:.4rem;margin:.3rem 0}.box button{white-space:nowrap}
.filter{display:flex;gap:.4rem;margin:.4rem 0;align-items:center}
.filter input{width:75px;margin:0}
.filter label{color:#aaa;font-size:.85rem;white-space:nowrap}
.result{display:flex;justify-content:space-between;align-items:center;padding:.5rem;border-bottom:1px solid #333;gap:.5rem}
.result .thumb{width:56px;height:56px;object-fit:cover;border-radius:4px;flex:0 0 56px}
.result .meta{flex:1;min-width:0}
.result .meta b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:.95rem}
.result .meta small{color:#aaa;font-size:.8rem}
.result .bpm{display:inline-block;margin-top:2px;padding:1px 6px;border-radius:3px;background:#333;color:#9cf;font-size:.75rem;font-weight:bold}
.result .bpm.none{color:#666}
.result .actions{display:flex;gap:.3rem}
.result .actions button{padding:.3rem .5rem}
.like{background:transparent;border:1px solid #f66;color:#f66;cursor:pointer;font-size:1rem;border-radius:4px;padding:.3rem .5rem}
.like:hover{background:#f66;color:#fff}
.msg{padding:.5rem;margin:.3rem 0;border-radius:4px}
.msg.ok{color:#0f0}.msg.err{color:#f66}
.count{color:#aaa;font-size:.85rem;margin:.3rem 0}
.plhead{color:#f90;font-weight:bold;margin:.4rem 0}
#moreBtn,#lessBtn{flex:1;margin:0}
#playlists .plitem{margin:.3rem 0}
#playlists .track{display:flex;align-items:center;gap:.5rem;padding:.4rem;border-bottom:1px solid #333}
#playlists .track .thumb{width:40px;height:40px;object-fit:cover;border-radius:3px;flex:0 0 40px}
#playlists .track .meta{flex:1;min-width:0}
#playlists .track .meta b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:.9rem}
#playlists .track .meta small{color:#aaa;font-size:.78rem}
#playlists .track button{padding:.25rem .5rem;font-size:.75rem}
</style></head>
<body>
<h1>ytmixx &mdash; YouTube Music</h1>

<div id="status" class="banner off">Verificando Mixxx...</div>

<div class="section">
  <h2>Buscar</h2>
  <input id="q" placeholder="Cancion o URL de mix/playlist..." onkeydown="if(event.key==='Enter')doGo()" disabled>
  <div class="box"><button id="goBtn" class="btn" onclick="doGo()" disabled>Buscar</button></div>
</div>

<div class="section">
  <h2>Filtros</h2>
  <div class="filter">
    <label>BPM</label>
    <input id="bpmMin" type="number" placeholder="min">
    <label>-</label>
    <input id="bpmMax" type="number" placeholder="max">
    <button class="btn" onclick="doFilter()">Filtrar</button>
    <button class="btn" onclick="clearFilter()">Limpiar</button>
  </div>
  <div class="filter">
    <label>Guardar en</label>
    <select id="playlistSel"><option>Favoritos</option></select>
    <button class="btn" onclick="newPlaylist()">+ Nueva</button>
  </div>
</div>

<div class="section">
  <h2>Resultados</h2>
  <div id="out"></div>
  <div class="box" style="margin:.4rem 0">
    <button id="lessBtn" class="btn" style="display:none;flex:1" onclick="doLess()">Mostrar menos</button>
    <button id="moreBtn" class="btn" style="display:none;flex:1" onclick="doMore()">Mostrar mas</button>
  </div>
</div>

<div class="section">
  <h2>Mis playlists</h2>
  <div id="playlists"></div>
</div>

<script>
var curQuery='',curLimit=10,curMix=false,curTracks=[],curPlaylistName='';
var allResults=[],visibleCount=10,MAX_RESULTS=400,STEP=25;
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function isMix(q){
  q=q.trim();
  if(!q)return false;
  if(/^https?:\/\//i.test(q)&&/youtube|youtu\.be/i.test(q))return true;
  if(/list=/.test(q))return true;
  if(/^(PL|RD|OLAK|UU|FL|AA|LL)[A-Za-z0-9_-]{5,}/.test(q))return true;
  return false;
}
function row(x){
  const bpm=x.bpm?Math.round(x.bpm):null;
  const badge=bpm?`<span class="bpm">${bpm} BPM</span>`:'<span class="bpm none">- BPM</span>';
  return `<div class="result" data-bpm="${bpm===null?'':bpm}">
    <img class="thumb" src="${esc(x.thumbnail||'')}" alt="">
    <div class="meta"><b>${esc(x.title)}</b><small>${esc(x.artist)} (${x.duration})</small>${badge}</div>
    <div class="actions"><button class="like" title="Guardar en playlist" onclick="doSave('${esc(x.id)}')">&#9829;</button>
    <button class="deck1" onclick="doLoad('${esc(x.id)}',1)">Deck 1</button>
    <button class="deck2" onclick="doLoad('${esc(x.id)}',2)">Deck 2</button></div></div>`;
}
function renderSlice(){
  const o=document.getElementById('out');
  if(!allResults.length){o.innerHTML='<div class="msg err">Sin resultados</div>';updateMoreLess();return;}
  const slice=allResults.slice(0,visibleCount);
  o.innerHTML=`<div class="count">${slice.length} de ${allResults.length} resultados</div>`+slice.map(row).join('');
  applyFilter();
  updateMoreLess();
}
function updateMoreLess(){
  const canShowFromCache=visibleCount<allResults.length;
  const canFetchMore=allResults.length>=curLimit&&curLimit<MAX_RESULTS;
  document.getElementById('moreBtn').style.display=(canShowFromCache||canFetchMore)?'block':'none';
  document.getElementById('lessBtn').style.display=(visibleCount>10)?'block':'none';
}
function applyFilter(){
  const minEl=document.getElementById('bpmMin').value;
  const maxEl=document.getElementById('bpmMax').value;
  const hasFilter=minEl!==''||maxEl!=='';
  const min=parseFloat(minEl)||0;
  const max=parseFloat(maxEl)||999;
  document.querySelectorAll('#out .result').forEach(el=>{
    if(!hasFilter){el.style.display='';return;}
    const b=parseFloat(el.dataset.bpm);
    el.style.display=(!isNaN(b)&&b>=min&&b<=max)?'':'none';
  });
}
function doFilter(){applyFilter();}
function clearFilter(){
  document.getElementById('bpmMin').value='';
  document.getElementById('bpmMax').value='';
  applyFilter();
}
async function checkStatus(){
  try{
    const r=await fetch('/status');const s=await r.json();
    const st=document.getElementById('status');
    const inp=document.getElementById('q');
    const btn=document.getElementById('goBtn');
    if(s.mixxx){
      st.className='banner on';st.textContent='Mixxx conectado';
      inp.disabled=false;btn.disabled=false;
    }else{
      st.className='banner off';st.textContent='Abre Mixxx para buscar y cargar pistas';
      inp.disabled=true;btn.disabled=true;
    }
  }catch(e){}
}
async function fetchResults(showLoading){
  const o=document.getElementById('out');
  const endpoint=curMix?'/mix':'/search';
  if(showLoading){o.innerHTML='<div class="msg">'+(curMix?'Listando mix...':'Buscando...')+'</div>';}
  let j;
  try{
    const r=await fetch(endpoint+'?q='+encodeURIComponent(curQuery)+'&limit='+curLimit);
    j=await r.json();
  }catch(e){j={error:'Error de red'};}
  if(!Array.isArray(j)){
    const msg='<div class="msg err">'+(j&&j.error?esc(j.error):'Error')+'</div>';
    if(!allResults.length){o.innerHTML=msg;}else{o.insertAdjacentHTML('afterbegin',msg);}
    updateMoreLess();
    return false;
  }
  allResults=j;
  renderSlice();
  return true;
}
async function doGo(){
  const q=document.getElementById('q').value;
  if(!q.trim())return;
  curQuery=q;curLimit=10;curMix=isMix(q);
  allResults=[];visibleCount=10;
  fetchResults(true);
}
async function doMore(){
  if(visibleCount<allResults.length){
    visibleCount=Math.min(visibleCount+STEP,allResults.length);
    renderSlice();
    return;
  }
  if(curLimit>=MAX_RESULTS||!allResults.length){updateMoreLess();return;}
  const more=document.getElementById('moreBtn');
  const old=more.textContent;
  more.textContent='Cargando mas...';more.disabled=true;
  const prevLimit=curLimit;
  curLimit=Math.min(curLimit+STEP,MAX_RESULTS);
  const ok=await fetchResults(false);
  more.textContent=old;more.disabled=false;
  if(!ok){curLimit=prevLimit;updateMoreLess();return;}
  if(visibleCount<allResults.length){
    visibleCount=Math.min(visibleCount+STEP,allResults.length);
    renderSlice();
  }
}
function doLess(){
  visibleCount=Math.max(10,visibleCount-STEP);
  renderSlice();
}
async function doLoad(id,deck){
  const o=document.getElementById('out');
  const r=await fetch('/load?q='+encodeURIComponent(id)+'&deck='+deck);const j=await r.json();
  if(j.error){o.insertAdjacentHTML('afterbegin',`<div class="msg err">${esc(j.error)}</div>`);return;}
  o.insertAdjacentHTML('afterbegin',`<div class="msg ok">Cargando en Deck ${deck}: ${esc(j.title)} - ${esc(j.artist)}</div>`);
}
async function doLoadFile(i,deck){
  const t=curTracks[i];
  if(!t)return;
  const o=document.getElementById('playlists');
  const r=await fetch('/loadfile?path='+encodeURIComponent(t.path)+'&deck='+deck);const j=await r.json();
  if(j.error){o.insertAdjacentHTML('afterbegin',`<div class="msg err">${esc(j.error)}</div>`);return;}
  o.insertAdjacentHTML('afterbegin',`<div class="msg ok">Cargando en Deck ${deck}</div>`);
}
async function loadPlaylists(){
  try{
    const r=await fetch('/playlists');const j=await r.json();
    const pls=j.playlists||[];
    const sel=document.getElementById('playlistSel');
    const cur=sel.value;
    sel.innerHTML=pls.length?pls.map(p=>`<option value="${esc(p.name)}">${esc(p.name)} (${p.count})</option>`).join(''):'<option>Favoritos</option>';
    if(cur){for(let i=0;i<sel.options.length;i++){if(sel.options[i].value===cur){sel.selectedIndex=i;break;}}}
    renderPlaylists(pls);
  }catch(e){}
}
function renderPlaylists(pls){
  const o=document.getElementById('playlists');
  if(!pls||pls.length===0){o.innerHTML='<div class="msg">Sin playlists aun</div>';return;}
  o.innerHTML=pls.map(p=>`<div class="plitem"><button class="btn" onclick="viewPlaylist('${esc(p.name)}')">${esc(p.name)} (${p.count})</button></div>`).join('');
}
async function newPlaylist(){
  const name=prompt('Nombre de la nueva playlist:');
  if(!name||!name.trim())return;
  const r=await fetch('/playlists',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name.trim()})});
  const j=await r.json();
  if(j.playlists){
    const sel=document.getElementById('playlistSel');
    sel.innerHTML=j.playlists.map(p=>`<option value="${esc(p.name)}">${esc(p.name)} (${p.count})</option>`).join('');
    sel.value=name.trim();
    renderPlaylists(j.playlists);
  }
}
async function doSave(id){
  const sel=document.getElementById('playlistSel');
  const name=sel.value||'Favoritos';
  const o=document.getElementById('out');
  o.insertAdjacentHTML('afterbegin',`<div class="msg">Guardando en "${esc(name)}"... (descarga + BPM)</div>`);
  const r=await fetch('/playlist',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,id:id})});
  const j=await r.json();
  if(j.error){o.insertAdjacentHTML('afterbegin',`<div class="msg err">${esc(j.error)}</div>`);return;}
  const bpm=j.bpm?Math.round(j.bpm)+' BPM':'sin BPM';
  o.insertAdjacentHTML('afterbegin',`<div class="msg ok">Guardado en "${esc(name)}": ${esc(j.title)} - ${esc(j.artist)} (${bpm})</div>`);
  loadPlaylists();
}
async function viewPlaylist(name){
  curPlaylistName=name;
  const r=await fetch('/playlist?name='+encodeURIComponent(name));
  const j=await r.json();
  curTracks=Array.isArray(j)?j:[];
  const o=document.getElementById('playlists');
  if(curTracks.length===0){o.innerHTML=`<div class="plhead">${esc(name)}</div><div class="msg">Vacia</div>`;return;}
  o.innerHTML=`<div class="plhead">${esc(name)} (${curTracks.length})</div>`+curTracks.map((t,i)=>{
    const bpm=t.bpm?Math.round(t.bpm):null;
    return `<div class="track"><img class="thumb" src="${esc(t.thumbnail||'')}" alt="">
      <div class="meta"><b>${esc(t.title)}</b><small>${esc(t.artist)}${bpm?' - '+bpm+' BPM':''}</small></div>
      <button class="deck1" onclick="doLoadFile(${i},1)">D1</button>
      <button class="deck2" onclick="doLoadFile(${i},2)">D2</button>
      <button class="btn" onclick="removeTrack(${i})">Quitar</button></div>`;
  }).join('');
}
async function removeTrack(i){
  const t=curTracks[i];
  if(!t)return;
  const r=await fetch('/playlist?name='+encodeURIComponent(curPlaylistName)+'&id='+encodeURIComponent(t.id),{method:'DELETE'});
  const j=await r.json();
  if(j&&j.ok){viewPlaylist(curPlaylistName);loadPlaylists();}
}
checkStatus();
setInterval(checkStatus,2000);
loadPlaylists();
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = _HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/status":
            self._json({"mixxx": is_mixxx_running()})
        elif parsed.path == "/search":
            q = parse_qs(parsed.query).get("q", [""])[0]
            limit = min(int(parse_qs(parsed.query).get("limit", ["10"])[0]), 400)
            if not is_mixxx_running():
                self._json({"error": "Mixxx no esta abierto"})
                return
            self._json([{
                "id": r["id"], "title": r["title"], "artist": r["artist"],
                "duration": _fmt_dur(r.get("duration")),
                "bpm": get_cached_bpm(r["id"]),
                "thumbnail": _thumbnail_url(r["id"]),
            } for r in search(q, limit=limit)])
        elif parsed.path == "/mix":
            q = parse_qs(parsed.query).get("q", [""])[0]
            limit = min(int(parse_qs(parsed.query).get("limit", ["50"])[0]), 400)
            if not is_mixxx_running():
                self._json({"error": "Mixxx no esta abierto"})
                return
            self._json([{
                "id": r["id"], "title": r["title"], "artist": r["artist"],
                "duration": _fmt_dur(r.get("duration")),
                "bpm": get_cached_bpm(r["id"]),
                "thumbnail": _thumbnail_url(r["id"]),
            } for r in mix_tracks(q, limit=limit)])
        elif parsed.path == "/load":
            q = parse_qs(parsed.query).get("q", [""])[0]
            deck = int(parse_qs(parsed.query).get("deck", ["1"])[0])
            if not is_mixxx_running():
                self._json({"error": "Mixxx no esta abierto"})
                return
            info = load(q, deck=deck, autoplay=False)
            self._json(info or {"error": "download failed"})
        elif parsed.path == "/loadfile":
            path = parse_qs(parsed.query).get("path", [""])[0]
            deck = int(parse_qs(parsed.query).get("deck", ["1"])[0])
            if not is_mixxx_running():
                self._json({"error": "Mixxx no esta abierto"})
                return
            if not path:
                self._json({"error": "falta path"})
                return
            write_load_command(path, deck=deck, autoplay=False)
            self._json({"ok": True, "path": path})
        elif parsed.path == "/playlists":
            self._json({"playlists": list_playlists()})
        elif parsed.path == "/playlist":
            name = parse_qs(parsed.query).get("name", [""])[0]
            self._json(get_playlist_tracks(name))
        else:
            self.send_response(404)
            self.end_headers()

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/playlists":
            body = self._read_body()
            name = (body.get("name") or "").strip()
            if not name:
                self._json({"error": "falta name"})
                return
            _get_playlists().setdefault(name, [])
            _save_playlists()
            self._json({"playlists": list_playlists()})
        elif parsed.path == "/playlist":
            if not is_mixxx_running():
                self._json({"error": "Mixxx no esta abierto"})
                return
            body = self._read_body()
            name = (body.get("name") or "Favoritos").strip()
            video_id = (body.get("id") or "").strip()
            if not video_id:
                self._json({"error": "falta id"})
                return
            entry = save_track_to_playlist(name, video_id)
            self._json(entry or {"error": "no se pudo descargar"})
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path == "/playlist":
            q = parse_qs(parsed.query)
            name = q.get("name", [""])[0]
            video_id = q.get("id", [""])[0]
            if name and video_id:
                remove_track_from_playlist(name, video_id)
                self._json({"ok": True})
                return
            self._json({"error": "falta name o id"})
        else:
            self.send_response(404)
            self.end_headers()


def cmd_serve(args) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"ytmixx UI: {url}")
    print("Abre esa URL en tu navegador. Ctrl+C para salir.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ytmixx", description="YouTube Music bridge for Mixxx")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--command-file", type=Path, default=None,
                        help="Ruta del archivo de comandos JSON (default: YTMIXX_COMMAND_FILE)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("search", parents=[common], help="Buscar pistas")
    sp.add_argument("query")
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(func=cmd_search)

    gp = sub.add_parser("get", parents=[common], help="Descargar y mostrar la ruta local")
    gp.add_argument("query")
    gp.add_argument("--json", action="store_true")
    gp.add_argument("--no-embed", action="store_true", help="No incrustar etiquetas ID3")
    gp.set_defaults(func=cmd_get)

    lp = sub.add_parser("load", parents=[common], help="Descargar y cargar en un deck")
    lp.add_argument("query")
    lp.add_argument("--deck", type=int, default=1)
    lp.add_argument("--autoplay", action="store_true")
    lp.set_defaults(func=cmd_load)

    mp = sub.add_parser("mix", parents=[common], help="Descargar y cargar una playlist/mix (o 'list')")
    mp.add_argument("query")
    mp.add_argument("--start-deck", type=int, default=1)
    mp.add_argument("--limit", type=int, default=0, help="Max pistas (0 = todas)")
    mp.set_defaults(func=cmd_mix)

    cp = sub.add_parser("cache", parents=[common], help="Ver o limpiar la cache")
    cp.add_argument("--clear", action="store_true")
    cp.set_defaults(func=cmd_cache)

    sv = sub.add_parser("serve", parents=[common], help="Interfaz web local")
    sv.add_argument("--port", type=int, default=8765)
    sv.set_defaults(func=cmd_serve)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    global COMMAND_FILE
    if args.command_file:
        COMMAND_FILE = args.command_file

    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
