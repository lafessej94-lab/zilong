# colab_leecher/__init__.py
import json
import logging
import asyncio
import os
from pathlib import Path
from uvloop import install
from pyrogram.client import Client
CREDENTIALS_PATH = Path("/content/zilong/credentials.json")
LOG_DIR = Path("/content/zilong/data")
LOG_PATH = LOG_DIR / "zilong.log"
def configure_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(stream_handler)
    root.addHandler(file_handler)
configure_logging()
log = logging.getLogger(__name__)
def load_credentials(path: Path = CREDENTIALS_PATH) -> dict:
    """Load and validate credentials from JSON file."""
    if not path.exists():
        raise FileNotFoundError(f"Credentials file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        creds = json.load(f)
    required_keys = ["API_ID", "API_HASH", "BOT_TOKEN", "USER_ID", "DUMP_ID"]
    missing = [k for k in required_keys if k not in creds]
    if missing:
        raise KeyError(f"Missing keys in credentials.json: {missing}")
    return creds
# Load credentials
credentials = load_credentials()
API_ID = int(credentials["API_ID"])
API_HASH = str(credentials["API_HASH"])
BOT_TOKEN = str(credentials["BOT_TOKEN"])
OWNER = int(credentials["USER_ID"])
DUMP_ID = str(credentials["DUMP_ID"])
CC_API_KEY = str(credentials.get("CC_API_KEY", "") or "")
FC_API_KEY = str(credentials.get("FC_API_KEY", "") or "")
SEEDR_USERNAME = str(credentials.get("SEEDR_USERNAME", "") or "")
SEEDR_PASSWORD = str(credentials.get("SEEDR_PASSWORD", "") or "")
SEEDR_PROXY = str(credentials.get("SEEDR_PROXY", "") or "")
if CC_API_KEY:
    os.environ.setdefault("CC_API_KEY", CC_API_KEY)
if FC_API_KEY:
    os.environ.setdefault("FC_API_KEY", FC_API_KEY)
if SEEDR_USERNAME:
    os.environ.setdefault("SEEDR_USERNAME", SEEDR_USERNAME)
if SEEDR_PASSWORD:
    os.environ.setdefault("SEEDR_PASSWORD", SEEDR_PASSWORD)
if SEEDR_PROXY:
    os.environ.setdefault("SEEDR_PROXY", SEEDR_PROXY)
log.info("Credentials loaded successfully")
# Use uvloop as event loop policy
install()
# Explicitly create and set an event loop for the main thread
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
# Create Pyrogram client using the current loop
colab_bot = Client(
    "my_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    max_concurrent_transmissions=6,  # défaut pyrofork = 1 (un seul flux) -> upload/download en série
    sleep_threshold=120,             # laisse pyrofork absorber les FloodWait courts sans planter
)
log.info("Pyrogram Client initialized")
