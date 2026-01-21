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

# Load Environment
load_dotenv()
TOKEN = os.getenv('TELEGRAM_TOKEN')
SETTINGS_FILE = Path('settings.json')

# Valid Keys mapping to readable labels
SETTINGS_MAP = {
    "notify_tickets": "🎫 تذاكر جديدة",
    "notify_expired": "🔴 اشتراكات منتهية",
    "notify_renewed": "🟢 تجديد اشتراكات",
    "notify_new_sub": "🆕 مشتركين جدد",
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

def is_admin(update: Update) -> bool:
    """Simple check: Only allow changes from Admin Chat or specific trusted users if needed.
    For groups, Telegram handles admin rights, but we can double check status if we want stricter control.
    For now, we assume if you are in the trusted Group, you can edit."""
    # Practical approach: Anyone in the group can click.
    # If customer wants stricter control, we can fetch chat_member status.
    return True 

def build_keyboard(settings):
    keyboard = []
    row = []
    
    for key, label in SETTINGS_MAP.items():
        is_on = settings.get(key, True)
        status = "✅" if is_on else "❌"
        # Callback data format: "toggle:notify_tickets"
        btn = InlineKeyboardButton(f"{status} {label}", callback_data=f"toggle:{key}")
        row.append(btn)
        
        if len(row) == 1: # 2 buttons per row? No, maybe 1 per row looks better or 2
             keyboard.append(row)
             row = []
    
    if row:
        keyboard.append(row)
        
    # Refresh button
    keyboard.append([InlineKeyboardButton("🔄 تحديث القائمة", callback_data="refresh")])
    return InlineKeyboardMarkup(keyboard)

# -----------------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------------
async def start_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the settings menu"""
    settings = load_settings()
    await update.message.reply_text(
        "⚙️ **لوحة تحكم الإشعارات**\n\nاضغط على الزر لتفعيل/تعطيل الإشعار:",
        reply_markup=build_keyboard(settings),
        parse_mode='Markdown'
    )

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles button clicks"""
    query = update.callback_query
    await query.answer() # Acknowledge click to stop spinner
    
    data = query.data
    settings = load_settings()
    
    if data == "refresh":
        pass # Just re-render
    
    elif data.startswith("toggle:"):
        key = data.split(":")[1]
        if key in SETTINGS_MAP:
            # Toggle boolean
            settings[key] = not settings.get(key, True)
            save_settings(settings)
    
    # Update message with new keyboard
    try:
        await query.edit_message_text(
            text=f"⚙️ **لوحة تحكم الإشعارات**\n\nآخر تحديث: {settings}", 
            # We don't show raw json usually, but let's keep it clean
            # text="⚙️ **لوحة تحكم الإشعارات**\n\nاضغط على الزر لتفعيل/تعطيل الإشعار:",
            reply_markup=build_keyboard(settings),
            parse_mode='Markdown'
        )
    except Exception as e:
        # Ignore "Message is not modified" error
        pass

async def check_admin_rights(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Optional: Check if user is admin before allowing toggle"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    # In a private chat, user is always admin of themselves
    if update.effective_chat.type == 'private':
        return True
        
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in ['creator', 'administrator']:
            return True
    except:
        pass
    
    await update.callback_query.answer("⚠️ فقط المشرفين يمكنهم تغيير الإعدادات!", show_alert=True)
    return False

# We wrap the safe handler to include admin check if desired
async def safe_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only check rights for 'toggle' actions, allow 'refresh' for all? Or restrict all.
    # Restricting all is safer.
    if not await check_admin_rights(update, context):
        return
        
    await button_click(update, context)


if __name__ == '__main__':
    if not TOKEN:
        print("❌ Error: TELEGRAM_TOKEN not found")
        exit(1)
        
    application = ApplicationBuilder().token(TOKEN).build()
    
    application.add_handler(CommandHandler("settings", start_settings))
    application.add_handler(CallbackQueryHandler(safe_button_click))
    
    print("✅ Settings Bot is running...")
    application.run_polling()
