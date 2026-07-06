"""
colab_leecher/media_tools.py
─────────────────────────────
Outils FFmpeg déclenchés quand l'utilisateur envoie directement un fichier
vidéo ou audio au bot (par opposition à un lien à télécharger).

Toutes les fonctions sont bloquantes-async : elles lancent ffmpeg dans un
sous-process et mettent à jour un message de statut Telegram pendant
l'exécution, façon `converters.py` / `handler.py`.
"""

import os
import json
import shutil
import subprocess
from asyncio import sleep
from datetime import datetime
from os import path as ospath, makedirs

from colab_leecher.utility.variables import Paths, ProcessTracker
from colab_leecher.utility.helper import sizeUnit, getTime


def _job_dir(job_id: str) -> str:
    d = ospath.join(Paths.WORK_PATH, f"mediatools_{job_id}")
    makedirs(d, exist_ok=True)
    return d


async def _run_ffmpeg(cmd: list[str], label: str, status_msg, title: str, extra: str = ""):
    """Lance ffmpeg, garde le PID trackable via /status, edite status_msg toutes les 3s."""
    start = datetime.now()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ProcessTracker.register(proc.pid, label)
    tick = 0
    try:
        while proc.poll() is None:
            spent = getTime((datetime.now() - start).seconds)
            bar = "░" * (tick % 12) + "█" + "░" * (11 - (tick % 12))
            try:
                await status_msg.edit_text(
                    f"⚙️ <b>{title}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"<code>[{bar}]</code>\n\n"
                    f"⏱ <b>Spent</b>  <code>{spent}</code>\n"
                    f"{extra}"
                )
            except Exception:
                pass
            tick += 1
            await sleep(3)
    finally:
        ProcessTracker.unregister(proc.pid)

    if proc.returncode != 0:
        raise RuntimeError(f"{label} a échoué (code {proc.returncode})")


def probe_streams(path: str) -> dict:
    """ffprobe -> dict brut (format + streams) pour lister les pistes locales."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


# ══════════════════════════════════════════════
#  1) Remove Audio
# ══════════════════════════════════════════════

async def remove_audio(video_path: str, status_msg) -> str:
    job = _job_dir("noaudio")
    out = ospath.join(job, f"noaudio_{ospath.basename(video_path)}")
    cmd = ["ffmpeg", "-y", "-i", video_path, "-c", "copy", "-an", out]
    await _run_ffmpeg(cmd, "ffmpeg-removeaudio", status_msg, "REMOVE AUDIO",
                       f"📄 <code>{ospath.basename(video_path)}</code>")
    return out


# ══════════════════════════════════════════════
#  2) Screenshot
# ══════════════════════════════════════════════

async def take_screenshot(video_path: str, status_msg, timestamp: float | None = None) -> str:
    job = _job_dir("shot")
    if timestamp is None:
        info = probe_streams(video_path)
        duration = float((info.get("format") or {}).get("duration") or 10.0)
        timestamp = duration / 2
    out = ospath.join(job, f"shot_{ospath.splitext(ospath.basename(video_path))[0]}.jpg")
    cmd = ["ffmpeg", "-y", "-ss", str(timestamp), "-i", video_path, "-frames:v", "1", "-q:v", "2", out]
    await _run_ffmpeg(cmd, "ffmpeg-screenshot", status_msg, "SCREENSHOT",
                       f"🕐 <code>{timestamp:.1f}s</code>")
    return out


# ══════════════════════════════════════════════
#  3) Merge Audio + Video
# ══════════════════════════════════════════════

async def merge_audio_video(video_path: str, audio_path: str, status_msg) -> str:
    job = _job_dir("merge")
    name = ospath.splitext(ospath.basename(video_path))[0]
    out = ospath.join(job, f"{name}_merged.mkv")
    # On garde la vidéo intacte et on remplace/ajoute la piste audio fournie.
    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-shortest", out,
    ]
    await _run_ffmpeg(cmd, "ffmpeg-merge", status_msg, "MERGE AUDIO + VIDEO",
                       f"🎬 <code>{ospath.basename(video_path)}</code>\n🎵 <code>{ospath.basename(audio_path)}</code>")
    return out


# ══════════════════════════════════════════════
#  4) Hardsub local (fichier local + sous-titre local)
# ══════════════════════════════════════════════

async def hardsub_local(video_path: str, subtitle_path: str, status_msg) -> str:
    job = _job_dir("hardsub")
    name = ospath.splitext(ospath.basename(video_path))[0]
    out = ospath.join(job, f"{name}_hardsub.mp4")

    # Copie du sous-titre dans le dossier de travail pour éviter les soucis
    # de chemins contenant des espaces/apostrophes avec le filtre ffmpeg.
    sub_ext = ospath.splitext(subtitle_path)[1].lower()
    local_sub = ospath.join(job, f"sub{sub_ext}")
    shutil.copy2(subtitle_path, local_sub)

    filter_name = "ass" if sub_ext == ".ass" else "subtitles"
    escaped_sub = local_sub.replace("\\", "/").replace(":", "\\:")
    vf = f"{filter_name}='{escaped_sub}'"

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "copy", out,
    ]
    await _run_ffmpeg(cmd, "ffmpeg-hardsub-local", status_msg, "HARDSUB LOCAL",
                       f"📄 <code>{ospath.basename(video_path)}</code>\n💬 <code>{ospath.basename(subtitle_path)}</code>")
    return out


# ══════════════════════════════════════════════
#  5) Audio Converter (audio venu d'une vidéo, ou fichier audio direct)
# ══════════════════════════════════════════════

AUDIO_FORMATS = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "m4a": ["-c:a", "aac", "-b:a", "192k"],
    "wav": ["-c:a", "pcm_s16le"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
}


async def convert_audio(input_path: str, out_ext: str, status_msg, extract_from_video: bool = False) -> str:
    if out_ext not in AUDIO_FORMATS:
        raise ValueError(f"Format audio non supporté: {out_ext}")
    job = _job_dir("audioconv")
    name = ospath.splitext(ospath.basename(input_path))[0]
    out = ospath.join(job, f"{name}.{out_ext}")

    cmd = ["ffmpeg", "-y", "-i", input_path]
    if extract_from_video:
        cmd += ["-vn"]  # on jette la piste vidéo si la source est une vidéo
    cmd += AUDIO_FORMATS[out_ext] + [out]

    await _run_ffmpeg(cmd, "ffmpeg-audioconv", status_msg, "AUDIO CONVERTER",
                       f"📄 <code>{ospath.basename(input_path)}</code> → <code>{out_ext.upper()}</code>")
    return out


# ══════════════════════════════════════════════
#  6) Stream Extractor (fichier local déjà en main)
# ══════════════════════════════════════════════

def list_local_streams(path: str) -> list[dict]:
    """Retourne une liste simplifiée des pistes du fichier local, avec l'index
    ffmpeg (0:N) nécessaire pour l'extraction via -map."""
    info = probe_streams(path)
    out = []
    for s in info.get("streams", []):
        stype = str(s.get("codec_type") or "").lower()
        if stype not in ("video", "audio", "subtitle"):
            continue
        tags = s.get("tags", {}) or {}
        out.append({
            "index": s.get("index"),
            "type": stype,
            "codec": str(s.get("codec_name") or "?"),
            "lang": (tags.get("language") or "").lower(),
            "width": s.get("width"),
            "height": s.get("height"),
        })
    return out


async def extract_local_stream(path: str, stream_index: int, stream_type: str, status_msg) -> str:
    job = _job_dir("extract")
    name = ospath.splitext(ospath.basename(path))[0]

    if stream_type == "video":
        out = ospath.join(job, f"{name}_video.mp4")
        cmd = ["ffmpeg", "-y", "-i", path, "-map", f"0:{stream_index}", "-c", "copy", "-an", out]
    elif stream_type == "audio":
        out = ospath.join(job, f"{name}_audio.m4a")
        cmd = ["ffmpeg", "-y", "-i", path, "-map", f"0:{stream_index}", "-c", "copy", out]
    else:  # subtitle
        out = ospath.join(job, f"{name}_sub.srt")
        cmd = ["ffmpeg", "-y", "-i", path, "-map", f"0:{stream_index}", "-c:s", "srt", out]

    await _run_ffmpeg(cmd, "ffmpeg-extract-local", status_msg, "STREAM EXTRACTOR",
                       f"📄 <code>{ospath.basename(path)}</code>  ·  piste {stream_index}")
    return out
