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
        subprocess.run(["git", "add", "settings.json"], check=True)
        # Allow empty commits if no changes
        subprocess.run(["git", "commit", "settings.json", "-m", "config: update notification settings via bot"], check=False)
        
        # Pull changes to avoid conflict (Rebase strategy)
        subprocess.run(["git", "pull", "--rebase"], check=True)
        
        subprocess.run(["git", "push"], check=True)
        logger.info("✅ Settings synced to GitHub successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to sync to GitHub: {e}")
        return False

def build_keyboard(settings, loading_key=None):
    keyboard = []
    
    # Header Button (Info only)
    keyboard.append([InlineKeyboardButton("⚙️ لوحة التحكم", callback_data="ignore")])
    
    for key, label in SETTINGS_MAP.items():
        if key == loading_key:
            # Loading State
            text = f"⏳ {label}..."
        else:
            # Normal State
            is_on = settings.get(key, True)
            status_icon = "✅" if is_on else "⛔"
            text = f"{status_icon} {label}"
        
        # Callback data format: "toggle:notify_tickets"
        btn = InlineKeyboardButton(text, callback_data=f"toggle:{key}")
        keyboard.append([btn]) # Stacked vertically looks better for "Control Panel" feel
        
    # Sync Actions
    refresh_text = "⏳ جاري التحديث..." if loading_key == "refresh" else "🔄 تحديث الواجهة"
    keyboard.append([InlineKeyboardButton(refresh_text, callback_data="refresh")])
    
    sync_text = "⏳ جاري المزامنة..." if loading_key == "forced_sync" else "♻️ تحديث شامل للإعدادات"
    keyboard.append([InlineKeyboardButton(sync_text, callback_data="forced_sync")])
    
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
        
    # 1. ACK immediately
    await query.answer("✅ جاري التنفيذ...", show_alert=False)
    
    settings = load_settings()
    target_key = None
    action_type = "toggle"

    if data == "refresh":
        target_key = "refresh"
        action_type = "refresh"
    elif data == "forced_sync":
        target_key = "forced_sync"
        action_type = "sync"
    elif data.startswith("toggle:"):
        target_key = data.split(":")[1]
        if target_key in SETTINGS_MAP:
            # Toggle value immediately for local feedback
            settings[target_key] = not settings.get(target_key, True)
            # Save locally
            SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    
    # 2. Show "Loading" State on Button
    try:
        await query.edit_message_text(
            text="⏳ **جاري الاتصال بالسيرفر...**",
            reply_markup=build_keyboard(settings, loading_key=target_key), 
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.warning(f"UI loading update warning: {e}")

    # 3. Perform Logic (Blocking)
    synced = False
    
    if action_type == "sync":
        # Force Pull
        try:
            # Stash changes if any to avoid conflict
            subprocess.run(["git", "stash"], check=False) 
            subprocess.run(["git", "pull", "--rebase"], check=True)
            # Reload fresh settings from disk
            settings = load_settings()
            synced = True
        except Exception as e:
            logger.error(f"Git pull failed: {e}")
            synced = False
            
    elif action_type == "toggle":
         synced = sync_to_github()
         settings = load_settings()
         
    else: # refresh
         settings = load_settings()
         synced = True 

    # 4. Final Status Update
    from datetime import datetime, timedelta
    
    # Adjust for Iraq Time (UTC+3)
    iraq_time = datetime.utcnow() + timedelta(hours=3)
    # Format: YYYY-MM-DD | HH:MM AM/PM
    time_str = iraq_time.strftime("%Y-%m-%d | %I:%M %p")
    
    if action_type == "sync":
        status_msg = "📥 **تم جلب أحدث إعدادات**" if synced else "❌ **فشل الاتصال**"
    elif action_type == "refresh":
        status_msg = "🔄 **تم تحديث الواجهة**"
    else:
        status_msg = "✅ **تم الحفظ والمزامنة**" if synced else "⚠️ **محفوظ محلياً فقط**"

    final_text = (
        "👋 **لوحة التحكم**\n"
        f"📅 الوقت: {time_str}\n"
        f"📊 الحالة: {status_msg}\n\n"
        "إليك الإعدادات الحالية:"
    )
    
    import asyncio
    for attempt in range(2):
        try:
            await query.edit_message_text(
                text=final_text,
                reply_markup=build_keyboard(settings),
                parse_mode='Markdown'
            )
            break
        except Exception as e:
            # Check for "Message is not modified" error (harmless)
            if "Message is not modified" in str(e):
                logger.info("⚠️ UI already up to date (MessageNotModified)")
                break
            if attempt == 0: await asyncio.sleep(1)

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
