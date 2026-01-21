#!/usr/bin/env python3
import os
import json
import logging
from pathlib import Path
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler

# Configure Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

import subprocess

# Load Environment
load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')
SETTINGS_FILE = Path('settings.json')

# Valid Keys mapping to readable labels
SETTINGS_MAP = {
    "notify_tickets": "تذاكر جديدة",
    "notify_expired": "اشتراكات منتهية",
    "notify_renewed": "تجديد اشتراكات",
    "notify_new_sub": "مشتركين جدد",
}

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def load_settings():
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text())
        except:
            pass
    return {k: True for k in SETTINGS_MAP.keys()}

def save_settings(data):
    SETTINGS_FILE.write_text(json.dumps(data, indent=2))
    # Sync to GitHub immediately
    sync_to_github()

def sync_to_github():
    """Pushes the updated settings.json to GitHub so Actions can see it"""
    try:
        # 1. Config User (if not set)
        subprocess.run(["git", "config", "user.name", "Settings Bot"], check=False)
        subprocess.run(["git", "config", "user.email", "bot@wakeel.local"], check=False)
        
        # 2. Add, Commit, Push
        subprocess.run(["git", "add", "settings.json"], check=True)
        subprocess.run(["git", "commit", "settings.json", "-m", "config: update notification settings via bot"], check=True)
        subprocess.run(["git", "push"], check=True)
        logger.info("✅ Settings synced to GitHub successfully")
    except Exception as e:
        logger.error(f"❌ Failed to sync to GitHub: {e}")

def build_keyboard(settings):
    keyboard = []
    
    # Header Button (Info only)
    keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم", callback_data="ignore")])
    
    for key, label in SETTINGS_MAP.items():
        is_on = settings.get(key, True)
        
        # UI Tweak: Use Clear Icons
        status_icon = "✅" if is_on else "⛔"
        text = f"{status_icon} {label}"
        
        # Callback data format: "toggle:notify_tickets"
        btn = InlineKeyboardButton(text, callback_data=f"toggle:{key}")
        keyboard.append([btn]) # Stacked vertically looks better for "Control Panel" feel
        
    # Refresh button
    keyboard.append([InlineKeyboardButton("🔄 تحديث الحالة", callback_data="refresh")])
    return InlineKeyboardMarkup(keyboard)

# -----------------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------------
async def start_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the settings menu (PINNED DASHBOARD)"""
    settings = load_settings()
    
    # 1. Send the Dashboard
    message = await update.message.reply_text(
        "👋 **مرحباً بك في لوحة التحكم**\n"
        "إليك الإعدادات الحالية (اضغط للتغيير):",
        reply_markup=build_keyboard(settings),
        parse_mode='Markdown'
    )
    
    # 2. Pin it (Make it permanent)
    try:
        await context.bot.pin_chat_message(
            chat_id=update.effective_chat.id,
            message_id=message.message_id
        )
    except:
        # Ignore if bot doesn't have Pin rights
        pass

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button clicks"""
    query = update.callback_query
    
    # --- SECURITY CHECK ---
    user = query.from_user
    chat = query.message.chat
    
    # 0. Always Allow Owner (You)
    admin_id = os.getenv('ADMIN_CHAT_ID')
    if str(user.id) == str(admin_id):
        # Allow immediately
        pass
    
    # 1. Else, check Group Admin status
    elif chat.type in ['group', 'supergroup']:
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            if member.status not in ['creator', 'administrator']:
                await query.answer("⛔ عذراً، هذا الزر للمسؤولين فقط!", show_alert=True)
                return
        except:
            # If check fails, default to blocking to be safe
            await query.answer("⚠️ لا يمكن التحقق من الصلاحيات", show_alert=True)
            return

    # Proceed
    data = query.data
    
    if data == "ignore":
        await query.answer("هذا مجرد عنوان 🏷️")
        return
        
    await query.answer() # Ack
    
    settings = load_settings()
    
    if data == "refresh":
        pass 
    elif data.startswith("toggle:"):
        key = data.split(":")[1]
        if key in SETTINGS_MAP:
            settings[key] = not settings.get(key, True)
            save_settings(settings)
    
    # Update message in place
    try:
        await query.edit_message_text(
            text="👋 **مرحباً بك في لوحة التحكم**\n"
                 "إليك الإعدادات الحالية (اضغط للتغيير):",
            reply_markup=build_keyboard(settings),
            parse_mode='Markdown'
        )
    except:
        pass

if __name__ == '__main__':
    if not TOKEN:
        print("❌ Error: TELEGRAM_TOKEN not found")
        exit(1)
        
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("settings", start_settings))
    application.add_handler(CallbackQueryHandler(button_click))
    
    print("✅ Settings Bot (Inline/Pinned) is running...")
    application.run_polling()
