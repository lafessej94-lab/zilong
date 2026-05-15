import logging
import re
import subprocess
import time
from datetime import datetime

from colab_leecher.utility.helper import sizeUnit, status_bar
from colab_leecher.utility.variables import (
    BOT,
    Aria2c,
    Paths,
    Messages,
    BotTimes,
    ProcessTracker,
    TaskInfo,
)


STALL_TIMEOUT_S = 900


async def aria2_Download(link: str, num: int):
    global BotTimes, Messages
    name_d = get_Aria2c_Name(link)
    BotTimes.task_start = datetime.now()
    Messages.status_head = (
        f"<b>DOWNLOADING FROM » </b><i>Link {str(num).zfill(2)}</i>\n\n"
        f"<b>Name » </b><code>{name_d}</code>\n"
    )

    TaskInfo.set(
        phase="download",
        engine="Aria2c",
        filename=name_d,
        started_at=datetime.now().timestamp(),
    )

    command = [
        "aria2c",
        "-x16",
        "--seed-time=0",
        "--summary-interval=1",
        "--max-tries=3",
        "--bt-stop-timeout=900",
        "--console-log-level=notice",
        "-d",
        Paths.down_path,
        link,
    ]

    proc = subprocess.Popen(
        command,
        bufsize=1,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    ProcessTracker.register(proc.pid, f"aria2c: {name_d[:30]}")

    last_progress = time.monotonic()
    try:
        while True:
            output = proc.stdout.readline()
            if output == "" and proc.poll() is not None:
                break
            if output:
                progressed = await on_output(output)
                if progressed:
                    last_progress = time.monotonic()
                logging.info("[aria2] %s", output.strip())
            if proc.poll() is None and time.monotonic() - last_progress > STALL_TIMEOUT_S:
                proc.terminate()
                raise RuntimeError(f"aria2c stalled for too long while downloading {name_d}")
    finally:
        ProcessTracker.unregister(proc.pid)

    exit_code = proc.wait()
    if exit_code != 0:
        if exit_code == 3:
            raise RuntimeError(f"The resource was not found: {link}")
        if exit_code == 9:
            raise RuntimeError("Not enough disk space available.")
        if exit_code == 24:
            raise RuntimeError("HTTP authorization failed.")
        raise RuntimeError(f"aria2c download failed with return code {exit_code} for {link}.")


def get_Aria2c_Name(link):
    if len(BOT.Options.custom_name) != 0:
        return BOT.Options.custom_name
    cmd = f'aria2c -x10 --dry-run --file-allocation=none "{link}"'
    result = subprocess.run(cmd, stdout=subprocess.PIPE, shell=True)
    stdout_str = result.stdout.decode("utf-8", errors="replace")
    filename = stdout_str.split("complete: ")[-1].split("\n")[0]
    name = filename.split("/")[-1]
    if len(name) == 0:
        name = "UNKNOWN DOWNLOAD NAME"
    return name


async def on_output(output: str) -> bool:
    total_size = "0B"
    progress_percentage = "0B"
    downloaded_bytes = "0B"
    eta = "0S"

    if "ETA:" not in output:
        return False

    try:
        parts = output.split()
        total_size = parts[1].split("/")[1]
        total_size = total_size.split("(")[0]
        progress_percentage = parts[1][parts[1].find("(") + 1 : parts[1].find(")")]
        downloaded_bytes = parts[1].split("/")[0]
        eta = parts[4].split(":")[1][:-1]
    except Exception as exc:
        logging.debug("Could not parse aria2 line: %s", exc)
        return False

    pct_match = re.findall(r"\d+\.\d+|\d+", progress_percentage)
    down_match = re.findall(r"\d+\.\d+|\d+", downloaded_bytes)
    unit_match = re.findall("[a-zA-Z]+", downloaded_bytes)
    if not pct_match or not down_match or not unit_match:
        return False

    percentage = pct_match[0]
    down = down_match[0]
    down_unit = unit_match[0]
    if "G" in down_unit:
        spd = 3
    elif "M" in down_unit:
        spd = 2
    elif "K" in down_unit:
        spd = 1
    else:
        spd = 0

    elapsed_time_seconds = (datetime.now() - BotTimes.task_start).seconds
    if elapsed_time_seconds >= 270 and not Aria2c.link_info:
        logging.error("Failed to get download information. Probably a dead link.")

    if total_size != "0B":
        Aria2c.link_info = True
        current_speed = (float(down) * 1024**spd) / elapsed_time_seconds if elapsed_time_seconds else 0
        speed_string = f"{sizeUnit(current_speed)}/s"

        TaskInfo.set(
            percentage=float(percentage),
            speed=speed_string,
            eta=eta,
        )

        await status_bar(
            Messages.status_head,
            speed_string,
            int(percentage),
            eta,
            downloaded_bytes,
            total_size,
            "Aria2c",
        )
        return True
    return False
