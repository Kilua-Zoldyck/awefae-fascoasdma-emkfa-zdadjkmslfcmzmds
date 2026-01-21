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

def sync_to_github():
    """Pushes the updated settings.json to GitHub so Actions can see it"""
    try:
        # 1. Config User (if not set)
        subprocess.run(["git", "config", "user.name", "Settings Bot"], check=False)
        subprocess.run(["git", "config", "user.email", "bot@wakeel.local"], check=False)
        
        # 2. Add, Commit, Push
        # 2. Add, Commit, Pull, Push
        subprocess.run(["git", "add", "settings.json"], check=True)
        subprocess.run(["git", "commit", "settings.json", "-m", "config: update notification settings via bot"], check=True)
        
        # Pull changes to avoid conflict (Rebase strategy)
        subprocess.run(["git", "pull", "--rebase"], check=True)
        
        subprocess.run(["git", "push"], check=True)
        logger.info("✅ Settings synced to GitHub successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to sync to GitHub: {e}")
        return False

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
    
    # 0. Privileged Users (Owner & Dev)
    admin_id = os.getenv('ADMIN_CHAT_ID')
    dev_id = os.getenv('DEV_CHAT_ID')
    
    # Normalize IDs
    u_id = str(user.id).strip()
    allow_list = [str(x).strip() for x in [admin_id, dev_id] if x]
    
    print(f"DEBUG: User={u_id}, Allowed={allow_list}")
    
    # 1. Pass if User is Privileged
    if u_id in allow_list:
        pass
    
    # 2. If Group/Supergroup, allow Admin
    elif chat.type in ['group', 'supergroup']:
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            if member.status not in ['creator', 'administrator']:
                await query.answer(f"⛔ عذراً، هذا الزر للمسؤولين فقط! (ID: {user.id})", show_alert=True)
                return
        except:
             await query.answer(f"⚠️ لا يمكن التحقق من الصلاحيات (ID: {user.id})", show_alert=True)
             return
             
    # 3. Block unauthorized private chats
    else:
        await query.answer(f"⛔ عذراً، هذا البوت خاص! (ID: {user.id})", show_alert=True)
        return

    # Proceed
    data = query.data
    
    if data == "ignore":
        await query.answer("هذا مجرد عنوان 🏷️")
        return
        
    # 1. ACK immediately with Toast (Stops hanging)
    await query.answer("✅ جاري التطبيق والمزامنة...", show_alert=False)
    
    settings = load_settings()

    if data == "refresh":
        pass 
    elif data.startswith("toggle:"):
        key = data.split(":")[1]
        if key in SETTINGS_MAP:
            settings[key] = not settings.get(key, True)
            # Save locally
            SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    
    # 2. Show "Syncing" State on Message
    try:
        await query.edit_message_text(
            text="⏳ **جاري الاتصال بالسيرفر...**\nرجاء الانتظار لحظات لتأكيد المزامنة.",
            reply_markup=build_keyboard(settings),
            parse_mode='Markdown'
        )
    except:
        pass

    # 3. Perform Sync (Blocking but acknowledged)
    # Only sync if we toggled something to check
    synced = False
    if data.startswith("toggle:"):
         synced = sync_to_github()
    else:
         # Refresh assumes synced if file is consistent? No, just checking status.
         # For simplicity, if refresh, we don't sync unless dirty.
         # But user might want to refresh UI from file.
         synced = True 

    # 4. Final Status Update
    from datetime import datetime
    time_str = datetime.now().strftime("%I:%M %p")
    
    status_msg = "✅ **تمت المزامنة بنجاح**" if synced else "⚠️ **فشل الرفع (محفوظ محلياً)**"
    if data == "refresh":
        status_msg = "✅ **تم تحديث الحالة**"

    final_text = (
        "👋 **لوحة التحكم**\n"
        f"آخر تحديث: {time_str}\n"
        f"الحالة: {status_msg}\n\n"
        "إليك الإعدادات الحالية:"
    )
    
    try:
        await query.edit_message_text(
            text=final_text,
            reply_markup=build_keyboard(settings),
            parse_mode='Markdown'
        )
    except:
        pass

if __name__ == '__main__':
    if not TOKEN:
        print("❌ Error: TELEGRAM_TOKEN not found")
        exit(1)
        
    if not os.getenv('ADMIN_CHAT_ID'):
        print("⚠️ Warning: ADMIN_CHAT_ID not set. Bot owner recognition might trigger false negatives.")
        
    if not os.getenv('DEV_CHAT_ID'):
        print("⚠️ Warning: DEV_CHAT_ID not set.")
        
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("settings", start_settings))
    application.add_handler(CallbackQueryHandler(button_click))
    
    print("✅ Settings Bot (Inline/Pinned) is running...")
    application.run_polling()
