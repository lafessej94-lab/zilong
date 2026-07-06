"""
colab_leecher/media_tools.py — PATCHED
─────────────────────────────
Outils FFmpeg déclenchés quand l'utilisateur envoie directement un fichier
vidéo ou audio au bot (par opposition à un lien à télécharger).

Toutes les fonctions sont bloquantes-async : elles lancent ffmpeg dans un
sous-process et mettent à jour un message de statut Telegram pendant
l'exécution, avec une vraie progression (%, vitesse, ETA) basée sur la
sortie -progress de ffmpeg.
"""

import os
import json
import shutil
import subprocess
from asyncio import sleep
from datetime import datetime
from os import path as ospath, makedirs

from colab_leecher.utility.variables import Paths, ProcessTracker
from colab_leecher.utility.helper import sizeUnit, getTime, _pct_bar


def _job_dir(job_id: str) -> str:
    d = ospath.join(Paths.WORK_PATH, f"mediatools_{job_id}")
    makedirs(d, exist_ok=True)
    return d


def probe_streams(path: str) -> dict:
    """ffprobe -> dict brut (format + streams) pour lister les pistes locales."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return {}
    return json.loads(result.stdout)


def _probe_duration(path: str) -> float:
    """Durée en secondes du média (0.0 si inconnue)."""
    try:
        info = probe_streams(path)
        return float((info.get("format") or {}).get("duration") or 0.0)
    except Exception:
        return 0.0


async def _run_ffmpeg(cmd: list[str], label: str, status_msg, title: str,
                       extra: str = "", total_duration: float = 0.0):
    """
    Lance ffmpeg avec -progress pipe:1, parse la sortie en temps réel pour
    calculer % / vitesse / ETA, et édite status_msg toutes les 3s.
    """
    # Insère -progress pipe:1 -nostats juste après le binaire ffmpeg
    patched_cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]

    start = datetime.now()
    proc = subprocess.Popen(
        patched_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1, universal_newlines=True,
    )
    ProcessTracker.register(proc.pid, label)

    pct = 0.0
    speed_x = "—"
    out_time_s = 0.0
    last_edit = 0.0
    done = False

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, val = line.partition("=")
            val = val.strip()

            if key == "out_time_ms":
                try:
                    out_time_s = max(0, int(val)) / 1_000_000
                except ValueError:
                    pass
            elif key == "out_time":
                # format HH:MM:SS.microseconds — fallback si out_time_ms absent
                try:
                    h, m, s = val.split(":")
                    out_time_s = int(h) * 3600 + int(m) * 60 + float(s)
                except Exception:
                    pass
            elif key == "speed":
                speed_x = val if val not in ("", "0x") else "—"
            elif key == "progress" and val == "end":
                done = True

            if total_duration > 0:
                pct = min(100.0, (out_time_s / total_duration) * 100)

            now_ts = datetime.now().timestamp()
            if now_ts - last_edit >= 3 or done:
                last_edit = now_ts
                elapsed_s = (datetime.now() - start).total_seconds()
                spent = getTime(int(elapsed_s))

                if total_duration > 0:
                    bar = _pct_bar(pct, 12)
                    eta = "—"
                    if pct > 0.5:
                        remaining_s = elapsed_s * (100 - pct) / pct
                        eta = getTime(int(remaining_s))
                    body = (
                        f"<code>[{bar}]</code>  <b>{pct:.1f}%</b>\n\n"
                        f"🎯 <b>Position</b>  <code>{getTime(int(out_time_s))} / {getTime(int(total_duration))}</code>\n"
                        f"🚀 <b>Speed</b>     <code>{speed_x}</code>\n"
                        f"⏳ <b>ETA</b>       <code>{eta}</code>\n"
                        f"⏱ <b>Spent</b>     <code>{spent}</code>"
                    )
                else:
                    # Pas de durée connue (ex: screenshot) → mode simple
                    body = (
                        f"🚀 <b>Speed</b>  <code>{speed_x}</code>\n"
                        f"⏱ <b>Spent</b>  <code>{spent}</code>"
                    )

                try:
                    await status_msg.edit_text(
                        f"⚙️ <b>{title}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"{body}\n"
                        f"{extra}"
                    )
                except Exception:
                    pass
    finally:
        proc.wait()
        ProcessTracker.unregister(proc.pid)

    if proc.returncode != 0:
        raise RuntimeError(f"{label} a échoué (code {proc.returncode})")


# ══════════════════════════════════════════════
#  1) Remove Audio
# ══════════════════════════════════════════════

async def remove_audio(video_path: str, status_msg) -> str:
    job = _job_dir("noaudio")
    out = ospath.join(job, f"noaudio_{ospath.basename(video_path)}")
    cmd = ["ffmpeg", "-y", "-i", video_path, "-c", "copy", "-an", out]
    duration = _probe_duration(video_path)
    await _run_ffmpeg(cmd, "ffmpeg-removeaudio", status_msg, "REMOVE AUDIO",
                       f"📄 <code>{ospath.basename(video_path)}</code>",
                       total_duration=duration)
    return out


# ══════════════════════════════════════════════
#  2) Screenshot
# ══════════════════════════════════════════════

async def take_screenshot(video_path: str, status_msg, timestamp: float | None = None) -> str:
    job = _job_dir("shot")
    if timestamp is None:
        duration = _probe_duration(video_path) or 10.0
        timestamp = duration / 2
    out = ospath.join(job, f"shot_{ospath.splitext(ospath.basename(video_path))[0]}.jpg")
    cmd = ["ffmpeg", "-y", "-ss", str(timestamp), "-i", video_path, "-frames:v", "1", "-q:v", "2", out]
    # Pas de total_duration ici : c'est quasi-instantané, pas la peine.
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
    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-shortest", out,
    ]
    duration = _probe_duration(video_path)
    await _run_ffmpeg(cmd, "ffmpeg-merge", status_msg, "MERGE AUDIO + VIDEO",
                       f"🎬 <code>{ospath.basename(video_path)}</code>\n🎵 <code>{ospath.basename(audio_path)}</code>",
                       total_duration=duration)
    return out


# ══════════════════════════════════════════════
#  4) Hardsub local (fichier local + sous-titre local)
# ══════════════════════════════════════════════

async def hardsub_local(video_path: str, subtitle_path: str, status_msg) -> str:
    job = _job_dir("hardsub")
    name = ospath.splitext(ospath.basename(video_path))[0]
    out = ospath.join(job, f"{name}_hardsub.mp4")

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
    duration = _probe_duration(video_path)
    await _run_ffmpeg(cmd, "ffmpeg-hardsub-local", status_msg, "HARDSUB LOCAL",
                       f"📄 <code>{ospath.basename(video_path)}</code>\n💬 <code>{ospath.basename(subtitle_path)}</code>",
                       total_duration=duration)
    return out


# ══════════════════════════════════════════════
#  5) Audio Converter
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
        cmd += ["-vn"]
    cmd += AUDIO_FORMATS[out_ext] + [out]

    duration = _probe_duration(input_path)
    await _run_ffmpeg(cmd, "ffmpeg-audioconv", status_msg, "AUDIO CONVERTER",
                       f"📄 <code>{ospath.basename(input_path)}</code> → <code>{out_ext.upper()}</code>",
                       total_duration=duration)
    return out


# ══════════════════════════════════════════════
#  6) Stream Extractor (fichier local déjà en main)
# ══════════════════════════════════════════════

def list_local_streams(path: str) -> list[dict]:
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
    else:
        out = ospath.join(job, f"{name}_sub.srt")
        cmd = ["ffmpeg", "-y", "-i", path, "-map", f"0:{stream_index}", "-c:s", "srt", out]

    duration = _probe_duration(path)
    await _run_ffmpeg(cmd, "ffmpeg-extract-local", status_msg, "STREAM EXTRACTOR",
                       f"📄 <code>{ospath.basename(path)}</code>  ·  piste {stream_index}",
                       total_duration=duration)
    return out
