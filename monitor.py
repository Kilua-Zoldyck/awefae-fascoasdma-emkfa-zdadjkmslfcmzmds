#!/usr/bin/env python3
"""
FTTH Ticket Monitor - Simple & Safe
نظام مراقبة التذاكر الجديدة

المهمة: إرسال إشعار Telegram لكل تذكرة جديدة
الأمان: session محفوظة + تأخيرات عشوائية
"""

import os
import sys
import json
import time
import random
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set

# Maximum age for a ticket to be considered "new" (in hours)
# Prevents spam if known_tickets.json is reset
# Increased to 24h to handle GitHub Actions delays
MAX_TICKET_AGE_HOURS = 24

from dotenv import load_dotenv
load_dotenv()

# Config
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('ADMIN_CHAT_ID', '')      # العميل - إشعارات التذاكر والاشتراكات
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID', '')        # جروب الموظفين - نفس إشعارات العميل
DEV_CHAT_ID = os.getenv('DEV_CHAT_ID', '')              # المطور - إشعارات النظام والأخطاء
SESSION_FILE = Path('browser_state.json')
KNOWN_TICKETS_FILE = Path('known_tickets.json')
KNOWN_SUBSCRIPTIONS_FILE = Path('known_subscriptions.json')
DASHBOARD_URL = 'https://admin.ftth.iq/dashboard'
API_URL = 'https://admin.ftth.iq/api/support/tickets'
SUBSCRIPTIONS_API_URL = 'https://admin.ftth.iq/api/subscriptions'

# 📱 WhatsApp Business API Config
WHATSAPP_PHONE_ID = os.getenv('WHATSAPP_PHONE_ID', '')  # Phone Number ID from Meta
WHATSAPP_TOKEN = os.getenv('WHATSAPP_TOKEN', '')        # Permanent Access Token
WHATSAPP_RECIPIENT = os.getenv('WHATSAPP_RECIPIENT', '')  # Recipient phone (e.g., 96477666774444)

# 🔐 Auto-Login Credentials (from GitHub Secrets)
FTTH_USERNAME = os.getenv('FTTH_USERNAME', '')
FTTH_PASSWORD = os.getenv('FTTH_PASSWORD', '')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def auto_login(page, report_callback=None) -> bool:
    """
    تسجيل دخول تلقائي بالـ Username/Password
    يُستخدم عندما تنتهي الـ session
    """
    if report_callback: report_callback("🔐 Auto-login starting...")
    if not FTTH_USERNAME or not FTTH_PASSWORD:
        logger.error("❌ No credentials found! Set FTTH_USERNAME and FTTH_PASSWORD")
        return False
    
    try:
        logger.info("🔐 Auto-login starting...")
        
        # Navigate to login page
        await page.goto('https://admin.ftth.iq/auth/login', wait_until='networkidle', timeout=60000)
        await asyncio.sleep(3)
        
        # Check if already on dashboard (session still valid)
        if 'dashboard' in page.url:
            logger.info("✅ Already logged in!")
            return True
        
        # Angular Material selectors
        username_selectors = [
            'input[formcontrolname="Username"]',
            'input[formcontrolname="username"]',
            '#mat-input-0',
        ]
        
        # Fill username
        username_filled = False
        for selector in username_selectors:
            try:
                await page.wait_for_selector(selector, state="visible", timeout=5000)
                await page.fill(selector, FTTH_USERNAME)
                logger.info(f"🔑 Username filled: {selector}")
                username_filled = True
                break
            except:
                continue
        
        if not username_filled:
            logger.error("❌ Could not find username field")
            return False
        
        # Fill password
        password_selectors = [
            'input[formcontrolname="Password"]',
            'input[formcontrolname="password"]',
            '#mat-input-1',
            'input[type="password"]',
        ]
        for selector in password_selectors:
            try:
                await page.fill(selector, FTTH_PASSWORD)
                logger.info("🔑 Password filled")
                break
            except:
                continue
        
        # Submit
        submit_selectors = [
            'button.mat-raised-button',
            'button.btn-xl',
            'button:has-text("تسجيل الدخول")',
        ]
        for selector in submit_selectors:
            try:
                await page.click(selector)
                logger.info("🔑 Form submitted")
                break
            except:
                continue
        
        # Wait for dashboard
        try:
            await page.wait_for_url('**/dashboard', timeout=60000)
            logger.info("✅ Auto-login successful!")
            
            # Save new session
            await page.context.storage_state(path=str(SESSION_FILE))
            logger.info("💾 New session saved")
            if report_callback: report_callback("✅ Auto-login successful!")
            
            return True
        except:
            logger.error("❌ Login failed - check credentials")
            return False
            
    except Exception as e:
        logger.error(f"❌ Auto-login error: {e}")
        return False


async def browser_refresh_token(page, report_callback=None) -> bool:
    """
    تجديد الـ Access Token عن طريق البراوزر
    لو الـ session انتهت، يسجل دخول تلقائي
    """
    try:
        logger.info("🔄 Attempting browser-based token refresh...")
        if report_callback: report_callback("🔄 Refreshing token...")
        
        # Navigate to dashboard - this triggers the site's built-in token refresh
        await page.goto('https://admin.ftth.iq/dashboard', wait_until='networkidle', timeout=60000)
        
        # ⚠️ Check if redirected to SSO login (means refresh token expired)
        current_url = page.url
        if 'sso.ftth.iq' in current_url or 'auth/login' in current_url:
            logger.warning("⚠️ Session expired - attempting auto-login...")
            
            # 🔐 Try auto-login with credentials
            if await auto_login(page, report_callback):
                return True
            else:
                logger.error("❌ Auto-login failed!")
                return False
        
        # Wait for the site's JavaScript to potentially refresh the token
        await asyncio.sleep(5)
        
        # Check if we got a new token
        new_token = await page.evaluate("localStorage.getItem('access_token')")
        
        if new_token:
            logger.info("✅ Browser token refresh successful!")
            if report_callback: report_callback("✅ Token refresh success")
            return True
        else:
            logger.error("❌ No token after browser refresh")
            return False
            
    except Exception as e:
        logger.error(f"❌ Browser refresh error: {e}")
        return False


def random_delay(min_s: float, max_s: float):
    d = random.uniform(min_s, max_s)
    logger.info(f"⏳ Waiting {d:.0f}s...")
    time.sleep(d)


def startup_delay():
    """تأخير عشوائي 30 ثانية - 6 دقائق (GitHub Actions فقط)"""
    if os.getenv('GITHUB_ACTIONS'):
        d = random.uniform(30, 360)  # 30 ثانية - 6 دقائق
        logger.info(f"⏳ Startup delay: {d:.0f}s")
        time.sleep(d)


class TicketState:
    def __init__(self):
        self.known: Set[str] = set()
        if KNOWN_TICKETS_FILE.exists():
            try:
                self.known = set(json.loads(KNOWN_TICKETS_FILE.read_text()).get('tickets', []))
                logger.info(f"📂 Loaded {len(self.known)} known tickets")
            except:
                pass
    
    def save(self):
        KNOWN_TICKETS_FILE.write_text(json.dumps({
            'tickets': list(self.known),
            'updated': datetime.now().isoformat(),
            'last_run': datetime.now().timestamp(),
            'count': len(self.known)
        }, indent=2))
        logger.info(f"💾 Saved {len(self.known)} tickets")
    
    def get_last_run(self) -> float:
        try:
            data = json.loads(KNOWN_TICKETS_FILE.read_text())
            return data.get('last_run', 0)
        except:
            return 0
    
    def is_new(self, tid: str) -> bool:
        return tid not in self.known
    
    def add(self, tid: str):
        self.known.add(tid)


class SubscriptionState:
    """
    تتبع حالة الاشتراكات لاكتشاف التغييرات
    Tracks subscription statuses to detect changes (Active ↔ Expired)
    """
    def __init__(self):
        # Dict of subscription_id -> status
        self.subscriptions: Dict[str, str] = {}
        if KNOWN_SUBSCRIPTIONS_FILE.exists():
            try:
                data = json.loads(KNOWN_SUBSCRIPTIONS_FILE.read_text())
                self.subscriptions = data.get('subscriptions', {})
                logger.info(f"📂 Loaded {len(self.subscriptions)} known subscriptions")
            except:
                pass
    
    def save(self):
        KNOWN_SUBSCRIPTIONS_FILE.write_text(json.dumps({
            'subscriptions': self.subscriptions,
            'updated': datetime.now().isoformat(),
            'count': len(self.subscriptions)
        }, indent=2, ensure_ascii=False))
        logger.info(f"💾 Saved {len(self.subscriptions)} subscriptions")
    
    def get_changes(self, current_subscriptions: list) -> tuple:
        """
        مقارنة الحالة الحالية بالمخزنة واكتشاف التغييرات
        Returns: (expired_list, renewed_list, new_list)
        """
        expired = []   # Active → Expired
        renewed = []   # Expired → Active
        new_subs = []  # New subscriptions
        
        for sub in current_subscriptions:
            sub_id = sub.get('self', {}).get('id') or sub.get('id')
            current_status = sub.get('status', '').lower()
            
            if not sub_id:
                continue
            
            # Normalize status
            if current_status in ['active', 'نشط', 'جاري']:
                current_status = 'active'
            elif current_status in ['expired', 'منتهي', 'منتهية']:
                current_status = 'expired'
            
            old_status = self.subscriptions.get(sub_id)
            
            if old_status is None:
                # New subscription
                new_subs.append(sub)
                self.subscriptions[sub_id] = current_status
            elif old_status != current_status:
                # Status changed!
                if old_status == 'active' and current_status == 'expired':
                    expired.append(sub)
                elif old_status == 'expired' and current_status == 'active':
                    renewed.append(sub)
                self.subscriptions[sub_id] = current_status
        
        return expired, renewed, new_subs


class Telegram:
    def __init__(self):
        self.token = TELEGRAM_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.group_chat_id = GROUP_CHAT_ID
        self.dev_chat_id = DEV_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
        self.dev_enabled = bool(self.token and self.dev_chat_id)
    
    async def send(self, text: str) -> bool:
        """Send notification to CLIENT and GROUP (tickets, subscriptions)"""
        if not self.enabled:
            return True
        
            # Load Settings (Try Remote GitHub First for Real-Time Control)
        settings = {}
        try:
            # 1. Try Cloud Fetch (Instant)
            gh_token = os.getenv('GITHUB_TOKEN')
            # Use raw.githubusercontent.com for speed. Private repos need token header? No, raw needs token in header.
            # actually API is more reliable for private repos with token.
            # Repo: Kilua-Zoldyck/awefae-fascoasdma-emkfa-zdadjkmslfcmzmds
            
            if gh_token:
               api_url = "https://raw.githubusercontent.com/Kilua-Zoldyck/awefae-fascoasdma-emkfa-zdadjkmslfcmzmds/main/settings.json"
               headers = {"Authorization": f"token {gh_token}"}
               async with aiohttp.ClientSession() as fetch_session:
                   async with fetch_session.get(api_url, headers=headers, timeout=5) as resp:
                       if resp.status == 200:
                           content = await resp.text()
                           settings = json.loads(content)
                           # logging.info("☁️ Cloud Settings Loaded")
            
            # 2. Fallback to Local if Cloud fails or empty
            if not settings and Path('settings.json').exists():
                settings = json.loads(Path('settings.json').read_text())
                
        except Exception as e:
            # logging.error(f"Settings Load Error: {e}")
            # Final fallback
            if Path('settings.json').exists():
                 try: settings = json.loads(Path('settings.json').read_text())
                 except: pass
            
        # Determine Notification Type based on text content (Simple Heuristic)
        notify_group = False
        if "تنبيه SLA جديد" in text:
            notify_group = settings.get("notify_tickets", True)
        elif "اشتراك منتهي" in text:
            notify_group = settings.get("notify_expired", True)
        elif "تم التجديد" in text:
            notify_group = settings.get("notify_renewed", True)
        elif "مشترك جديد" in text:
            notify_group = settings.get("notify_new_sub", True)
        else:
            # Default for unknown types (or fallback)
            notify_group = True

        import aiohttp
        try:
            async with aiohttp.ClientSession() as s:
                # 1. Send to Client (Admin) - ALWAYS (Per User Request)
                await s.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={'chat_id': self.chat_id, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
                )
                
                # 2. Send to Group (Employees) - ONLY IF ENABLED in settings
                if self.group_chat_id and notify_group:
                     await s.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                        json={'chat_id': self.group_chat_id, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
                    )
                return True
        except:
            return False
    
    async def send_to_dev(self, text: str) -> bool:
        """Send notification to DEVELOPER only (system errors, session expired)"""
        if not self.dev_enabled:
            return True
        import aiohttp
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={'chat_id': self.dev_chat_id, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
                ) as r:
                    return r.status == 200
        except:
            return False
    
    async def send_to_all(self, text: str) -> bool:
        """Send notification to BOTH client AND developer (monitoring alerts)"""
        # Send to client
        await self.send(text)
        # Also send to developer
        await self.send_to_dev(text)
        return True
    
    def format(self, t: Dict) -> str:
        def e(x): return str(x).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') if x else ''
        st = t.get('status', 'N/A')
        em = {'Open':'🔴','In Progress':'🟡','In progress':'🟡','Resolved':'🟢','Closed':'⚫'}.get(st,'⚪')
        ticket_time = datetime.fromisoformat(t.get('createdAt', '').replace('Z', '+00:00'))
        local_time = ticket_time.astimezone(timezone(timedelta(hours=3)))
        formatted_time = local_time.strftime('%Y-%m-%d %H:%M:%S')

        return f"""<b>🔔 تنبيه SLA جديد</b>
━━━━━━━━━━━━━━━━━

🎫 <b>رقم التذكرة:</b> {t.get('displayId', 'N/A')}
🕐 <b>التاريخ:</b> {formatted_time}

🆔 <b>معرف الوكيل:</b> {t.get('partner', {}).get('id', '')}
👤 <b>اسم الوكيل:</b> {e(t.get('partner', {}).get('displayValue', ''))}

👥 <b>المشترك:</b> {e(t.get('customer', {}).get('displayValue', ''))}
📋 <b>نوع الطلب:</b> {e(t.get('self', {}).get('displayValue', ''))}
📝 <b>الوصف:</b> {e(t.get('summary', ''))[:300]}
📍 <b>المنطقة:</b> {t.get('zone', {}).get('displayValue', '')}
{em} <b>الحالة:</b> {st}

🔗 <a href="https://admin.ftth.iq/tickets/details/{t.get('self', {}).get('id', '')}">فتح التذكرة</a>
━━━━━━━━━━━━━━━━━"""
    
    def _extract_common_data(self, sub: Dict) -> Dict:
        """Helper to extract common subscription fields with fallbacks"""
        data = {}
        data['sub_id'] = sub.get('self', {}).get('id') or sub.get('id', 'N/A')
        data['customer'] = sub.get('customer', {}).get('displayValue', '') or sub.get('customerName', 'N/A')
        
        # Service Plan Extraction (from 'services' array or 'bundle')
        services = []
        if 'services' in sub and isinstance(sub['services'], list):
            services = [s.get('displayValue', '') for s in sub['services'] if s.get('displayValue')]
        
        bundle = sub.get('bundle', {}).get('displayValue', '')
        
        if services:
            # Combine Bundle + Main Service (e.g. "FTTH Basic - FIBER 35")
            main_service = services[0] 
            data['service'] = f"{bundle} - {main_service}" if bundle else main_service
        else:
            data['service'] = bundle or sub.get('servicePlan', {}).get('displayValue', 'N/A')
        
        # Expiry Date Extraction (Correct key is 'expires')
        expiry_raw = (
            sub.get('expires') or 
            sub.get('expiryDate') or 
            sub.get('validUntil')
        )
        data['expiry'] = expiry_raw[:10] if expiry_raw else 'N/A'
        
        data['zone'] = sub.get('zone', {}).get('displayValue') or sub.get('zoneName', 'N/A')
        return data

    def format_expired(self, sub: Dict) -> str:
        """Format expired subscription notification"""
        def e(x): return str(x).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') if x else ''
        d = self._extract_common_data(sub)
        
        return f"""<b>🔴 اشتراك منتهي</b>
━━━━━━━━━━━━━━━━━

🆔 <b>رمز الاشتراك:</b> {e(d['sub_id'])}
👤 <b>المشترك:</b> {e(d['customer'])}
📦 <b>الخدمة:</b> {e(d['service'])}
📅 <b>تاريخ الانتهاء:</b> {d['expiry']}
📍 <b>المنطقة:</b> {e(d['zone'])}

⚠️ <b>الحالة:</b> منتهي الصلاحية

🔗 <a href="https://admin.ftth.iq/subscriptions">فتح الاشتراكات</a>
━━━━━━━━━━━━━━━━━"""
    
    def format_renewed(self, sub: Dict) -> str:
        """Format renewed subscription notification"""
        def e(x): return str(x).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') if x else ''
        d = self._extract_common_data(sub)
        
        return f"""<b>🟢 تم التجديد</b>
━━━━━━━━━━━━━━━━━

🆔 <b>رمز الاشتراك:</b> {e(d['sub_id'])}
👤 <b>المشترك:</b> {e(d['customer'])}
📦 <b>الخدمة:</b> {e(d['service'])}
📅 <b>صالح حتى:</b> {d['expiry']}
📍 <b>المنطقة:</b> {e(d['zone'])}

✅ <b>الحالة:</b> تم التجديد بنجاح

🔗 <a href="https://admin.ftth.iq/subscriptions">فتح الاشتراكات</a>
━━━━━━━━━━━━━━━━━"""
    
    def format_new_subscriber(self, sub: Dict) -> str:
        """Format new subscriber notification"""
        def e(x): return str(x).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') if x else ''
        d = self._extract_common_data(sub)
        status = sub.get('status', 'N/A')
        status_emoji = "🟢" if status.lower() in ['active', 'نشط', 'جاري'] else "🔴"
        
        return f"""<b>🆕 مشترك جديد</b>
━━━━━━━━━━━━━━━━━

🆔 <b>رمز الاشتراك:</b> {e(d['sub_id'])}
👤 <b>المشترك:</b> {e(d['customer'])}
📦 <b>الخدمة:</b> {e(d['service'])}
📅 <b>صالح حتى:</b> {d['expiry']}
📍 <b>المنطقة:</b> {e(d['zone'])}
{status_emoji} <b>الحالة:</b> {status}

📢 <b>تمت إضافته للمراقبة</b>

🔗 <a href="https://admin.ftth.iq/subscriptions">فتح الاشتراكات</a>
━━━━━━━━━━━━━━━━━"""


class WhatsApp:
    """WhatsApp Business API integration"""
    def __init__(self):
        self.phone_id = WHATSAPP_PHONE_ID
        self.token = WHATSAPP_TOKEN
        self.recipient = WHATSAPP_RECIPIENT
        self.enabled = bool(self.phone_id and self.token and self.recipient)
        if self.enabled:
            logger.info("📱 WhatsApp notifications enabled")
    
    async def send(self, text: str) -> bool:
        """Send a text message via WhatsApp Business API"""
        if not self.enabled:
            return True
        
        import aiohttp
        try:
            # First, we need to use a template message since we're outside the 24h window
            # Using hello_world template for now - you can create custom template later
            url = f"https://graph.facebook.com/v22.0/{self.phone_id}/messages"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            # Send text message (Requires active 24h conversation window for simple text)
            # User must message the bot first!
            payload = {
                "messaging_product": "whatsapp",
                "to": self.recipient,
                "type": "text",
                "text": {"body": text}
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        logger.info("📱 WhatsApp notification sent!")
                        return True
                    else:
                        error = await response.text()
                        logger.warning(f"⚠️ WhatsApp error: {response.status} - {error}")
                        return False
        except Exception as e:
            logger.warning(f"⚠️ WhatsApp send error: {e}")
            return False
    
    async def send_ticket(self, ticket: Dict) -> bool:
        """Send ticket notification - uses template message"""
        if not self.enabled:
            return True
        
        # Send full text details (User must maintain 24h window)
        text = self.format(ticket)
        return await self.send(text)
    
    def format(self, t: Dict) -> str:
        """Format ticket for WhatsApp (plain text, no HTML)"""
        st = t.get('status', 'N/A')
        em = {'Open':'🔴','In Progress':'🟡','In progress':'🟡','Resolved':'🟢','Closed':'⚫'}.get(st,'⚪')
        return f"""🔔 *تنبيه SLA جديد*
━━━━━━━━━━━━━━━━━

🎫 *رقم التذكرة:* {t.get('displayId', 'N/A')}
🕐 *التاريخ:* {t.get('createdAt', '')[:19].replace('T', ' ')}

🆔 *معرف الوكيل:* {t.get('partner', {}).get('id', '')}
👤 *اسم الوكيل:* {t.get('partner', {}).get('displayValue', '')}

👥 *المشترك:* {t.get('customer', {}).get('displayValue', '')}
📋 *نوع الطلب:* {t.get('self', {}).get('displayValue', '')}
📝 *الوصف:* {t.get('summary', '')[:300]}
📍 *المنطقة:* {t.get('zone', {}).get('displayValue', '')}
{em} *الحالة:* {st}

🔗 https://admin.ftth.iq/tickets/details/{t.get('self', {}).get('id', '')}
━━━━━━━━━━━━━━━━━"""

    async def send_template(self, template_name: str, variable_text: str) -> bool:
        """Send a template message (required for notifications > 24h)"""
        if not self.enabled:
            return True
        import aiohttp
        try:
            url = f"https://graph.facebook.com/v22.0/{self.phone_id}/messages"
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "messaging_product": "whatsapp",
                "to": self.recipient,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": "ar"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {
                                    "type": "text",
                                    "text": variable_text
                                }
                            ]
                        }
                    ]
                }
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, headers=headers, json=payload) as response:
                    if response.status == 200:
                        logger.info(f"📱 WhatsApp Template '{template_name}' sent!")
                        return True
                    else:
                        error = await response.text()
                        logger.warning(f"⚠️ WhatsApp Template error: {response.status} - {error}")
                        return False
        except Exception as e:
            logger.warning(f"⚠️ WhatsApp send error: {e}")
            return False

    def format_simple(self, sub: Dict) -> str:
        """Simple format for batched messages"""
        data = self._extract_common_data(sub) # Helper needs to be available or duplicated
        return f"🆔 {data['sub_id']} | 👤 {data['customer']} | 📦 {data['service']}"

    # Helper to extract common data (duplicated from Telegram class to keep classes independent)
    def _extract_common_data(self, sub: Dict) -> Dict:
        data = {}
        data['sub_id'] = sub.get('self', {}).get('id') or sub.get('id', 'N/A')
        data['customer'] = sub.get('customer', {}).get('displayValue', '') or sub.get('customerName', 'N/A')
        services = []
        if 'services' in sub and isinstance(sub['services'], list):
            services = [s.get('displayValue', '') for s in sub['services'] if s.get('displayValue')]
        bundle = sub.get('bundle', {}).get('displayValue', '')
        if services:
            main_service = services[0] 
            data['service'] = f"{bundle} - {main_service}" if bundle else main_service
        else:
            data['service'] = bundle or sub.get('servicePlan', {}).get('displayValue', 'N/A')
        return data


class Monitor:
    def __init__(self):
        self.state = TicketState()
        self.subscription_state = SubscriptionState()
        self.telegram = Telegram()
        self.whatsapp = WhatsApp()
        self.browser = None
        self.ctx = None
        self.page = None
        self.report_buffer = []
        self.whatsapp_buffer = [] # Buffer for periodic WhatsApp updates

    def log_report(self, msg: str):
        """Add message to execution report"""
        self.report_buffer.append(msg)
    
    async def setup(self):
        from playwright.async_api import async_playwright
        
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(
            headless=True,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        
        vp = random.choice([
            {'width': 1920, 'height': 1080},
            {'width': 1366, 'height': 768},
            {'width': 1536, 'height': 864},
        ])
        
        # Create context with session if exists, otherwise empty
        ctx_args = {
            'viewport': vp,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
            'locale': 'ar-IQ',
            'timezone_id': 'Asia/Baghdad',
        }
        
        if SESSION_FILE.exists():
            ctx_args['storage_state'] = str(SESSION_FILE)
            logger.info("📂 Using existing session")
            self.log_report("📂 Session: Found existing file")
        else:
            logger.warning("⚠️ No session file - will need to auto-login")
            self.log_report("⚠️ Session: No file, new login needed")
        
        self.ctx = await self.browser.new_context(**ctx_args)
        self.page = await self.ctx.new_page()
        await self.page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        
        logger.info(f"✅ Browser ready ({vp['width']}x{vp['height']})")
        return True
    
    async def _fetch_api(self) -> Optional[Dict]:
        """Internal API fetch - does NOT retry"""
        try:
            await self.page.goto(DASHBOARD_URL, wait_until='domcontentloaded', timeout=120000)
            
            if 'sso.ftth.iq' in self.page.url:
                logger.error("❌ Session expired!")
                return None
            
            # Wait for site to auto-refresh token if needed
            await asyncio.sleep(3)
            random_delay(2, 4)
            
            result = await self.page.evaluate(f"""
                (async()=>{{
                    try{{
                        let token = localStorage.getItem('access_token');
                        if (!token) return {{error:'no_token'}};
                        
                        let r = await fetch('{API_URL}?pageSize=30&pageNumber=1&sortCriteria.property=createdAt&sortCriteria.direction=desc',{{
                            headers:{{'Authorization':'Bearer '+token,'Accept':'application/json'}}
                        }});
                        
                        // Retry once if 401
                        if (r.status === 401) {{
                            await new Promise(x=>setTimeout(x,2000));
                            token = localStorage.getItem('access_token');
                            r = await fetch('{API_URL}?pageSize=30&pageNumber=1&sortCriteria.property=createdAt&sortCriteria.direction=desc',{{
                                headers:{{'Authorization':'Bearer '+token,'Accept':'application/json'}}
                            }});
                        }}
                        
                        return r.ok ? await r.json() : {{error:r.status}};
                    }}catch(e){{return {{error:e.message}};}}
                }})()
            """)
            
            if 'error' in result:
                logger.error(f"❌ API: {result['error']}")
                # Token errors are handled by the fetch() wrapper
                return result
            
            # Save updated session (tokens may have refreshed)
            try:
                await self.ctx.storage_state(path=str(SESSION_FILE))
            except:
                pass
            
            logger.info(f"✅ Got {len(result.get('items',[]))} tickets")
            return result
            
        except Exception as e:
            logger.error(f"❌ {e}")
            return None
    
    async def fetch(self) -> Optional[Dict]:
        """Fetch tickets with automatic token refresh on failure"""
        result = await self._fetch_api()
        
        # If token error, try refresh and retry once
        if result and 'error' in result and result['error'] in ['no_token', 401]:
            logger.info("🔄 Attempting automatic token refresh...")
            
            # Use browser-based refresh (not API call - Keycloak blocks datacenter IPs)
            if await browser_refresh_token(self.page, self.log_report):
                # Save the refreshed state
                try:
                    await self.ctx.storage_state(path=str(SESSION_FILE))
                    logger.info("💾 Saved refreshed session")
                except:
                    pass
                
                # Retry API call with fresh token
                result = await self._fetch_api()
                if result and 'error' not in result:
                    logger.info("✅ Retry successful after token refresh!")
                    return result
            
            # If refresh failed, send notification to DEVELOPER only (not client)
            await self.telegram.send_to_dev("""⚠️ <b>تنبيه: الجلسة انتهت!</b>
━━━━━━━━━━━━━━━━━
❌ النظام لم يستطع تجديد التوكن تلقائياً.
الـ Refresh Token ربما انتهى (8 أيام).

🛠️ <b>الحل المطلوب:</b>
1. استخرج Session جديد باستخدام <code>extract_session.py</code>
2. ارفع الملف <code>browser_state.json</code> إلى GitHub يدويًا.
━━━━━━━━━━━━━━━━━""")
            return None
        
        return result
    
    async def _fetch_subscriptions_api(self) -> Optional[Dict]:
        """Fetch ALL subscriptions from API (Pagination Support)"""
        all_items = []
        page = 1
        page_size = 100
        total_count = 0
        
        logger.info("📦 Fetching subscription list...")
        
        while True:
            try:
                # Add random small delay between pages
                if page > 1: await asyncio.sleep(random.uniform(0.5, 1.5))
                
                result = await self.page.evaluate(f"""
                    (async()=>{{
                        try{{
                            let token = localStorage.getItem('access_token');
                            if (!token) return {{error:'no_token'}};
                            
                            let r = await fetch('{SUBSCRIPTIONS_API_URL}?pageSize={page_size}&pageNumber={page}',{{
                                headers:{{'Authorization':'Bearer '+token,'Accept':'application/json'}}
                            }});
                            
                            if (r.status === 401) {{
                                await new Promise(x=>setTimeout(x,2000));
                                token = localStorage.getItem('access_token');
                                r = await fetch('{SUBSCRIPTIONS_API_URL}?pageSize={page_size}&pageNumber={page}',{{
                                    headers:{{'Authorization':'Bearer '+token,'Accept':'application/json'}}
                                }});
                            }}
                            
                            return r.ok ? await r.json() : {{error:r.status}};
                        }}catch(e){{return {{error:e.message}};}}
                    }})()
                """)
                
                if 'error' in result:
                    logger.error(f"❌ Subscriptions Page {page}: {result['error']}")
                    # If first page fails, abort. If subsequent, return what we have? 
                    # Better to return None to trigger retry or avoid partial data.
                    return None
                
                items = result.get('items', [])
                count = len(items)
                total_count = result.get('totalCount', 0)
                
                all_items.extend(items)
                logger.debug(f"📄 Page {page}: Got {count} items (Total so far: {len(all_items)})")
                
                # Check termination
                if count < page_size or len(all_items) >= total_count:
                    break
                    
                page += 1
                
            except Exception as e:
                logger.error(f"❌ Subscriptions fetch error on page {page}: {e}")
                return None
        
        logger.info(f"✅ Fetched ALL subscriptions: {len(all_items)}/{total_count}")
        return {'items': all_items, 'totalCount': total_count}
    
    async def run(self):
        self.report_buffer = []
        self.whatsapp_buffer = []  # Reset batch buffer
        logger.info("=" * 50)
        logger.info("🚀 FTTH Monitor")
        logger.info("=" * 50)
        
        startup_delay()
        
        # 🛡️ Safety: Prevent frequent runs (Dual Scheduler Protection)
        last_run = self.state.get_last_run()
        if last_run > 0:
            elapsed = datetime.now().timestamp() - last_run
            if elapsed < 300:  # Less than 5 minutes
                logger.warning(f"🛑 Skipping run: Last run was {elapsed:.0f}s ago (< 300s)")
                self.log_report("🛑 Skipped: Too frequent (Rate Limit)")
                # Send brief report to dev so they know it worked but skipped
                # await self.telegram.send_to_dev(f"⚠️ <b>Skipped Run</b>\nReason: Recently ran ({elapsed:.0f}s ago)")
                return True
        
        if not await self.setup():
            return False
        
        try:
            result = await self.fetch()
            if not result:
                self.log_report("❌ Fetch Failed")
                return False
            
            items = result.get('items', [])
            self.log_report(f"📊 Tickets: {len(items)} fetched")
            
            # First run: mark all as known
            if len(self.state.known) == 0:
                logger.info("🎯 First run - saving existing tickets")
                for t in items:
                    if t.get('displayId'):
                        self.state.add(t['displayId'])
                self.state.save()
                await self.telegram.send_to_dev(f"""🚀 <b>FTTH Monitor Started</b>
━━━━━━━━━━━━━━━━━

✅ النظام يعمل
📊 التذاكر: {result.get('totalCount', 0)}
📋 تم تسجيل: {len(self.state.known)}
━━━━━━━━━━━━━━━━━""")
                return True
            
            # Find new tickets
            new = [t for t in items if t.get('displayId') and self.state.is_new(t['displayId'])]
            
            if new:
                logger.info(f"🆕 {len(new)} NEW tickets found")
                self.log_report(f"🆕 Found {len(new)} NEW tickets")
                sent_count = 0
                for t in new:
                    self.state.add(t['displayId'])
                    
                    # Time filter: only send notification for recent tickets
                    try:
                        created_at = t.get('createdAt', '')
                        if created_at:
                            ticket_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            now = datetime.now(timezone.utc)
                            age_hours = (now - ticket_time).total_seconds() / 3600
                            
                            if age_hours > MAX_TICKET_AGE_HOURS:
                                logger.info(f"⏭️ Skipping old ticket {t['displayId']} (age: {age_hours:.1f}h)")
                                continue
                    except Exception as e:
                        logger.warning(f"⚠️ Could not parse ticket date: {e}")
                    
                    # 1. Telegram: Instant Notification (UNTOUCHED)
                    await self.telegram.send_to_all(self.telegram.format(t))
                    
                    # 2. WhatsApp: Buffer for batch sending
                    wa_msg = self.whatsapp.format(t)
                    self.whatsapp_buffer.append(wa_msg)
                    
                    sent_count += 1
                    await asyncio.sleep(random.uniform(1, 3))
                
                logger.info(f"📤 Processed {sent_count}/{len(new)} tickets")
            else:
                logger.info("✅ No new tickets")
            
            self.state.save()
            
            # ═══════════════════════════════════════════════════
            # 📦 SUBSCRIPTION MONITORING
            # ═══════════════════════════════════════════════════
            logger.info("=" * 50)
            logger.info("📦 Checking Subscriptions...")
            
            sub_result = await self._fetch_subscriptions_api()
            if sub_result:
                subscriptions = sub_result.get('items', [])
                self.log_report(f"📦 Subscriptions: {len(subscriptions)} fetched")
                
                # First run: save all subscription statuses
                if len(self.subscription_state.subscriptions) == 0:
                    logger.info("🎯 First run - saving subscription states")
                    for sub in subscriptions:
                        sub_id = sub.get('self', {}).get('id') or sub.get('id')
                        status = sub.get('status', '').lower()
                        if status in ['active', 'نشط', 'جاري']:
                            status = 'active'
                        elif status in ['expired', 'منتهي', 'منتهية']:
                            status = 'expired'
                        if sub_id:
                            self.subscription_state.subscriptions[sub_id] = status
                    self.subscription_state.save()
                    logger.info(f"📋 Saved {len(self.subscription_state.subscriptions)} subscriptions")
                else:
                    # Check for changes
                    expired, renewed, new_subs = self.subscription_state.get_changes(subscriptions)
                    
                    # 🔍 DEBUG: Log data structure if we have N/A fields
                    if expired or renewed or new_subs:
                        changes = expired + renewed + new_subs
                        if changes:
                            logger.info(f"🔍 DEBUG DATA FOR FIRST CHANGE: {json.dumps(changes[0], ensure_ascii=False)}")

                    # Send notifications for expired subscriptions
                    for sub in expired:
                        logger.info(f"🔴 Expired: {sub.get('id', 'N/A')}")
                        msg = self.telegram.format_expired(sub)
                        await self.telegram.send_to_all(msg)
                        
                        # Buffer for WhatsApp
                        self.whatsapp_buffer.append(f"🔴 *اشتراك منتهي*\n{self.whatsapp.format_simple(sub)}")
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                    
                    # Send notifications for renewed subscriptions
                    for sub in renewed:
                        logger.info(f"🟢 Renewed: {sub.get('id', 'N/A')}")
                        msg = self.telegram.format_renewed(sub)
                        await self.telegram.send_to_all(msg)
                        
                        # Buffer for WhatsApp
                        self.whatsapp_buffer.append(f"🟢 *تم التجديد*\n{self.whatsapp.format_simple(sub)}")
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                    
                    # Send notifications for new subscribers
                    for sub in new_subs:
                        logger.info(f"🆕 New subscriber: {sub.get('id', 'N/A')}")
                        msg = self.telegram.format_new_subscriber(sub)
                        await self.telegram.send_to_all(msg)
                        
                        # Buffer for WhatsApp
                        self.whatsapp_buffer.append(f"🆕 *مشترك جديد*\n{self.whatsapp.format_simple(sub)}")
                        await asyncio.sleep(random.uniform(0.5, 1.5))


                    
                    # Log summary
                    if expired or renewed or new_subs:
                        self.log_report(f"📊 Changes: {len(expired)} expired, {len(renewed)} renewed, {len(new_subs)} new")
                    else:
                        logger.info("✅ No subscription changes")
                    
                    self.subscription_state.save()

            # Define variables for report safely
            sub_count = len(self.subscription_state.subscriptions)
            new_tickets = len(new) if 'new' in locals() else 0
            expired_count = len(expired) if 'expired' in locals() else 0
            renewed_count = len(renewed) if 'renewed' in locals() else 0
            new_subs_count = len(new_subs) if 'new_subs' in locals() else 0

            # 📊 Detailed Run Log (For Developer ONLY)
            if self.report_buffer:
                log_text = "📊 <b>تقرير التشغيل (Run Log)</b>\n━━━━━━━━━━━━━━━━━\n" + "\n".join(self.report_buffer)
                await self.telegram.send_to_dev(log_text)

            await self.telegram.send_to_dev(f"""📊 <b>FTTH Monitor Run Summary</b>
━━━━━━━━━━━━━━━━━

🎫 <b>التذاكر:</b> {len(self.state.known)} معروفة
🆕 <b>تذاكر جديدة:</b> {new_tickets}

📦 <b>الاشتراكات:</b> {sub_count} مراقَبة
🔴 <b>منتهية:</b> {expired_count}
🟢 <b>تجديد:</b> {renewed_count}
🆕 <b>جدد:</b> {new_subs_count}

✅ <b>الحالة:</b> Run completed successfully
━━━━━━━━━━━━━━━━━""")
            
            return True
            
        finally:
            if self.browser:
                await self.browser.close()
            if hasattr(self, 'pw'):
                await self.pw.stop()


if __name__ == '__main__':
    success = asyncio.run(Monitor().run())
    sys.exit(0 if success else 1)
