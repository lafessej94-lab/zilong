import logging
import os
import platform
import pathlib
import psutil
import shutil
import json
import subprocess
from datetime import datetime
from asyncio import sleep, get_event_loop
from urllib.parse import urlparse
from uuid import uuid4
from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from colab_leecher import CC_API_KEY, FC_API_KEY, DUMP_ID, SEEDR_PASSWORD, SEEDR_USERNAME, colab_bot, OWNER
from colab_leecher.cloudconvert import cc_mode_label, quality_label, resize_label
from colab_leecher.utility.handler import (
    Direct_FC_Hardsub_Handler,
    Seedr_CC_Convert_Handler,
    Seedr_CC_Hardsub_Handler,
    Seedr_FC_Hardsub_Handler,
    cancelTask,
)
from colab_leecher.utility.variables import (
    BOT, MSG, BotTimes, Paths, Messages, ProcessTracker, TaskInfo, Aria2c,
)
from colab_leecher.utility.task_manager import taskScheduler
from colab_leecher.utility.helper import (
    isLink, setThumbnail, message_deleter, send_settings,
    sizeUnit, getTime, is_ytdl_link, fileType, _pct_bar, _speed_emoji,
)
from colab_leecher.downlader.aria2 import aria2_Download
from colab_leecher.stream_extractor import (
    analyse, get_session, clear_session,
    kb_type, kb_video, kb_audio, kb_subs,
    dl_video, dl_audio, dl_sub,
)
from colab_leecher import media_tools  # NEW — outils vidéo/audio directs


BOT.Options.auto_forward = str(DUMP_ID or "").strip() not in ("", "0")
BOT.Setting.auto_forward = "On" if BOT.Options.auto_forward else "Off"

# ── État en mémoire pour le hardsub FreeConvert concurrent ──────────────────
# _link_sessions : message_id (du message "Choose mode:") -> liste de sources.
#   Nécessaire pour que plusieurs liens envoyés d'affilée ne se marchent pas
#   dessus sur le global BOT.SOURCE — chaque bouton "mode" retrouve SON lien
#   via le message auquel il est attaché, pas via BOT.SOURCE (qui ne reflète
#   que le tout dernier lien envoyé).
# _pending_fc_subtitle : message_id (du message "Envoie le sous-titre...") ->
#   {"url":..., "name":...}. Permet plusieurs hardsub FC en attente de
#   sous-titre en même temps — l'utilisateur répond (reply) au bon message
#   avec le bon fichier pour lever l'ambiguïté.
_link_sessions: dict[int, list[str]] = {}
_pending_fc_subtitle: dict[int, dict] = {}

# ── NEW — État en mémoire pour les Media Tools (fichier envoyé directement) ─
# _pending_media : message_id (du message avec les boutons d'action) ->
#   {"video_path": ...} ou {"audio_path": ...} — le fichier local déjà téléchargé.
# _pending_media_input : message_id (du prompt "envoie le fichier compagnon") ->
#   {"action": "hardsub", "video_path": ...} — en attente d'un reply
#   avec le sous-titre (hardsub local uniquement).
_pending_media: dict[int, dict] = {}
_pending_media_input: dict[int, dict] = {}

# ── NEW — État global pour Merge Audio+Video, SANS dépendre du reply Telegram.
# Bot mono-utilisateur (OWNER only) → une seule fusion en attente à la fois
# suffit. Dès qu'un audio arrive (envoyé normalement OU en reply, peu importe),
# s'il y a une fusion en attente, elle est utilisée directement.
_pending_merge: dict = {}  # clé "video_msg_id" présente ⇔ une fusion attend un audio


# ══════════════════════════════════════════════
#  NEW — Callback de progression pour les téléchargements Pyrogram
# ══════════════════════════════════════════════

async def _make_dl_progress_cb(status_msg, label: str):
    """Callback de progression pour client.download_media / message.download.
    Édite status_msg toutes les 3s avec %, vitesse, ETA — throttlé pour
    éviter le flood-wait Telegram. Toujours édité une dernière fois à 100%."""
    state = {"last": 0.0, "start": datetime.now().timestamp()}

    async def cb(current: int, total: int):
        now = datetime.now().timestamp()
        if now - state["last"] < 3 and current != total:
            return
        state["last"] = now
        pct = (current / total * 100) if total else 0.0
        elapsed = max(now - state["start"], 0.01)
        speed = current / elapsed
        eta_s = int((total - current) / speed) if speed > 0 else 0
        bar = _pct_bar(pct, 12)
        try:
            await status_msg.edit_text(
                f"⏳ <b>{label}</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<code>[{bar}]</code>  <b>{pct:.1f}%</b>\n\n"
                f"📦 <b>Done</b>   <code>{sizeUnit(current)}</code>\n"
                f"📦 <b>Total</b>  <code>{sizeUnit(total)}</code>\n"
                f"🚀 <b>Speed</b>  <code>{sizeUnit(speed)}/s</code>\n"
                f"⏳ <b>ETA</b>    <code>{getTime(eta_s)}</code>"
            )
        except Exception:
            pass

    return cb


async def _ensure_local_video(msg_id: int, client, status_msg) -> str:
    """Télécharge la vidéo en attente (si pas déjà fait) et met en cache le
    chemin local dans _pending_media pour les appels suivants sur le même msg_id.
    Affiche une vraie progression (%, vitesse, ETA) pendant le téléchargement."""
    session = _pending_media.get(msg_id)
    if not session:
        raise RuntimeError("Session expirée — renvoie le fichier.")
    if "video_path" in session:
        return session["video_path"]
    os.makedirs(Paths.WORK_PATH, exist_ok=True)
    local_path = await client.download_media(
        session["video_file_id"],
        file_name=os.path.join(Paths.WORK_PATH, f"in_{uuid4().hex[:8]}_{session['video_file_name']}"),
        progress=await _make_dl_progress_cb(status_msg, "TÉLÉCHARGEMENT VIDÉO"),
    )
    session["video_path"] = local_path
    return local_path


async def _ensure_local_audio(msg_id: int, client, status_msg) -> str:
    """Idem _ensure_local_video mais pour un audio en attente."""
    session = _pending_media.get(msg_id)
    if not session:
        raise RuntimeError("Session expirée — renvoie le fichier.")
    if "audio_path" in session:
        return session["audio_path"]
    os.makedirs(Paths.WORK_PATH, exist_ok=True)
    local_path = await client.download_media(
        session["audio_file_id"],
        file_name=os.path.join(Paths.WORK_PATH, f"in_{uuid4().hex[:8]}_{session['audio_file_name']}"),
        progress=await _make_dl_progress_cb(status_msg, "TÉLÉCHARGEMENT AUDIO"),
    )
    session["audio_path"] = local_path
    return local_path


def _pick_stream_source_file(root: str) -> str | None:
    files = [str(p) for p in pathlib.Path(root).glob("**/*") if p.is_file()]
    if not files:
        return None
    videos = [f for f in files if fileType(f) == "video"]
    pool = videos or files
    return max(pool, key=lambda p: os.path.getsize(p))


async def _prepare_stream_source(url: str) -> str:
    if not url.startswith("magnet:?xt=urn:btih:"):
        return url

    if os.path.exists(Paths.WORK_PATH):
        shutil.rmtree(Paths.WORK_PATH)
    os.makedirs(Paths.WORK_PATH, exist_ok=True)
    os.makedirs(Paths.down_path, exist_ok=True)

    Aria2c.link_info = False
    TaskInfo.reset()
    TaskInfo.set(phase="download", engine="Aria2c", filename="magnet", started_at=datetime.now().timestamp())
    await aria2_Download(url, 1)

    source_file = _pick_stream_source_file(Paths.down_path)
    if not source_file:
        raise RuntimeError("Torrent download finished but no media file was found for stream extraction.")
    return source_file


def _fmt_hms(seconds: float) -> str:
    total = int(seconds or 0)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _probe_media_info(path: str) -> str:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""
        data = json.loads(result.stdout)
    except Exception as exc:
        logging.warning("Media info probe failed: %s", exc)
        return ""

    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    lines = [
        "MEDIA INFO",
        f"FILE  <code>{os.path.basename(path)}</code>",
        f"SIZE  <code>{sizeUnit(os.path.getsize(path))}</code>",
    ]
    duration = float(fmt.get("duration") or 0.0)
    if duration > 0:
        lines.append(f"DURATION  <code>{_fmt_hms(duration)}</code>")

    for stream in streams:
        stype = str(stream.get("codec_type") or "").lower()
        codec = str(stream.get("codec_name") or "?").upper()
        tags = stream.get("tags", {}) or {}
        lang = (tags.get("language") or "").lower()
        lang_s = f" [{lang}]" if lang else ""
        if stype == "video":
            w = stream.get("width", 0)
            h = stream.get("height", 0)
            fr = str(stream.get("r_frame_rate") or "0/1")
            try:
                fn, fd = fr.split("/")
                fps = float(fn) / max(float(fd), 1.0)
                fps_s = f"{fps:.3f}fps"
            except Exception:
                fps_s = "?"
            lines.append(f"VIDEO  <code>{codec}  {w}x{h}  {fps_s}</code>")
        elif stype == "audio":
            ch = int(stream.get("channels") or 0)
            ch_s = {1: "Mono", 2: "Stereo", 6: "5.1", 8: "7.1"}.get(ch, f"{ch}ch" if ch else "")
            lines.append(f"AUDIO  <code>{codec}  {ch_s}{lang_s}</code>")
        elif stype == "subtitle":
            lines.append(f"SUB  <code>{codec}{lang_s}</code>")
    return "\n".join(lines[:12])


async def _startup_welcome() -> None:
    for _ in range(6):
        try:
            await sleep(2)
            owner = await colab_bot.get_users(OWNER)
            first = owner.first_name or owner.username or str(OWNER)
            display = first.replace("<", "&lt;").replace(">", "&gt;")
            text = (
                f"👋 <b>Welcome back, {display}</b>\n"
                "⚡ <b>Sae is online</b>\n\n"
                "Send a link, magnet, or path to begin.\n"
                "Use /start for the full menu and /status for the live dashboard."
            )
            await colab_bot.send_message(chat_id=OWNER, text=text)
            return
        except Exception as exc:
            logging.warning("Startup welcome attempt failed: %s", exc)


def _owner(m): return m.chat.id == OWNER
def _ring(p):  return "🟢" if p < 40 else ("🟡" if p < 70 else "🔴")


# ══════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════

@colab_bot.on_message(filters.command("start") & filters.private)
async def start(client, message):
    await message.delete()
    await message.reply_text(
        "⚡ <b>ZILONG BOT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🟢 Online &amp; Ready\n\n"
        "Send a <b>link</b>, <b>magnet</b> or <b>path</b>.\n\n"
        "📥 Direct links · Magnet · GDrive\n"
        "🎬 YouTube · Mega · Terabox\n"
        "☁️ CloudConvert convert · resize · compress\n"
        "🧲 Seedr + CloudConvert convert · hardsub\n"
        "🧲 Seedr + FreeConvert hardsub\n"
        "🎞 Stream Extractor (any link)\n"
        "📎 Envoie une vidéo/audio directement pour les Media Tools\n"
        "📊 /status — live dashboard\n"
        "📡 /nyaa_search — anime search\n\n"
        "💡 /help for all commands",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📖 Help",     callback_data="cb_help"),
            InlineKeyboardButton("⚙️ Settings", callback_data="cb_settings"),
        ], [
            InlineKeyboardButton("📊 Status",   callback_data="status_refresh"),
        ]])
    )


# ══════════════════════════════════════════════
#  /help
# ══════════════════════════════════════════════

@colab_bot.on_message(filters.command("help") & filters.private)
async def help_cmd(client, message):
    text = (
        "📖 <b>HELP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔗 <b>Supported Sources</b>\n"
        "  · HTTP/HTTPS  · Magnet  · Torrent\n"
        "  · Google Drive  · Mega.nz  · Terabox\n"
        "  · YouTube / YTDL  · Telegram links\n"
        "  · Local paths (/content/...)\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚙️ <b>Commands</b>\n"
        "  /settings  — bot preferences\n"
        "  /status    — <b>live task dashboard + cancel</b>\n"
        "  /stats     — system resources\n"
        "  /ping      — latency test\n"
        "  /cancel    — cancel running task\n"
        "  /stop      — shutdown bot\n"
        "  /setname   — custom filename\n"
        "  /rename    — rename after download\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📡 <b>Nyaa Anime Search</b>\n"
        "  /nyaa_search <query> — search Nyaa.si\n"
        "  /nyaa_add <title>    — track anime\n"
        "  /nyaa_list           — watchlist\n"
        "  /nyaa_check          — poll now\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎛 <b>Options (after link)</b>\n"
        "  <code>[name.ext]</code>  — custom filename\n"
        "  <code>{pass}</code>     — zip password\n"
        "  <code>(pass)</code>     — unzip password\n\n"
        "☁️ <b>CloudConvert</b> — use CC Convert / Resize / Compress buttons\n"
        "🧲 <b>Seedr + CC</b> — on magnet links, use Seedr+CC Convert / Hardsub\n"
        "🧲 <b>Seedr + FreeConvert</b> — on magnet links, use Seedr+FC Hardsub\n"
        "🎞 <b>Stream Extractor</b> — tap 🎞 Streams on any link\n"
        "📎 <b>Media Tools</b> — envoie une vidéo (Merge, Hardsub Local, Screenshot, "
        "Remove Audio, Audio Converter, Stream Extractor) ou un audio (Convertir en MP3)\n"
        "🖼 Send a <b>photo</b> to set thumbnail"
    )
    msg = await message.reply_text(text)
    await sleep(120)
    await message_deleter(message, msg)


@colab_bot.on_message(filters.command("logs") & filters.private)
async def logs_cmd(client, message):
    if not _owner(message):
        return
    await message.delete()
    if not os.path.exists(Paths.LOG_PATH):
        await message.reply_text("❌ No log file found yet.")
        return
    try:
        with open(Paths.LOG_PATH, "r", encoding="utf-8", errors="replace") as fh:
            tail = "".join(fh.readlines()[-80:]).strip()
        if tail:
            await message.reply_text(f"📜 <b>Recent Logs</b>\n\n<code>{tail[-3500:]}</code>")
        await client.send_document(chat_id=OWNER, document=Paths.LOG_PATH, caption="Zilong runtime log")
    except Exception as exc:
        await message.reply_text(f"❌ Could not send logs: <code>{exc}</code>")


# ══════════════════════════════════════════════
#  /status — LIVE TASK DASHBOARD WITH CANCEL
# ══════════════════════════════════════════════

def _status_panel() -> str:
    """Build the /status panel text — shows task state + system + cancel info."""
    cpu  = psutil.cpu_percent(interval=0)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")

    cpu_bar  = _pct_bar(cpu, 10)
    ram_bar  = _pct_bar(ram.percent, 10)
    disk_bar = _pct_bar(disk.percent, 10)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "⚡  <b>ZILONG BOT — STATUS</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
    ]

    # ── Active task section ───────────────────
    if BOT.State.task_going:
        phase_icons = {
            "download": "📥", "upload": "📤", "process": "⚙️",
            "zip": "🗜", "extract": "📂",
        }
        icon   = phase_icons.get(TaskInfo.phase, "⏳")
        engine = TaskInfo.engine or "—"
        fname  = TaskInfo.filename or Messages.download_name or "—"
        fname  = (fname[:35] + "…") if len(fname) > 35 else fname
        pct    = TaskInfo.percentage
        speed  = TaskInfo.speed or "—"
        eta    = TaskInfo.eta or "—"
        spd_e  = _speed_emoji(speed)
        bar    = _pct_bar(pct, 14)

        elapsed = getTime((datetime.now() - BotTimes.task_start).seconds)

        lines += [
            f"{icon}  <b>{TaskInfo.phase.upper()}</b>  ·  <code>{engine}</code>",
            f"🏷  <code>{fname}</code>",
            "",
            f"<code>[{bar}]</code>  <b>{pct:.1f}%</b>",
            "",
            f"{spd_e}  <b>Speed</b>   <code>{speed}</code>",
            f"⏳  <b>ETA</b>     <code>{eta}</code>",
            f"🕰  <b>Elapsed</b> <code>{elapsed}</code>",
        ]

        procs = ProcessTracker.active()
        if procs:
            lines.append("")
            lines.append(f"🔧  <b>Processes</b>  <code>{len(procs)}</code>")
            for pid, label in procs[:5]:
                lines.append(f"   · PID {pid}  <code>{label[:25]}</code>")
    else:
        lines += [
            "💤  <b>No active task</b>",
            "",
            "<i>Send a link to start a download.</i>",
        ]

    # ── System section ────────────────────────
    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"{_ring(cpu)}  CPU   <code>[{cpu_bar}]</code>  <b>{cpu:.0f}%</b>",
        f"{_ring(ram.percent)}  RAM   <code>[{ram_bar}]</code>  <b>{ram.percent:.0f}%</b>",
        f"   Used <code>{sizeUnit(ram.used)}</code>  ·  Free <code>{sizeUnit(ram.available)}</code>",
        f"{_ring(disk.percent)}  Disk  <code>[{disk_bar}]</code>  <b>{disk.percent:.0f}%</b>",
        f"   Free <code>{sizeUnit(disk.free)}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    return "\n".join(lines)


def _status_kb() -> InlineKeyboardMarkup:
    rows = []
    if BOT.State.task_going:
        rows.append([
            InlineKeyboardButton("⛔ CANCEL TASK", callback_data="status_cancel"),
            InlineKeyboardButton("🔄 Refresh",     callback_data="status_refresh"),
        ])
        # Kill individual processes
        procs = ProcessTracker.active()
        if procs:
            row = []
            for pid, label in procs[:4]:
                short = label[:10] if label else str(pid)
                row.append(InlineKeyboardButton(
                    f"💀 {short}", callback_data=f"status_kill|{pid}",
                ))
                if len(row) == 2:
                    rows.append(row)
                    row = []
            if row:
                rows.append(row)
    else:
        rows.append([
            InlineKeyboardButton("🔄 Refresh", callback_data="status_refresh"),
            InlineKeyboardButton("❌ Close",    callback_data="close"),
        ])
    return InlineKeyboardMarkup(rows)


@colab_bot.on_message(filters.command("status") & filters.private)
async def cmd_status(client, message):
    await message.delete()
    await message.reply_text(
        _status_panel(),
        reply_markup=_status_kb(),
    )


# ══════════════════════════════════════════════
#  /stats — system info (unchanged)
# ══════════════════════════════════════════════

@colab_bot.on_message(filters.command("stats") & filters.private)
async def stats(client, message):
    if not _owner(message): return
    await message.delete()
    cpu  = psutil.cpu_percent(interval=1)
    ram  = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net  = psutil.net_io_counters()
    up_s = int((datetime.now() - datetime.fromtimestamp(psutil.boot_time())).total_seconds())
    text = (
        "📊 <b>SERVER STATS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🖥  <b>OS</b>      <code>{platform.system()} {platform.release()}</code>\n"
        f"🐍  <b>Python</b>  <code>v{platform.python_version()}</code>\n"
        f"⏱  <b>Uptime</b>  <code>{getTime(up_s)}</code>\n"
        f"🤖  <b>Task</b>    {'🟠 Running' if BOT.State.task_going else '⚪ Idle'}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{_ring(cpu)}  CPU  <code>[{_pct_bar(cpu,12)}]</code>  <b>{cpu:.1f}%</b>\n\n"
        f"{_ring(ram.percent)}  RAM  <code>[{_pct_bar(ram.percent,12)}]</code>  <b>{ram.percent:.1f}%</b>\n"
        f"    Used <code>{sizeUnit(ram.used)}</code>  ·  Free <code>{sizeUnit(ram.available)}</code>\n\n"
        f"{_ring(disk.percent)}  Disk <code>[{_pct_bar(disk.percent,12)}]</code>  <b>{disk.percent:.1f}%</b>\n"
        f"    Used <code>{sizeUnit(disk.used)}</code>  ·  Free <code>{sizeUnit(disk.free)}</code>\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"    ⬆️  <code>{sizeUnit(net.bytes_sent)}</code>\n"
        f"    ⬇️  <code>{sizeUnit(net.bytes_recv)}</code>"
    )
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh", callback_data="stats_refresh"),
        InlineKeyboardButton("❌ Close",    callback_data="close"),
    ]]))


# ══════════════════════════════════════════════
#  /ping
# ══════════════════════════════════════════════

@colab_bot.on_message(filters.command("ping") & filters.private)
async def ping(client, message):
    t0  = datetime.now()
    msg = await message.reply_text("⏳")
    ms  = (datetime.now() - t0).microseconds // 1000
    if ms < 100:   q, fill = "🟢 Excellent", 12
    elif ms < 300: q, fill = "🟡 Good",       8
    elif ms < 700: q, fill = "🟠 Average",     4
    else:          q, fill = "🔴 Poor",         1
    bar = "█" * fill + "░" * (12 - fill)
    await msg.edit_text(
        f"🏓 <b>PONG</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<code>[{bar}]</code>\n\n"
        f"⚡ <b>Latency</b>  <code>{ms} ms</code>\n"
        f"📶 <b>Quality</b>  {q}"
    )
    await sleep(20)
    await message_deleter(message, msg)


# ══════════════════════════════════════════════
#  /cancel, /stop, /settings, /setname, /rename
# ══════════════════════════════════════════════

@colab_bot.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message):
    if not _owner(message): return
    await message.delete()
    if BOT.State.task_going:
        await cancelTask("Cancelled via /cancel")
    else:
        msg = await message.reply_text("⚠️ No active task.")
        await sleep(8); await msg.delete()


@colab_bot.on_message(filters.command("stop") & filters.private)
async def stop_bot(client, message):
    if not _owner(message): return
    await message.delete()
    if BOT.State.task_going:
        await cancelTask("Bot shutdown")
    await message.reply_text("🛑 <b>Shutting down...</b> 👋")
    await sleep(2); await client.stop(); os._exit(0)


@colab_bot.on_message(filters.command("settings") & filters.private)
async def settings_cmd(client, message):
    if _owner(message):
        await message.delete()
        await send_settings(client, message, message.id, True)


@colab_bot.on_message(filters.command("setname") & filters.private)
async def custom_name(client, message):
    if len(message.command) != 2:
        msg = await message.reply_text("Usage: <code>/setname file.ext</code>", quote=True)
    else:
        BOT.Options.custom_name = message.command[1]
        msg = await message.reply_text(f"✅ Name → <code>{BOT.Options.custom_name}</code>", quote=True)
    await sleep(15); await message_deleter(message, msg)


@colab_bot.on_message(filters.command("rename") & filters.private)
async def rename_cmd(client, message):
    """Minimal rename — set name for next upload."""
    if len(message.command) < 2:
        return await message.reply_text(
            "✏️ <b>Rename</b>\n\nUsage: <code>/rename New Name.mkv</code>",
            quote=True,
        )
    new_name = " ".join(message.command[1:])
    BOT.Options.custom_name = new_name
    await message.reply_text(
        f"✅ Next file will be named: <code>{new_name}</code>",
        quote=True,
    )


@colab_bot.on_message(filters.command("zipaswd") & filters.private)
async def zip_pswd(client, message):
    if len(message.command) != 2:
        msg = await message.reply_text("Usage: <code>/zipaswd password</code>", quote=True)
    else:
        BOT.Options.zip_pswd = message.command[1]
        msg = await message.reply_text("✅ Zip password set 🔐", quote=True)
    await sleep(15); await message_deleter(message, msg)


@colab_bot.on_message(filters.command("unzipaswd") & filters.private)
async def unzip_pswd(client, message):
    if len(message.command) != 2:
        msg = await message.reply_text("Usage: <code>/unzipaswd password</code>", quote=True)
    else:
        BOT.Options.unzip_pswd = message.command[1]
        msg = await message.reply_text("✅ Unzip password set 🔓", quote=True)
    await sleep(15); await message_deleter(message, msg)


@colab_bot.on_message(filters.reply & filters.private)
async def setFix(client, message):
    if BOT.State.prefix:
        BOT.Setting.prefix = message.text; BOT.State.prefix = False
        await send_settings(client, message, message.reply_to_message_id, False)
        await message.delete()
    elif BOT.State.suffix:
        BOT.Setting.suffix = message.text; BOT.State.suffix = False
        await send_settings(client, message, message.reply_to_message_id, False)
        await message.delete()


# ══════════════════════════════════════════════
#  Link handler — mode selection
# ══════════════════════════════════════════════

def _mode_keyboard():
    rows = [
        [InlineKeyboardButton("📄 Normal",      callback_data="normal"),
         InlineKeyboardButton("🗜 Compress",    callback_data="zip")],
        [InlineKeyboardButton("📂 Extract",     callback_data="unzip"),
         InlineKeyboardButton("♻️ UnDoubleZip", callback_data="undzip")],
        [InlineKeyboardButton("☁️ CC Convert",  callback_data="cc_convert"),
         InlineKeyboardButton("📐 CC Resize",   callback_data="cc_resize")],
        [InlineKeyboardButton("🧱 CC Compress", callback_data="cc_compress"),
         InlineKeyboardButton("🎞 Streams",     callback_data="sx_open")],
    ]
    first = (BOT.SOURCE or [""])[0].strip()
    if first.startswith("magnet:?xt=urn:btih:"):
        rows.append([
            InlineKeyboardButton("☁️ Seedr+CC Convert", callback_data="seedr_cc_convert"),
            InlineKeyboardButton("☁️ Seedr+CC Hardsub", callback_data="seedr_cc_hardsub"),
        ])
        rows.append([
            InlineKeyboardButton("🆓 Seedr+FC Hardsub", callback_data="seedr_fc_hardsub"),
        ])
    elif first.startswith("http://") or first.startswith("https://"):
        rows.append([
            InlineKeyboardButton("🆓 FC Hardsub", callback_data="fc_hardsub_manual"),
        ])
    return InlineKeyboardMarkup(rows)


@colab_bot.on_message(filters.create(isLink) & ~filters.photo & filters.private)
async def handle_url(client, message):
    if not _owner(message): return
    BOT.Options.custom_name = ""
    BOT.Options.zip_pswd    = ""
    BOT.Options.unzip_pswd  = ""

    if BOT.State.task_going:
        msg = await message.reply_text("⚠️ Task running — /cancel first.", quote=True)
        await sleep(8); await msg.delete()
        return

    src = message.text.splitlines()
    for _ in range(3):
        if not src: break
        last = src[-1].strip()
        if   last.startswith("[") and last.endswith("]"): BOT.Options.custom_name = last[1:-1]; src.pop()
        elif last.startswith("{") and last.endswith("}"): BOT.Options.zip_pswd    = last[1:-1]; src.pop()
        elif last.startswith("(") and last.endswith(")"): BOT.Options.unzip_pswd  = last[1:-1]; src.pop()
        else: break

    BOT.SOURCE    = src
    BOT.Mode.ytdl = all(is_ytdl_link(l) for l in src if l.strip())
    BOT.Mode.mode = "leech"
    BOT.State.started = True

    n     = len([l for l in src if l.strip()])
    label = "🏮 YTDL" if BOT.Mode.ytdl else "🔗 Link"

    sent = await message.reply_text(
        f"{label}  ·  <code>{n}</code> source(s)\n<b>Choose mode:</b>",
        reply_markup=_mode_keyboard(), quote=True,
    )
    _link_sessions[sent.id] = src


# ══════════════════════════════════════════════
#  NEW — Media Tools : vidéo / audio envoyés directement
# ══════════════════════════════════════════════

def _video_tools_keyboard(msg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎬 Merge Audio+Video", callback_data=f"mt_merge|{msg_id}"),
         InlineKeyboardButton("🎞 Stream Extractor",   callback_data=f"mt_streams|{msg_id}")],
        [InlineKeyboardButton("💬 Hardsub Local",      callback_data=f"mt_hardsub|{msg_id}"),
         InlineKeyboardButton("📸 Screenshot",          callback_data=f"mt_shot|{msg_id}")],
        [InlineKeyboardButton("🔇 Remove Audio",       callback_data=f"mt_noaudio|{msg_id}")],
    ])


@colab_bot.on_message(filters.video & filters.private)
async def handle_incoming_video(client, message):
    if not _owner(message):
        return
    if BOT.State.task_going:
        msg = await message.reply_text("⚠️ Task running — /cancel first.", quote=True)
        await sleep(8); await msg.delete()
        return

    file_label = message.video.file_name or "video.mp4"

    # On affiche les boutons TOUT DE SUITE — pas de téléchargement ici.
    # Le fichier ne sera récupéré que quand une action sera choisie
    # (voir _ensure_local_video), pour que le menu apparaisse instantanément.
    status = await message.reply_text(
        "🎬 <b>OUTILS VIDÉO</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<code>{file_label}</code>\n\n"
        "Choisis une action :",
        quote=True,
    )
    _pending_media[status.id] = {
        "video_file_id": message.video.file_id,
        "video_file_name": file_label,
    }
    await status.edit_reply_markup(_video_tools_keyboard(status.id))
    await message.delete()


@colab_bot.on_message(filters.audio & filters.private)
async def handle_incoming_audio(client, message):
    if not _owner(message):
        return

    # ── Priorité absolue : s'il y a une fusion en attente d'un audio, cet
    # audio EST le fichier compagnon — peu importe si le message est un
    # reply ou juste envoyé normalement. Plus besoin de "répondre" au bot.
    if "video_msg_id" in _pending_merge:
        video_msg_id = _pending_merge.pop("video_msg_id")
        file_name = message.audio.file_name or "audio.mp3"
        ext = os.path.splitext(file_name)[1].lower() or ".mp3"
        os.makedirs(Paths.WORK_PATH, exist_ok=True)
        local_path = os.path.join(Paths.WORK_PATH, f"companion_{uuid4().hex[:8]}{ext}")
        status = await message.reply_text("⏳ <i>Starting...</i>")
        await message.download(
            file_name=local_path,
            progress=await _make_dl_progress_cb(status, "TÉLÉCHARGEMENT AUDIO"),
        )
        await message.delete()
        try:
            video_path = await _ensure_local_video(video_msg_id, client, status)
            out = await media_tools.merge_audio_video(video_path, local_path, status)
            from colab_leecher.uploader.telegram import upload_file
            await upload_file(out, os.path.basename(out), is_last=True)
            await status.delete()
        except Exception as e:
            await status.edit_text(f"❌ <b>Erreur</b>\n<code>{e}</code>")
        return

    # ── Aucune fusion en attente → "Audio Converter" a été supprimé (inutile).
    # On informe juste l'utilisateur au lieu d'ouvrir un menu.
    msg = await message.reply_text(
        "ℹ️ <b>Aucune fusion en attente.</b>\n\n"
        "Envoie une vidéo, clique sur <b>🎬 Merge Audio+Video</b>, "
        "puis envoie ton fichier audio — il sera utilisé automatiquement.",
        quote=True,
    )
    await sleep(10)
    await message_deleter(message, msg)


# ══════════════════════════════════════════════
#  ALL CALLBACKS
# ══════════════════════════════════════════════

@colab_bot.on_callback_query()
async def callbacks(client, cq):
    data    = cq.data
    chat_id = cq.message.chat.id

    # ── Help/Settings from /start ──────────────
    if data == "cb_help":
        await cq.answer()
        text = (
            "📖 <b>Quick Guide</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Send any link to download.\n"
            "/status — live dashboard + cancel\n"
            "/nyaa_search — anime torrents\n"
            "/settings — preferences\n"
            "/help — full command list"
        )
        await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="cb_back_start"),
        ]]))
        return

    if data == "cb_settings":
        await cq.answer()
        await send_settings(client, cq.message, cq.message.id, False)
        return

    if data == "cb_back_start":
        await cq.answer()
        await cq.message.edit_text(
            "⚡ <b>ZILONG BOT</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n🟢 Online",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📖 Help",     callback_data="cb_help"),
                InlineKeyboardButton("⚙️ Settings", callback_data="cb_settings"),
            ], [
                InlineKeyboardButton("📊 Status", callback_data="status_refresh"),
            ]])
        )
        return

    # ── Status panel callbacks ─────────────────

    if data == "status_refresh":
        await cq.answer("🔄 Refreshed")
        try:
            await cq.message.edit_text(
                _status_panel(),
                reply_markup=_status_kb(),
            )
        except Exception:
            pass
        return

    if data == "status_cancel":
        await cq.answer("⛔ Cancelling ALL tasks…")
        await cancelTask("Cancelled via /status panel")
        try:
            await cq.message.edit_text(
                _status_panel(),
                reply_markup=_status_kb(),
            )
        except Exception:
            pass
        return

    if data.startswith("status_kill|"):
        pid = int(data.split("|")[1])
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
            ProcessTracker.unregister(pid)
            await cq.answer(f"💀 Killed PID {pid}")
        except ProcessLookupError:
            ProcessTracker.unregister(pid)
            await cq.answer("Process already dead.")
        except Exception as e:
            await cq.answer(f"Kill failed: {e}", show_alert=True)
        try:
            await cq.message.edit_text(_status_panel(), reply_markup=_status_kb())
        except Exception:
            pass
        return

    # ── Stats refresh ──────────────────────────
    if data == "stats_refresh":
        await cq.answer("🔄")
        cpu  = psutil.cpu_percent(interval=0)
        ram  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net  = psutil.net_io_counters()
        text = (
            "📊 <b>SERVER STATS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{_ring(cpu)}  CPU  <code>[{_pct_bar(cpu,12)}]</code>  <b>{cpu:.1f}%</b>\n\n"
            f"{_ring(ram.percent)}  RAM  <code>[{_pct_bar(ram.percent,12)}]</code>  <b>{ram.percent:.1f}%</b>\n"
            f"    Used <code>{sizeUnit(ram.used)}</code>  ·  Free <code>{sizeUnit(ram.available)}</code>\n\n"
            f"{_ring(disk.percent)}  Disk <code>[{_pct_bar(disk.percent,12)}]</code>  <b>{disk.percent:.1f}%</b>\n"
            f"    Free <code>{sizeUnit(disk.free)}</code>\n\n"
            f"    ⬆️ <code>{sizeUnit(net.bytes_sent)}</code>  ⬇️ <code>{sizeUnit(net.bytes_recv)}</code>"
        )
        try:
            await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="stats_refresh"),
                InlineKeyboardButton("❌ Close",    callback_data="close"),
            ]]))
        except Exception:
            pass
        return

    # ── Task launch ────────────────────────────
    if data in ["normal", "zip", "unzip", "undzip", "cc_convert", "cc_resize", "cc_compress"]:
        if data.startswith("cc_") and not CC_API_KEY.strip():
            await cq.answer("CloudConvert API key is missing in your Colab launcher.", show_alert=True)
            return
        BOT.Mode.type = data
        await cq.message.delete()
        MSG.status_msg = await colab_bot.send_message(
            chat_id=OWNER, text="⏳ <i>Starting...</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⛔ Cancel", callback_data="cancel"),
                InlineKeyboardButton("📊 Status", callback_data="status_refresh"),
            ]]),
        )
        BOT.State.task_going = True
        BOT.State.started    = False
        BotTimes.start_time  = datetime.now()
        TaskInfo.reset()
        TaskInfo.set(phase="download", started_at=datetime.now().timestamp())
        BOT.TASK = get_event_loop().create_task(taskScheduler())
        await BOT.TASK
        BOT.State.task_going = False
        TaskInfo.reset()
        return

    if data in ["seedr_cc_convert", "seedr_cc_hardsub"]:
        if not CC_API_KEY.strip():
            await cq.answer("CloudConvert API key is missing in your Colab launcher.", show_alert=True)
            return
        if not str(SEEDR_USERNAME or "").strip() or not str(SEEDR_PASSWORD or "").strip():
            await cq.answer("Seedr credentials are missing in your Colab launcher.", show_alert=True)
            return
        magnet = _link_sessions.get(cq.message.id, BOT.SOURCE or [""])[0].strip()
        if not magnet.startswith("magnet:?xt=urn:btih:"):
            await cq.answer("Seedr mode currently needs a magnet link.", show_alert=True)
            return
        if BOT.State.task_going:
            await cq.answer("A task is already running — /cancel first.", show_alert=True)
            return

        await cq.message.delete()
        MSG.status_msg = await colab_bot.send_message(
            chat_id=OWNER,
            text="⏳ <i>Starting Seedr job...</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⛔ Cancel", callback_data="cancel"),
                InlineKeyboardButton("📊 Status", callback_data="status_refresh"),
            ]]),
        )
        BOT.State.task_going = True
        BOT.State.started = False
        BotTimes.start_time = datetime.now()
        TaskInfo.reset()
        TaskInfo.set(phase="process", engine="Seedr+CloudConvert", started_at=datetime.now().timestamp())
        BOT.Mode.type = data
        if data == "seedr_cc_convert":
            BOT.TASK = get_event_loop().create_task(Seedr_CC_Convert_Handler(magnet))
        else:
            BOT.TASK = get_event_loop().create_task(Seedr_CC_Hardsub_Handler(magnet))
        await BOT.TASK
        BOT.State.task_going = False
        TaskInfo.reset()
        return

    # ── FreeConvert Hardsub (magnet) — CONCURRENT, jusqu'à 3 en parallèle ──
    # Ne bloque pas sur BOT.State.task_going : peut tourner en même temps
    # qu'un autre hardsub FC, ou même pendant un leech normal en cours.
    if data == "seedr_fc_hardsub":
        if not FC_API_KEY.strip():
            await cq.answer("FreeConvert API key is missing in your Colab launcher.", show_alert=True)
            return
        if not str(SEEDR_USERNAME or "").strip() or not str(SEEDR_PASSWORD or "").strip():
            await cq.answer("Seedr credentials are missing in your Colab launcher.", show_alert=True)
            return
        magnet = _link_sessions.get(cq.message.id, BOT.SOURCE or [""])[0].strip()
        if not magnet.startswith("magnet:?xt=urn:btih:"):
            await cq.answer("Seedr mode currently needs a magnet link.", show_alert=True)
            return

        await cq.answer("🆓 Hardsub FreeConvert démarré (en parallèle)")
        await cq.message.delete()
        job_status_msg = await colab_bot.send_message(
            chat_id=OWNER,
            text="⏳ <i>Starting Seedr + FreeConvert hardsub job...</i>",
        )
        get_event_loop().create_task(Seedr_FC_Hardsub_Handler(magnet, job_status_msg))
        return

    # ── FreeConvert Hardsub sur lien direct (sous-titre fourni manuellement) ──
    # Concurrent lui aussi. Le sous-titre est associé via reply-to-message,
    # pour supporter plusieurs demandes en attente simultanément.
    if data == "fc_hardsub_manual":
        if not FC_API_KEY.strip():
            await cq.answer("FreeConvert API key is missing in your Colab launcher.", show_alert=True)
            return
        url = _link_sessions.get(cq.message.id, BOT.SOURCE or [""])[0].strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            await cq.answer("This option needs a direct HTTP(S) link.", show_alert=True)
            return

        name = os.path.basename(urlparse(url).path) or "video.mp4"

        prompt = await cq.message.edit_text(
            "🆓 <b>FREECONVERT HARDSUB</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"<code>{name}</code>\n\n"
            "📎 <b>Réponds à ce message</b> (reply) avec le fichier de sous-titres "
            "(<code>.ass</code> ou <code>.srt</code>) à utiliser.\n\n"
            "<i>Le style (police, gras, contour...) sera appliqué automatiquement. "
            "Tu peux lancer un autre lien pendant que celui-ci tourne.</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✖ Annuler", callback_data="fc_hardsub_cancel"),
            ]]),
        )
        _pending_fc_subtitle[prompt.id] = {"url": url, "name": name}
        return

    if data == "fc_hardsub_cancel":
        _pending_fc_subtitle.pop(cq.message.id, None)
        await cq.message.edit_text("❌ Hardsub annulé.")
        return

    # ════════════════════════════════════════════
    #  STREAM EXTRACTOR
    # ════════════════════════════════════════════

    if data == "sx_open":
        url = (BOT.SOURCE or [None])[0]
        if not url:
            await cq.answer("No URL found.", show_alert=True); return

        source_url = url
        if url.startswith("magnet:?xt=urn:btih:"):
            await cq.message.edit_text(
                "STREAM EXTRACTOR\n\nDownloading magnet first...\nThe stream menu will open once the main video is local."
            )
            MSG.status_msg = cq.message
            BOT.State.task_going = True
            try:
                source_url = await _prepare_stream_source(url)
            except Exception as exc:
                BOT.State.task_going = False
                await cq.message.edit_text(
                    f"STREAM EXTRACTOR\n\nFailed to prepare source:\n<code>{exc}</code>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="sx_back")]])
                )
                return
            BOT.State.task_going = False
        else:
            await cq.message.edit_text(
                "STREAM EXTRACTOR\n\n"
                f"Analyzing streams...\n"
                f"<code>{url[:70]}{'...' if len(url)>70 else ''}</code>"
            )

        session = await analyse(source_url, chat_id)

        if not session or (not session["video"] and not session["audio"] and not session["subs"]):
            await cq.message.edit_text(
                "STREAM EXTRACTOR\n\n"
                "Could not extract streams.\n"
                "<i>Only yt-dlp compatible sources are supported.</i>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Back", callback_data="sx_back")
                ]])
            )
            return

        await _show_type_menu(cq.message, session)
        return

    if data == "sx_type":
        session = get_session(chat_id)
        if not session:
            await cq.answer("Session expired.", show_alert=True); return
        await _show_type_menu(cq.message, session)
        return

    if data == "sx_video":
        session = get_session(chat_id)
        if not session: await cq.answer("Session expired.", show_alert=True); return
        if not session["video"]: await cq.answer("No video tracks.", show_alert=True); return
        await cq.message.edit_text(
            "🎬 <b>VIDEO TRACKS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>flag  resolution  [codec]  size</i>\n\nTap to download:",
            reply_markup=kb_video(session)
        )
        return

    if data == "sx_audio":
        session = get_session(chat_id)
        if not session: await cq.answer("Session expired.", show_alert=True); return
        if not session["audio"]: await cq.answer("No audio tracks.", show_alert=True); return
        await cq.message.edit_text(
            "🎵 <b>AUDIO TRACKS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>flag  language  [codec]  bitrate  size</i>\n\nTap to download:",
            reply_markup=kb_audio(session)
        )
        return

    if data == "sx_subs":
        session = get_session(chat_id)
        if not session: await cq.answer("Session expired.", show_alert=True); return
        if not session["subs"]: await cq.answer("No subtitles.", show_alert=True); return
        await cq.message.edit_text(
            "💬 <b>SUBTITLES</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>flag  language  [format]</i>\n\nTap to download:",
            reply_markup=kb_subs(session)
        )
        return

    if data == "sx_back":
        clear_session(chat_id)
        n     = len([l for l in (BOT.SOURCE or []) if l.strip()])
        label = "🏮 YTDL" if BOT.Mode.ytdl else "🔗 Link"
        await cq.message.edit_text(
            f"{label}  ·  <code>{n}</code> source(s)\n<b>Choose mode:</b>",
            reply_markup=_mode_keyboard()
        )
        return

    # ── Stream download ────────────────────────
    if data.startswith("sx_dl_"):
        session = get_session(chat_id)
        if not session: await cq.answer("Session expired.", show_alert=True); return

        parts = data.split("_")
        kind  = parts[2]
        idx   = int(parts[3])

        stream = (session["video"] if kind == "video"
                  else session["audio"] if kind == "audio"
                  else session["subs"])[idx]

        await cq.message.edit_text(
            f"🎞 <b>STREAM EXTRACTOR</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⬇️ <i>Downloading {kind}...</i>\n\n"
            f"<code>{stream['label']}</code>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⛔ Cancel", callback_data="cancel")
            ]])
        )
        MSG.status_msg = cq.message

        os.makedirs(Paths.down_path, exist_ok=True)
        try:
            if kind == "video":
                fp = await dl_video(session, idx, Paths.down_path)
            elif kind == "audio":
                fp = await dl_audio(session, idx, Paths.down_path)
            else:
                fp = await dl_sub(session, idx, Paths.down_path)

            from colab_leecher.uploader.telegram import upload_file
            await upload_file(fp, os.path.basename(fp), is_last=True)
            media_info = _probe_media_info(fp)
            if media_info:
                await colab_bot.send_message(chat_id=OWNER, text=media_info)
            clear_session(chat_id)

        except Exception as e:
            logging.error(f"[StreamDL] {e}")
            try:
                await cq.message.edit_text(
                    f"🎞 <b>STREAM EXTRACTOR</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"❌ <b>Error:</b> <code>{e}</code>"
                )
            except Exception: pass
        return

    # ════════════════════════════════════════════
    #  NEW — MEDIA TOOLS (fichier envoyé directement)
    # ════════════════════════════════════════════

    if data.startswith("mt_"):
        parts  = data.split("|")
        action = parts[0]

        # ── Remove Audio (direct, pas d'input supplémentaire) ──
        if action == "mt_noaudio":
            msg_id = int(parts[1])
            if not _pending_media.get(msg_id):
                await cq.answer("Session expirée — renvoie le fichier.", show_alert=True); return
            await cq.answer("🔇 Suppression de l'audio...")
            status = await cq.message.edit_text("⏳ <i>Starting...</i>")
            try:
                video_path = await _ensure_local_video(msg_id, client, status)
                out = await media_tools.remove_audio(video_path, status)
                from colab_leecher.uploader.telegram import upload_file
                await upload_file(out, os.path.basename(out), is_last=True)
                await status.delete()
            except Exception as e:
                await status.edit_text(f"❌ <b>Erreur</b>\n<code>{e}</code>")
            return

        # ── Screenshot (direct, capture au milieu de la vidéo) ──
        if action == "mt_shot":
            msg_id = int(parts[1])
            if not _pending_media.get(msg_id):
                await cq.answer("Session expirée — renvoie le fichier.", show_alert=True); return
            await cq.answer("📸 Capture...")
            status = await cq.message.edit_text("⏳ <i>Starting...</i>")
            try:
                video_path = await _ensure_local_video(msg_id, client, status)
                out = await media_tools.take_screenshot(video_path, status)
                await colab_bot.send_photo(chat_id=OWNER, photo=out, caption=os.path.basename(video_path))
                await status.delete()
            except Exception as e:
                await status.edit_text(f"❌ <b>Erreur</b>\n<code>{e}</code>")
            return

        # ── Merge Audio+Video : demande le fichier audio compagnon ──
        # Le téléchargement de la vidéo n'a lieu que quand le fichier audio
        # compagnon arrive (voir handle_incoming_audio / handle_subtitle_document).
        # Pas besoin de reply : le prochain audio envoyé sera utilisé direct.
        if action == "mt_merge":
            msg_id = int(parts[1])
            session = _pending_media.get(msg_id)
            if not session:
                await cq.answer("Session expirée — renvoie le fichier.", show_alert=True); return
            _pending_merge["video_msg_id"] = msg_id
            await cq.message.edit_text(
                "🎬 <b>MERGE AUDIO + VIDEO</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<code>{session.get('video_file_name', 'video')}</code>\n\n"
                "📎 <b>Envoie maintenant le fichier audio</b> à fusionner — "
                "pas besoin de répondre (reply), envoie-le simplement.",
            )
            await cq.answer()
            return

        # ── Hardsub Local : demande le fichier de sous-titres ──
        # Même principe : la vidéo n'est téléchargée qu'à la réception du sous-titre.
        if action == "mt_hardsub":
            msg_id = int(parts[1])
            session = _pending_media.get(msg_id)
            if not session:
                await cq.answer("Session expirée — renvoie le fichier.", show_alert=True); return
            prompt = await cq.message.edit_text(
                "💬 <b>HARDSUB LOCAL</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"<code>{session.get('video_file_name', 'video')}</code>\n\n"
                "📎 <b>Réponds à ce message</b> (reply) avec le fichier <code>.srt</code> ou <code>.ass</code>.",
            )
            _pending_media_input[prompt.id] = {"action": "hardsub", "video_msg_id": msg_id}
            await cq.answer()
            return

        # ── Stream Extractor sur le fichier local (nécessite le fichier pour ffprobe) ──
        if action == "mt_streams":
            msg_id = int(parts[1])
            if not _pending_media.get(msg_id):
                await cq.answer("Session expirée — renvoie le fichier.", show_alert=True); return
            await cq.answer("🔍 Analyse...")
            status = await cq.message.edit_text("⏳ <i>Starting...</i>")
            try:
                video_path = await _ensure_local_video(msg_id, client, status)
            except Exception as e:
                await status.edit_text(f"❌ <b>Erreur</b>\n<code>{e}</code>"); return
            streams = media_tools.list_local_streams(video_path)
            if not streams:
                await status.edit_text("❌ Impossible de lire les pistes."); return
            rows = []
            for s in streams:
                if s["type"] == "video":
                    label = f"🎬 {s['width']}x{s['height']} [{s['codec']}]"
                elif s["type"] == "audio":
                    label = f"🎵 {s['lang'] or '?'} [{s['codec']}]"
                else:
                    label = f"💬 {s['lang'] or '?'} [{s['codec']}]"
                rows.append([InlineKeyboardButton(
                    label, callback_data=f"mt_streamdl|{s['type']}|{s['index']}|{msg_id}",
                )])
            await status.edit_text(
                "🎞 <b>STREAM EXTRACTOR</b>\n\nPistes détectées, tape pour extraire :",
                reply_markup=InlineKeyboardMarkup(rows),
            )
            return

        if action == "mt_streamdl":
            _, stype, sidx, msg_id = parts
            msg_id = int(msg_id)
            if not _pending_media.get(msg_id):
                await cq.answer("Session expirée.", show_alert=True); return
            await cq.answer("⬇️ Extraction...")
            status = await cq.message.edit_text("⏳ <i>Starting...</i>")
            try:
                video_path = await _ensure_local_video(msg_id, client, status)  # déjà en cache normalement
                out = await media_tools.extract_local_stream(video_path, int(sidx), stype, status)
                from colab_leecher.uploader.telegram import upload_file
                await upload_file(out, os.path.basename(out), is_last=True)
                await status.delete()
            except Exception as e:
                await status.edit_text(f"❌ <b>Erreur</b>\n<code>{e}</code>")
            return

    # ── Settings callbacks ─────────────────────
    if data == "video":
        await cq.message.edit_text(
            "🎥 <b>VIDEO SETTINGS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Convert  <code>{BOT.Setting.convert_video}</code>\n"
            f"Split    <code>{BOT.Setting.split_video}</code>\n"
            f"Format   <code>{BOT.Options.video_out.upper()}</code>\n"
            f"Quality  <code>{BOT.Setting.convert_quality}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✂️ Split",   callback_data="split-true"),
                 InlineKeyboardButton("🗜 Zip",     callback_data="split-false")],
                [InlineKeyboardButton("🔄 Convert", callback_data="convert-true"),
                 InlineKeyboardButton("🚫 No",      callback_data="convert-false")],
                [InlineKeyboardButton("🎬 MP4",     callback_data="mp4"),
                 InlineKeyboardButton("📦 MKV",     callback_data="mkv")],
                [InlineKeyboardButton("🔝 High",    callback_data="q-High"),
                 InlineKeyboardButton("📉 Low",     callback_data="q-Low")],
                [InlineKeyboardButton("⏎ Back",     callback_data="back")],
            ]))
    elif data == "cc":
        cc_ready = "Ready" if CC_API_KEY.strip() else "Missing"
        await cq.message.edit_text(
            "☁️ <b>CLOUDCONVERT SETTINGS</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"API Key  <code>{cc_ready}</code>\n"
            f"Mode     <code>{cc_mode_label(BOT.Options.cc_engine_mode)}</code>\n"
            f"Preset   <code>{quality_label(BOT.Options.cc_quality_profile)}</code>\n"
            f"Resize   <code>{resize_label(BOT.Options.cc_resize)}</code>\n"
            f"Target   <code>{BOT.Setting.cc_target_size}</code>\n\n"
            "These settings are used by CC Convert, CC Resize, and CC Compress.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚖️ CC Mode", callback_data="cc-mode"),
                 InlineKeyboardButton("🎚 Preset", callback_data="cc-quality")],
                [InlineKeyboardButton("📐 Resize", callback_data="cc-resize"),
                 InlineKeyboardButton("🗜 Target", callback_data="cc-target")],
                [InlineKeyboardButton("⏮ Back", callback_data="back")],
            ]))
    elif data == "caption":
        await cq.message.edit_text(
            f"✏️ <b>CAPTION STYLE</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Current: <code>{BOT.Setting.caption}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Monospace", callback_data="code-Monospace"),
                 InlineKeyboardButton("Bold",      callback_data="b-Bold")],
                [InlineKeyboardButton("Italic",    callback_data="i-Italic"),
                 InlineKeyboardButton("Underline", callback_data="u-Underlined")],
                [InlineKeyboardButton("Plain",     callback_data="p-Regular")],
                [InlineKeyboardButton("⏎ Back",    callback_data="back")],
            ]))
    elif data == "thumb":
        await cq.message.edit_text(
            f"🖼 <b>THUMBNAIL</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Status: {'✅ Set' if BOT.Setting.thumbnail else '❌ None'}\n\n"
            "Send a photo to update.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Delete", callback_data="del-thumb")],
                [InlineKeyboardButton("⏎ Back",   callback_data="back")],
            ]))
    elif data == "del-thumb":
        if BOT.Setting.thumbnail:
            try: os.remove(Paths.THMB_PATH)
            except Exception: pass
        BOT.Setting.thumbnail = False
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "set-prefix":
        await cq.message.edit_text("Reply with your <b>prefix</b> text:")
        BOT.State.prefix = True
    elif data == "set-suffix":
        await cq.message.edit_text("Reply with your <b>suffix</b> text:")
        BOT.State.suffix = True
    elif data in ["code-Monospace","p-Regular","b-Bold","i-Italic","u-Underlined"]:
        r = data.split("-"); BOT.Options.caption = r[0]; BOT.Setting.caption = r[1]
        await send_settings(client, cq.message, cq.message.id, False)
    elif data in ["split-true","split-false"]:
        BOT.Options.is_split    = data == "split-true"
        BOT.Setting.split_video = "Split" if data == "split-true" else "Zip"
        await send_settings(client, cq.message, cq.message.id, False)
    elif data in ["convert-true","convert-false","mp4","mkv","q-High","q-Low"]:
        if   data == "convert-true":  BOT.Options.convert_video = True;  BOT.Setting.convert_video = "Yes"
        elif data == "convert-false": BOT.Options.convert_video = False; BOT.Setting.convert_video = "No"
        elif data == "q-High": BOT.Setting.convert_quality = "High"; BOT.Options.convert_quality = True
        elif data == "q-Low":  BOT.Setting.convert_quality = "Low";  BOT.Options.convert_quality = False
        else: BOT.Options.video_out = data
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "cc-mode":
        cycle = ["balanced", "economy"]
        cur = str(BOT.Options.cc_engine_mode or "balanced").lower()
        nxt = cycle[(cycle.index(cur) + 1) % len(cycle)] if cur in cycle else "balanced"
        BOT.Options.cc_engine_mode = nxt
        BOT.Setting.cc_engine_mode = cc_mode_label(nxt)
        await cq.answer(BOT.Setting.cc_engine_mode, show_alert=True)
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "cc-quality":
        cycle = ["fast", "balanced", "small", "best"]
        cur = str(BOT.Options.cc_quality_profile or "balanced").lower()
        nxt = cycle[(cycle.index(cur) + 1) % len(cycle)] if cur in cycle else "balanced"
        BOT.Options.cc_quality_profile = nxt
        BOT.Setting.cc_quality_profile = quality_label(nxt)
        await cq.answer(BOT.Setting.cc_quality_profile, show_alert=True)
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "cc-resize":
        cycle = [0, 480, 720, 1080]
        cur = int(BOT.Options.cc_resize or 0)
        nxt = cycle[(cycle.index(cur) + 1) % len(cycle)] if cur in cycle else 720
        BOT.Options.cc_resize = nxt
        BOT.Setting.cc_resize = resize_label(nxt)
        await cq.answer(BOT.Setting.cc_resize, show_alert=True)
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "cc-target":
        cycle = [50, 100, 200, 500]
        cur = int(BOT.Options.cc_target_size_mb or 100)
        nxt = cycle[(cycle.index(cur) + 1) % len(cycle)] if cur in cycle else 100
        BOT.Options.cc_target_size_mb = nxt
        BOT.Setting.cc_target_size = f"{nxt} MB"
        await cq.answer(BOT.Setting.cc_target_size, show_alert=True)
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "autofwd":
        if str(DUMP_ID or "").strip() in ("", "0"):
            await cq.answer("Set DUMP_ID first in Colab to use autoforward.", show_alert=True)
        else:
            BOT.Options.auto_forward = not BOT.Options.auto_forward
            BOT.Setting.auto_forward = "On" if BOT.Options.auto_forward else "Off"
            await cq.answer(f"AutoFwd {BOT.Setting.auto_forward}", show_alert=True)
            await send_settings(client, cq.message, cq.message.id, False)
    elif data in ["media","document"]:
        BOT.Options.stream_upload = data == "media"
        BOT.Setting.stream_upload = "Media" if data == "media" else "Document"
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "close":
        await cq.message.delete()
    elif data == "back":
        await send_settings(client, cq.message, cq.message.id, False)
    elif data == "cancel":
        await cancelTask("Cancelled by user")


async def _show_type_menu(msg, session):
    v = len(session["video"])
    a = len(session["audio"])
    s = len(session["subs"])
    title = session["title"]
    await msg.edit_text(
        "🎞 <b>STREAM EXTRACTOR</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌  <b>{title}</b>\n\n"
        f"🎬  Video tracks     <code>{v}</code>\n"
        f"🎵  Audio tracks     <code>{a}</code>\n"
        f"💬  Subtitles        <code>{s}</code>\n\n"
        "Choose track type:",
        reply_markup=kb_type(v, a, s)
    )


# ══════════════════════════════════════════════
#  Photo → thumbnail
# ══════════════════════════════════════════════

@colab_bot.on_message(filters.photo & filters.private)
async def handle_photo(client, message):
    msg = await message.reply_text("⏳ <i>Saving thumbnail...</i>")
    if await setThumbnail(message):
        await msg.edit_text("✅ Thumbnail updated.")
        await message.delete()
    else:
        await msg.edit_text("❌ Could not set thumbnail.")
    await sleep(10)
    await message_deleter(message, msg)


# ══════════════════════════════════════════════
#  Document → sous-titre pour FC Hardsub manuel, sous-titre/audio pour Media Tools
# ══════════════════════════════════════════════

@colab_bot.on_message(filters.document & filters.private)
async def handle_subtitle_document(client, message):
    if not _owner(message):
        return

    reply_id = message.reply_to_message_id

    # ── Cas 1 : sous-titre pour le hardsub FreeConvert existant (lien distant) ──
    if reply_id in _pending_fc_subtitle:
        pending = _pending_fc_subtitle.get(reply_id)
        file_name = message.document.file_name or ""
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in (".ass", ".srt", ".ssa"):
            await message.reply_text(
                "❌ Envoie un fichier <code>.ass</code> ou <code>.srt</code> valide.",
                quote=True,
            )
            return
        _pending_fc_subtitle.pop(reply_id, None)
        status_msg = await message.reply_text("⏳ <i>Sous-titre reçu, démarrage du hardsub...</i>")
        await message.delete()
        os.makedirs(Paths.WORK_PATH, exist_ok=True)
        subtitle_path = os.path.join(Paths.WORK_PATH, f"manual_sub_{uuid4().hex[:8]}{ext}")
        await message.download(file_name=subtitle_path)
        get_event_loop().create_task(
            Direct_FC_Hardsub_Handler(pending["url"], pending["name"], subtitle_path, status_msg)
        )
        return

    # ── Cas 2 : fichier compagnon pour Merge / Hardsub Local (Media Tools) ──
    if reply_id in _pending_media_input:
        pending = _pending_media_input.get(reply_id)
        file_name = message.document.file_name or ""
        ext = os.path.splitext(file_name)[1].lower()

        if pending["action"] == "merge":
            audio_exts = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac", ".opus")
            if ext not in audio_exts:
                await message.reply_text("❌ Envoie un fichier audio valide.", quote=True)
                return
            _pending_media_input.pop(reply_id, None)
            os.makedirs(Paths.WORK_PATH, exist_ok=True)
            local_path = os.path.join(Paths.WORK_PATH, f"companion_{uuid4().hex[:8]}{ext}")
            status = await message.reply_text("⏳ <i>Starting...</i>")
            await message.download(
                file_name=local_path,
                progress=await _make_dl_progress_cb(status, "TÉLÉCHARGEMENT AUDIO"),
            )
            await message.delete()
            try:
                video_path = await _ensure_local_video(pending["video_msg_id"], client, status)
                out = await media_tools.merge_audio_video(video_path, local_path, status)
                from colab_leecher.uploader.telegram import upload_file
                await upload_file(out, os.path.basename(out), is_last=True)
                await status.delete()
            except Exception as e:
                await status.edit_text(f"❌ <b>Erreur</b>\n<code>{e}</code>")
            return

        if pending["action"] == "hardsub":
            if ext not in (".ass", ".srt", ".ssa"):
                await message.reply_text("❌ Envoie un fichier <code>.ass</code> ou <code>.srt</code> valide.", quote=True)
                return
            _pending_media_input.pop(reply_id, None)
            os.makedirs(Paths.WORK_PATH, exist_ok=True)
            local_path = os.path.join(Paths.WORK_PATH, f"companion_{uuid4().hex[:8]}{ext}")
            status = await message.reply_text("⏳ <i>Starting...</i>")
            await message.download(
                file_name=local_path,
                progress=await _make_dl_progress_cb(status, "TÉLÉCHARGEMENT SOUS-TITRE"),
            )
            await message.delete()
            try:
                video_path = await _ensure_local_video(pending["video_msg_id"], client, status)
                out = await media_tools.hardsub_local(video_path, local_path, status)
                from colab_leecher.uploader.telegram import upload_file
                await upload_file(out, os.path.basename(out), is_last=True)
                await status.delete()
            except Exception as e:
                await status.edit_text(f"❌ <b>Erreur</b>\n<code>{e}</code>")
            return

    # ── Cas 3 : rien en attente → comportement d'origine (on ignore) ──
    return


# ══════════════════════════════════════════════
#  Import nyaa_tracker (registers its handlers)
# ══════════════════════════════════════════════

try:
    import colab_leecher.nyaa_tracker
    logging.info("📡 Nyaa tracker loaded")
except Exception as e:
    logging.warning(f"Nyaa tracker not loaded: {e}")


logging.info("⚡ Zilong started.")
get_event_loop().create_task(_startup_welcome())
colab_bot.run()
