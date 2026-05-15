import logging
import os
import platform
import psutil
from datetime import datetime
from asyncio import sleep, get_event_loop
from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from colab_leecher import CC_API_KEY, SEEDR_PASSWORD, SEEDR_USERNAME, colab_bot, OWNER
from colab_leecher.cloudconvert import cc_mode_label, quality_label, resize_label
from colab_leecher.utility.handler import (
    Seedr_CC_Convert_Handler,
    Seedr_CC_Hardsub_Handler,
    cancelTask,
)
from colab_leecher.utility.variables import (
    BOT, MSG, BotTimes, Paths, Messages, ProcessTracker, TaskInfo,
)
from colab_leecher.utility.task_manager import taskScheduler
from colab_leecher.utility.helper import (
    isLink, setThumbnail, message_deleter, send_settings,
    sizeUnit, getTime, is_ytdl_link, _pct_bar, _speed_emoji,
)
from colab_leecher.stream_extractor import (
    analyse, get_session, clear_session,
    kb_type, kb_video, kb_audio, kb_subs,
    dl_video, dl_audio, dl_sub,
)

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
        "🎞 Stream Extractor (any link)\n"
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
        "🎞 <b>Stream Extractor</b> — tap 🎞 Streams on any link\n"
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

    await message.reply_text(
        f"{label}  ·  <code>{n}</code> source(s)\n<b>Choose mode:</b>",
        reply_markup=_mode_keyboard(), quote=True,
    )


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
        magnet = (BOT.SOURCE or [""])[0].strip()
        if not magnet.startswith("magnet:?xt=urn:btih:"):
            await cq.answer("Seedr mode currently needs a magnet link.", show_alert=True)
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
        if data == "seedr_cc_convert":
            BOT.Mode.type = data
            BOT.TASK = get_event_loop().create_task(Seedr_CC_Convert_Handler(magnet))
        else:
            BOT.Mode.type = data
            BOT.TASK = get_event_loop().create_task(Seedr_CC_Hardsub_Handler(magnet))
        await BOT.TASK
        BOT.State.task_going = False
        TaskInfo.reset()
        return

    # ════════════════════════════════════════════
    #  STREAM EXTRACTOR
    # ════════════════════════════════════════════

    if data == "sx_open":
        url = (BOT.SOURCE or [None])[0]
        if not url:
            await cq.answer("No URL found.", show_alert=True); return

        await cq.message.edit_text(
            "🎞 <b>STREAM EXTRACTOR</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"⏳ <i>Analyzing streams...</i>\n"
            f"<code>{url[:70]}{'…' if len(url)>70 else ''}</code>"
        )

        session = await analyse(url, chat_id)

        if not session or (not session["video"] and not session["audio"] and not session["subs"]):
            await cq.message.edit_text(
                "🎞 <b>STREAM EXTRACTOR</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "❌ Could not extract streams.\n"
                "<i>Only yt-dlp compatible sources are supported.</i>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⏎ Back", callback_data="sx_back")
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
#  Import nyaa_tracker (registers its handlers)
# ══════════════════════════════════════════════

try:
    import colab_leecher.nyaa_tracker
    logging.info("📡 Nyaa tracker loaded")
except Exception as e:
    logging.warning(f"Nyaa tracker not loaded: {e}")


logging.info("⚡ Zilong started.")
colab_bot.run()
