import pytz
import shutil
import logging
from time import time
from datetime import datetime
from asyncio import sleep
from os import makedirs, path as ospath, system
from colab_leecher import OWNER, colab_bot
from colab_leecher.downlader.manager import calDownSize, get_d_name, downloadManager
from colab_leecher.utility.helper import (
    getSize, applyCustomName, keyboard, sysINFO,
    is_google_drive, is_telegram, is_ytdl_link,
    is_mega, is_terabox, is_torrent,
)
from colab_leecher.utility.handler import (
    CloudConvert_Handler, Leech, Unzip_Handler, Zip_Handler, SendLogs, cancelTask,
)
from colab_leecher.utility.variables import (
    BOT, MSG, BotTimes, Messages, Paths, Aria2c, Transfer, TaskError,
)


async def task_starter(message, text):
    await message.delete()
    BOT.State.started = True
    return await message.reply_text(text)


async def taskScheduler():
    global BOT, MSG, BotTimes, Messages, Paths, Transfer, TaskError

    is_dualzip = BOT.Mode.type == "undzip"
    is_unzip   = BOT.Mode.type == "unzip"
    is_zip     = BOT.Mode.type == "zip"
    is_cc      = BOT.Mode.type in {"cc_convert", "cc_resize", "cc_compress"}
    is_dir     = BOT.Mode.mode == "dir-leech"

    # Reset
    Messages.download_name   = ""
    Messages.task_msg        = ""
    Messages.status_head     = "<b>📥 DOWNLOADING</b>\n"
    Transfer.sent_file       = []
    Transfer.sent_file_names = []
    Transfer.down_bytes      = [0, 0]
    Transfer.up_bytes        = [0, 0]

    # Validate dir-leech
    if is_dir:
        if not ospath.exists(BOT.SOURCE[0]):
            TaskError.state = True
            TaskError.text  = "Directory not found."
            logging.error(TaskError.text)
            return
        if not ospath.exists(Paths.temp_dirleech_path):
            makedirs(Paths.temp_dirleech_path)
        Transfer.total_down_size = getSize(BOT.SOURCE[0])
        Messages.download_name   = ospath.basename(BOT.SOURCE[0])

    # Prepare work directory
    if ospath.exists(Paths.WORK_PATH):
        shutil.rmtree(Paths.WORK_PATH)
    makedirs(Paths.WORK_PATH)
    makedirs(Paths.down_path)

    # ── No task-log message, no hero photo.
    # MSG.status_msg was already sent by __main__ as "⏳ Starting..."
    # We just edit it throughout. ─────────────────────────────────────

    await calDownSize(BOT.SOURCE)

    if not is_dir:
        await get_d_name(BOT.SOURCE[0])
    else:
        Messages.download_name = ospath.basename(BOT.SOURCE[0])

    if is_zip:
        Paths.down_path = ospath.join(Paths.down_path, Messages.download_name)
        if not ospath.exists(Paths.down_path):
            makedirs(Paths.down_path)

    BotTimes.current_time = time()

    if BOT.Mode.mode != "mirror":
        await Do_Leech(BOT.SOURCE, is_dir, BOT.Mode.ytdl, is_zip, is_unzip, is_dualzip)
    else:
        await Do_Mirror(BOT.SOURCE, BOT.Mode.ytdl, is_zip, is_unzip, is_dualzip)


async def Do_Leech(source, is_dir, is_ytdl, is_zip, is_unzip, is_dualzip):
    if is_dir:
        for s in source:
            if not ospath.exists(s):
                await cancelTask("Directory not found.")
                return
            Paths.down_path = s
            if is_zip:
                await Zip_Handler(Paths.down_path, True, False)
                await Leech(Paths.temp_zpath, True)
            elif is_unzip:
                await Unzip_Handler(Paths.down_path, False)
                await Leech(Paths.temp_unzip_path, True)
            elif is_dualzip:
                await Unzip_Handler(Paths.down_path, False)
                await Zip_Handler(Paths.temp_unzip_path, True, True)
                await Leech(Paths.temp_zpath, True)
            else:
                if ospath.isdir(s):
                    await Leech(Paths.down_path, False)
                else:
                    Transfer.total_down_size = ospath.getsize(s)
                    makedirs(Paths.temp_dirleech_path)
                    shutil.copy(s, Paths.temp_dirleech_path)
                    Messages.download_name = ospath.basename(s)
                    await Leech(Paths.temp_dirleech_path, True)
    else:
        await downloadManager(source, is_ytdl)
        Transfer.total_down_size = getSize(Paths.down_path)
        applyCustomName()

        if is_zip:
            await Zip_Handler(Paths.down_path, True, True)
            await Leech(Paths.temp_zpath, True)
        elif is_unzip:
            await Unzip_Handler(Paths.down_path, True)
            await Leech(Paths.temp_unzip_path, True)
        elif is_dualzip:
            await Unzip_Handler(Paths.down_path, True)
            await Zip_Handler(Paths.temp_unzip_path, True, True)
            await Leech(Paths.temp_zpath, True)
        elif is_cc:
            await CloudConvert_Handler(Paths.down_path, True)
        else:
            await Leech(Paths.down_path, True)

    await SendLogs(True)


async def Do_Mirror(source, is_ytdl, is_zip, is_unzip, is_dualzip):
    if not ospath.exists(Paths.MOUNTED_DRIVE):
        await cancelTask("Google Drive not mounted.")
        return

    if not ospath.exists(Paths.mirror_dir):
        makedirs(Paths.mirror_dir)

    await downloadManager(source, is_ytdl)
    Transfer.total_down_size = getSize(Paths.down_path)
    applyCustomName()

    mirror_d = ospath.join(
        Paths.mirror_dir,
        datetime.now().strftime("Uploaded » %Y-%m-%d %H:%M:%S"),
    )

    if is_zip:
        await Zip_Handler(Paths.down_path, True, True)
        shutil.copytree(Paths.temp_zpath, mirror_d)
    elif is_unzip:
        await Unzip_Handler(Paths.down_path, True)
        shutil.copytree(Paths.temp_unzip_path, mirror_d)
    elif is_dualzip:
        await Unzip_Handler(Paths.down_path, True)
        await Zip_Handler(Paths.temp_unzip_path, True, True)
        shutil.copytree(Paths.temp_zpath, mirror_d)
    else:
        shutil.copytree(Paths.down_path, mirror_d)

    await SendLogs(False)
