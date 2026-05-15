# @title 🖥️ Zilong Code

API_ID    = 0                              # @param {type: "integer"}
API_HASH  = ""   # @param {type: "string"}
BOT_TOKEN = ""  # @param {type: "string"}
USER_ID   = 0                           # @param {type: "integer"}
DUMP_ID   = 0                                     # @param {type: "integer"} — unused, keep as 0
CC_API_KEY = ""  # @param {type: "string"}
SEEDR_USERNAME = ""  # @param {type: "string"}
SEEDR_PASSWORD = ""  # @param {type: "string"}
SEEDR_PROXY = ""  # @param {type: "string"}

import subprocess, time, json, shutil, os, sys
from IPython.display import clear_output
from threading import Thread

Working = True

banner = '''
 ███████╗██╗██╗██╗      ██████╗ ███╗   ██╗ ██████╗
 ╚══███╔╝██║██║██║     ██╔═══██╗████╗  ██║██╔════╝
   ███╔╝ ██║██║██║     ██║   ██║██╔██╗ ██║██║  ███╗
  ███╔╝  ██║██║██║     ██║   ██║██║╚██╗██║██║   ██║
 ███████╗██║██║███████╗╚██████╔╝██║ ╚████║╚██████╔╝
 ╚══════╝╚═╝╚═╝╚══════╝ ╚═════╝ ╚═╝  ╚═══╝ ╚═════╝

  ██████╗ ██████╗ ██████╗ ███████╗
 ██╔════╝██╔═══██╗██╔══██╗██╔════╝
 ██║     ██║   ██║██║  ██║█████╗
 ██║     ██║   ██║██║  ██║██╔══╝
 ╚██████╗╚██████╔╝██████╔╝███████╗
  ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝
'''
print(banner)

def Loading():
    white = 37
    black = 0
    while Working:
        print("\r" + "░"*white + "▒▒"+ "▓"*black + "▒▒" + "░"*white, end="")
        black = (black + 2) % 75
        white = (white - 1) if white != 0 else 37
        time.sleep(2)
    clear_output()

_Thread = Thread(target=Loading, name="Prepare", args=())
_Thread.start()

if os.path.exists("/content/sample_data"):
    shutil.rmtree("/content/sample_data")

subprocess.run("git clone https://github.com/vicMenma/zilong.git", shell=True)
subprocess.run("apt update && apt install -y ffmpeg aria2", shell=True)
subprocess.run("pip3 install -r /content/zilong/requirements.txt", shell=True)

credentials = {
    "API_ID":    API_ID,
    "API_HASH":  API_HASH,
    "BOT_TOKEN": BOT_TOKEN,
    "USER_ID":   USER_ID,
    "DUMP_ID":   DUMP_ID,
    "CC_API_KEY": CC_API_KEY,
    "SEEDR_USERNAME": SEEDR_USERNAME,
    "SEEDR_PASSWORD": SEEDR_PASSWORD,
    "SEEDR_PROXY": SEEDR_PROXY,
}

with open('/content/zilong/credentials.json', 'w') as f:
    json.dump(credentials, f)

Working = False

if os.path.exists("/content/zilong/my_bot.session"):
    os.remove("/content/zilong/my_bot.session")

print("\rStarting Bot....")
os.makedirs("/content/zilong/data", exist_ok=True)
log_path = "/content/zilong/data/zilong.log"
print(f"Live logs will stream below. File log: {log_path}")

proc = subprocess.Popen(
    ["python3", "-m", "colab_leecher"],
    cwd="/content/zilong",
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    encoding="utf-8",
    errors="replace",
    bufsize=1,
)

try:
    while True:
        line = proc.stdout.readline()
        if line == "" and proc.poll() is not None:
            break
        if line:
            print(line, end="")
            sys.stdout.flush()
finally:
    if proc.stdout:
        proc.stdout.close()

return_code = proc.wait()
print(f"\nBot process exited with code {return_code}")
