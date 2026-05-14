import os
import shutil
import logging
import pathlib
from asyncio import sleep
from time import time
from colab_leecher import OWNER, CC_API_KEY, colab_bot
from natsort import natsorted
from datetime import datetime
from os import makedirs, path as ospath
from colab_leecher.cloudconvert import (
    cc_mode_label,
    compress_file,
    convert_file,
    quality_label,
    resize_file,
    resize_label,
)
from colab_leecher.uploader.telegram import upload_file
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from colab_leecher.utility.variables import (
    BOT, MSG, BotTimes, Messages, Paths, Transfer, ProcessTracker, TaskInfo,
)
from colab_leecher.utility.converters import archive, extract, videoConverter, sizeChecker
from colab_leecher.utility.helper import (
    fileType, getSize, getTime, keyboard,
    shortFileName, sizeUnit, sysINFO,
)


async def Leech(folder_path: str, remove: bool, convert_videos: bool = True):
    files = [str(p) for p in pathlib.Path(folder_path).glob("**/*") if p.is_file()]
    for f in natsorted(files):
        fp = ospath.join(folder_path, f)
        if convert_videos and BOT.Options.convert_video and fileType(fp) == "video":
            await videoConverter(fp)

    Transfer.total_down_size = getSize(folder_path)

    files = natsorted([str(p) for p in pathlib.Path(folder_path).glob("**/*") if p.is_file()])
    upload_queue = []

    for f in files:
        file_path = ospath.join(folder_path, f)
        leech = await sizeChecker(file_path, remove)
        if leech:
            if ospath.exists(file_path) and remove:
                os.remove(file_path)
            for part in natsorted(os.listdir(Paths.temp_zpath)):
                upload_queue.append(("split", ospath.join(Paths.temp_zpath, part)))
        else:
            upload_queue.append(("single", file_path))

    total_uploads    = len(upload_queue)
    split_cleaned    = False

    for idx, (kind, file_path) in enumerate(upload_queue):
        is_last = (idx == total_uploads - 1)

        # Update TaskInfo for /status panel
        TaskInfo.set(
            phase="upload", engine="Pyrofork",
            filename=ospath.basename(file_path),
        )

        if kind == "split":
            file_name = ospath.basename(file_path)
            new_path  = shortFileName(file_path)
            os.rename(file_path, new_path)
            BotTimes.current_time = time()
            Messages.status_head  = (
                f"📤 <b>UPLOADING</b>  <i>{idx+1} / {total_uploads}</i>\n\n"
                f"<code>{file_name}</code>\n"
            )
            try:
                MSG.status_msg = await MSG.status_msg.edit_text(
                    text=Messages.task_msg + Messages.status_head
                    + "\n⏳ <i>Starting...</i>" + sysINFO(),
                    reply_markup=keyboard(),
                )
            except Exception: pass
            await upload_file(new_path, file_name, is_last=is_last)
            Transfer.up_bytes.append(os.stat(new_path).st_size)
            if is_last and not split_cleaned:
                if ospath.exists(Paths.temp_zpath): shutil.rmtree(Paths.temp_zpath)
                split_cleaned = True
        else:
            if not ospath.exists(Paths.temp_files_dir): makedirs(Paths.temp_files_dir)
            if not remove: file_path = shutil.copy(file_path, Paths.temp_files_dir)
            file_name = ospath.basename(file_path)
            new_path  = shortFileName(file_path)
            os.rename(file_path, new_path)
            BotTimes.current_time = time()
            Messages.status_head  = f"📤 <b>UPLOADING</b>\n\n<code>{file_name}</code>\n"
            try:
                MSG.status_msg = await MSG.status_msg.edit_text(
                    text=Messages.task_msg + Messages.status_head
                    + "\n⏳ <i>Starting...</i>" + sysINFO(),
                    reply_markup=keyboard(),
                )
            except Exception: pass
            file_size = os.stat(new_path).st_size
            await upload_file(new_path, file_name, is_last=is_last)
            Transfer.up_bytes.append(file_size)
            if remove and ospath.exists(new_path): os.remove(new_path)
            elif not remove:
                for fi in os.listdir(Paths.temp_files_dir):
                    os.remove(ospath.join(Paths.temp_files_dir, fi))

    if remove and ospath.exists(folder_path): shutil.rmtree(folder_path)
    for d in (Paths.thumbnail_ytdl, Paths.temp_files_dir):
        if ospath.exists(d): shutil.rmtree(d)


async def CloudConvert_Handler(folder_path: str, remove: bool):
    if not CC_API_KEY.strip():
        await cancelTask("CloudConvert API key is missing in your Colab launcher.")
        return

    files = natsorted([str(p) for p in pathlib.Path(folder_path).glob("**/*") if p.is_file()])
    video_files = [f for f in files if fileType(f) == "video"]
    if not video_files:
        await cancelTask("CloudConvert mode needs at least one video file.")
        return

    if ospath.exists(Paths.temp_cc_path):
        shutil.rmtree(Paths.temp_cc_path)
    makedirs(Paths.temp_cc_path)

    for f in files:
        if fileType(f) == "video":
            continue
        rel = ospath.relpath(f, folder_path)
        dest = ospath.join(Paths.temp_cc_path, rel)
        os.makedirs(ospath.dirname(dest), exist_ok=True)
        shutil.copy2(f, dest)

    total_videos = len(video_files)

    for idx, video_path in enumerate(video_files):
        rel = ospath.relpath(video_path, folder_path)
        out_dir = ospath.join(Paths.temp_cc_path, ospath.dirname(rel))
        os.makedirs(out_dir, exist_ok=True)
        display_name = ospath.basename(video_path)
        chunk_start = idx / total_videos * 100.0
        chunk_end = (idx + 1) / total_videos * 100.0
        stage_state = {"last": 0.0}

        async def _cc_update(stage: str, pct: float, detail: str) -> None:
            now = time()
            if now - stage_state["last"] < 2 and pct < 100:
                return
            stage_state["last"] = now
            overall = chunk_start + ((chunk_end - chunk_start) * max(0.0, min(pct, 100.0)) / 100.0)
            TaskInfo.set(
                phase="process",
                engine="CloudConvert",
                filename=display_name,
                percentage=overall,
                speed=detail,
                eta="-",
            )
            text = (
                f"☁️ <b>CLOUDCONVERT</b>\n\n"
                f"<code>{display_name}</code>\n\n"
                f"<b>Stage</b>  <code>{stage}</code>\n"
                f"<b>Progress</b>  <code>{overall:.1f}%</code>\n"
                f"<b>Mode</b>  <code>{cc_mode_label(BOT.Options.cc_engine_mode)}</code>\n"
                f"<b>Preset</b>  <code>{quality_label(BOT.Options.cc_quality_profile)}</code>\n"
                f"<b>Detail</b>  <code>{detail}</code>"
            )
            try:
                await MSG.status_msg.edit_text(
                    text=Messages.task_msg + text + sysINFO(),
                    reply_markup=keyboard(),
                    disable_web_page_preview=True,
                )
            except Exception:
                pass

        upload_cb = lambda pct, detail: _cc_update("Upload", pct * 0.35, detail)
        process_cb = lambda pct, detail: _cc_update("Process", 35.0 + (pct * 0.5), detail)
        download_cb = lambda pct, detail: _cc_update("Download", 85.0 + (pct * 0.15), detail)

        try:
            if BOT.Mode.type == "cc_convert":
                await _cc_update("Convert", 0.0, "Preparing CloudConvert job")
                await convert_file(
                    CC_API_KEY,
                    video_path,
                    out_dir,
                    output_ext=BOT.Options.video_out,
                    cc_mode=BOT.Options.cc_engine_mode,
                    quality_profile=BOT.Options.cc_quality_profile,
                    upload_cb=upload_cb,
                    process_cb=process_cb,
                    download_cb=download_cb,
                )
            elif BOT.Mode.type == "cc_resize":
                await _cc_update("Resize", 0.0, f"Target {resize_label(BOT.Options.cc_resize)}")
                await resize_file(
                    CC_API_KEY,
                    video_path,
                    out_dir,
                    height=BOT.Options.cc_resize,
                    output_ext=BOT.Options.video_out,
                    cc_mode=BOT.Options.cc_engine_mode,
                    quality_profile=BOT.Options.cc_quality_profile,
                    upload_cb=upload_cb,
                    process_cb=process_cb,
                    download_cb=download_cb,
                )
            else:
                await _cc_update("Compress", 0.0, f"Target {BOT.Setting.cc_target_size}")
                await compress_file(
                    CC_API_KEY,
                    video_path,
                    out_dir,
                    target_mb=BOT.Options.cc_target_size_mb,
                    cc_mode=BOT.Options.cc_engine_mode,
                    upload_cb=upload_cb,
                    process_cb=process_cb,
                    download_cb=download_cb,
                )
        except Exception as exc:
            await cancelTask(f"CloudConvert failed: {display_name}\n\n{exc}")
            return

    await Leech(Paths.temp_cc_path, True, convert_videos=False)
    if remove and ospath.exists(folder_path):
        shutil.rmtree(folder_path)


async def Zip_Handler(down_path: str, is_split: bool, remove: bool):
    Messages.status_head = f"🗜 <b>COMPRESSING</b>\n\n<code>{Messages.download_name}</code>\n"
    TaskInfo.set(phase="process", engine="zip", filename=Messages.download_name)
    try:
        MSG.status_msg = await MSG.status_msg.edit_text(
            text=Messages.task_msg + Messages.status_head + sysINFO(),
            reply_markup=keyboard(),
        )
    except Exception: pass
    if not ospath.exists(Paths.temp_zpath): makedirs(Paths.temp_zpath)
    await archive(down_path, is_split, remove)
    await sleep(2)
    Transfer.total_down_size = getSize(Paths.temp_zpath)
    if remove and ospath.exists(down_path): shutil.rmtree(down_path)


async def Unzip_Handler(down_path: str, remove: bool):
    Messages.status_head = f"📂 <b>EXTRACTING</b>\n\n<code>{Messages.download_name}</code>\n"
    TaskInfo.set(phase="process", engine="unzip", filename=Messages.download_name)
    try:
        MSG.status_msg = await MSG.status_msg.edit_text(
            text=Messages.task_msg + Messages.status_head
            + "\n⏳ <i>Starting...</i>" + sysINFO(),
            reply_markup=keyboard(),
        )
    except Exception: pass
    filenames = natsorted([str(p) for p in pathlib.Path(down_path).glob("**/*") if p.is_file()])
    for f in filenames:
        short_path = ospath.join(down_path, f)
        if not ospath.exists(Paths.temp_unzip_path): makedirs(Paths.temp_unzip_path)
        _, ext = ospath.splitext(ospath.basename(f).lower())
        try:
            if ospath.exists(short_path):
                if ext in [".7z", ".gz", ".zip", ".rar", ".001", ".tar", ".z01"]:
                    await extract(short_path, remove)
                else:
                    shutil.copy(short_path, Paths.temp_unzip_path)
        except Exception as e:
            logging.warning(f"Unzip error: {e}")
    if remove: shutil.rmtree(down_path)


# ═════════════════════════════════════════════════════════════
# cancelTask — FIXED: now kills ALL subprocesses
# ═════════════════════════════════════════════════════════════

async def cancelTask(reason: str):
    """
    Kill the running task AND every subprocess it spawned.

    The old version only called BOT.TASK.cancel() which cancels the
    Python asyncio coroutine but leaves aria2c/ffmpeg/yt-dlp running
    as orphan processes. ProcessTracker.kill_all() sends SIGTERM to
    every registered PID — this is why tasks actually stop now.
    """
    spent = getTime((datetime.now() - BotTimes.start_time).seconds)

    # 1. Kill ALL tracked subprocesses (aria2c, ffmpeg, yt-dlp, etc.)
    killed = ProcessTracker.kill_all()

    # 2. Cancel the asyncio task
    if BOT.State.task_going:
        try:
            if BOT.TASK and not BOT.TASK.done():
                BOT.TASK.cancel()
        except Exception as e:
            logging.warning(f"Task cancel: {e}")

    # 3. Also kill any stray aria2c/ffmpeg processes by name
    _kill_stray_processes()

    # 4. Cleanup work directory
    try:
        if ospath.exists(Paths.WORK_PATH):
            shutil.rmtree(Paths.WORK_PATH)
    except Exception as e:
        logging.warning(f"Cancel cleanup: {e}")

    # 5. Reset state
    BOT.State.task_going = False
    TaskInfo.reset()

    text = (
        "⛔ <b>TASK CANCELLED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"❓  <b>Reason</b>   <i>{reason}</i>\n"
        f"⏱  <b>Spent</b>    <code>{spent}</code>\n"
        f"💀  <b>Killed</b>   <code>{killed} process(es)</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>All downloads, uploads and processing stopped.</i>"
    )

    try:
        await MSG.status_msg.edit_text(text)
    except Exception:
        try:
            await colab_bot.send_message(chat_id=OWNER, text=text)
        except Exception:
            pass

    logging.info(f"[Cancel] Task cancelled: {reason} — killed {killed} procs")


def _kill_stray_processes():
    """Kill any aria2c/ffmpeg/yt-dlp that might have been missed."""
    import subprocess
    for name in ("aria2c", "ffmpeg", "ffprobe"):
        try:
            subprocess.run(
                ["pkill", "-f", name],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass


async def SendLogs(is_leech: bool):
    BOT.State.started    = False
    BOT.State.task_going = False
    TaskInfo.reset()
