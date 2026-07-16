import math
import logging
import os
import psutil
import random
import subprocess
from time import time
from datetime import datetime
from os import path as ospath
from urllib.parse import urlparse
from asyncio import get_event_loop

from PIL import Image
from pyrogram.errors import BadRequest
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from colab_leecher import CC_API_KEY, DUMP_ID, SEEDR_PASSWORD, SEEDR_USERNAME, colab_bot
from colab_leecher.cloudconvert import cc_mode_label, quality_label, resize_label
from colab_leecher.utility.variables import BOT, MSG, BotTimes, Messages, Paths

# ----------------------------
# Fonction utilitaire locale sizeUnit
# ----------------------------
def sizeUnit(size):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PiB"

# ----------------------------
# INTERFACE SETTINGS (/settings)
# ----------------------------
async def send_settings(client, message, msg_id, command: bool):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎞 Découpage", callback_data="split"),
         InlineKeyboardButton("📂 Format", callback_data="format")],
        [InlineKeyboardButton("☁️ Réglages CC", callback_data="cc"),
         InlineKeyboardButton("🖼 Miniature", callback_data="thumb")],
        [InlineKeyboardButton("✏️ Légende", callback_data="caption"),
         InlineKeyboardButton("⬅️ Préfixe/Suffixe ➡️", callback_data="prefix_suffix")],
        [InlineKeyboardButton("📨 Transfert : OFF", callback_data="autofwd")],
        [InlineKeyboardButton("✖ Fermer", callback_data="close")]
    ])

    text = (
        "⚙️ <b>RÉGLAGES DU BOT</b>\n"
        "------------------\n\n"
        "🎥 Vidéo : Auto · MP4 · Haute\n"
        "✂️ Découpage : Par partie\n\n"
        "☁️ CloudConvert : Balanced · Balanced\n"
        "📐 Resize/Cible : 720p · 100 Mo\n"
        f"🔑 CC API : {'✅ Prête' if CC_API_KEY else '❌ Manquante'}\n\n"
        f"🧲 Seedr : {'✅ Prêt' if SEEDR_USERNAME and SEEDR_PASSWORD else '❌ Manquant'}\n"
        f"📨 Transfert auto : {'ON' if BOT.Options.auto_forward else 'OFF'}\n"
        f"📦 Canal dump : {'✅ Configuré' if DUMP_ID else '❌ Non configuré'}\n\n"
        "📤 Upload : Document/Media\n"
        "✏️ Légende : Monospace\n"
        "⬅️ Préfixe/Suffixe : _–_\n"
        f"🖼 Miniature : {'✅ Définie' if BOT.Setting.thumbnail else '❌ Non définie'}"
    )
    try:
        if command:
            await message.reply_text(text=text, reply_markup=kb)
        else:
            await colab_bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=msg_id,
                text=text,
                reply_markup=kb,
            )
    except Exception as exc:
        logging.warning(f"Settings error: {exc}")

# ----------------------------
# INTERFACE MAGNET DÉTECTÉ
# ----------------------------
def magnet_menu(file_name: str):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📄 Normal", callback_data="normal"),
         InlineKeyboardButton("🗜 Compresser", callback_data="compress")],
        [InlineKeyboardButton("📂 Extraire", callback_data="extract"),
         InlineKeyboardButton("📦 Ré-archiver", callback_data="rearchive")],
        [InlineKeyboardButton("☁️ Convertir", callback_data="cc_convert"),
         InlineKeyboardButton("📐 Redimensionner", callback_data="cc_resize")],
        [InlineKeyboardButton("🗜 CC Compresser", callback_data="cc_compress")],
        [InlineKeyboardButton("🧲 Seedr+CC Convert", callback_data="seedr_cc_convert")],
        [InlineKeyboardButton("💬 CC Hardsub", callback_data="cc_hardsub"),
         InlineKeyboardButton("💬 FC Hardsub", callback_data="fc_hardsub")],
        [InlineKeyboardButton("🎶 Extraire pistes (streams)", callback_data="streams")],
        [InlineKeyboardButton("✖ Fermer", callback_data="close")]
    ])

    text = (
        f"🧲 <b>Magnet détecté</b>\n"
        f"{file_name}\n\n"
        "Choisis un mode :"
    )
    return text, kb

# ----------------------------
# STATUS BAR (Téléchargement/Upload)
# ----------------------------
async def status_bar(down_msg, speed, percentage, eta, done, left, engine, status_msg=None):
    target_msg = status_msg or MSG.status_msg
    bar = "█" * int(percentage/10) + "░" * (10-int(percentage/10))
    pct_str = f"<b>{percentage:.1f}%</b>"
    elapsed = (datetime.now() - BotTimes.start_time).seconds

    text = (
        f"📥 <b>Téléchargement</b>\n"
        f"{down_msg}\n\n"
        f"[{bar}] {pct_str}\n"
        f"⚡ Vitesse : {speed}\n"
        f"⏳ Temps restant : {eta}\n"
        f"🕰 Écoulé : {elapsed}s\n"
        f"⚙️ Moteur : {engine}\n\n"
        f"✅ Fait : {done}\n"
        f"📦 Total : {left}\n\n"
        f"🖥 CPU : {psutil.cpu_percent()}%\n"
        f"💾 RAM : {sizeUnit(psutil.Process(os.getpid()).memory_info().rss)}\n"
        f"💿 Disque libre : {sizeUnit(psutil.disk_usage('/').free)}"
    )
    try:
        await target_msg.edit_text(
            text=text,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔵 Annuler", callback_data="cancel")]])
        )
    except Exception as exc:
        logging.warning(f"Status bar error: {exc}")

# ----------------------------
# STATUS BAR UPLOAD
# ----------------------------
async def upload_bar(file_name, speed, percentage, eta, done, left, engine, status_msg=None):
    target_msg = status_msg or MSG.status_msg
    bar = "█" * int(percentage/10) + "░" * (10-int(percentage/10))
    pct_str = f"<b>{percentage:.1f}%</b>"
    elapsed = (datetime.now() - BotTimes.start_time).seconds

    text = (
        f"📤 <b>Upload</b>\n"
        f"{file_name}\n\n"
        f"[{bar}] {pct_str}\n"
        f"⚡ Vitesse : {speed}\n"
        f"⏳ Temps restant : {eta}\n"
        f"🕰 Écoulé : {elapsed}s\n"
        f"⚙️ Moteur : {engine}\n\n"
        f"✅ Fait : {done}\n"
        f"📦 Total : {left}\n\n"
        f"🖥 CPU : {psutil.cpu_percent()}%\n"
        f"💾 RAM : {sizeUnit(psutil.Process(os.getpid()).memory_info().rss)}\n"
        f"💿 Disque libre : {sizeUnit(psutil.disk_usage('/').free)}"
    )
    try:
        await target_msg.edit_text(
            text=text,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔵 Annuler", callback_data="cancel")]])
        )
    except Exception as exc:
        logging.warning(f"Upload bar error: {exc}")
