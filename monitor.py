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
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set

from dotenv import load_dotenv
load_dotenv()

# Config
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.getenv('ADMIN_CHAT_ID', '')
SESSION_FILE = Path('browser_state.json')
KNOWN_TICKETS_FILE = Path('known_tickets.json')
DASHBOARD_URL = 'https://admin.ftth.iq/dashboard'
API_URL = 'https://admin.ftth.iq/api/support/tickets'

# 🔐 Auto-Login Credentials (from GitHub Secrets)
FTTH_USERNAME = os.getenv('FTTH_USERNAME', '')
FTTH_PASSWORD = os.getenv('FTTH_PASSWORD', '')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def auto_login(page) -> bool:
    """
    تسجيل دخول تلقائي بالـ Username/Password
    يُستخدم عندما تنتهي الـ session
    """
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
            
            return True
        except:
            logger.error("❌ Login failed - check credentials")
            return False
            
    except Exception as e:
        logger.error(f"❌ Auto-login error: {e}")
        return False


async def browser_refresh_token(page) -> bool:
    """
    تجديد الـ Access Token عن طريق البراوزر
    لو الـ session انتهت، يسجل دخول تلقائي
    """
    try:
        logger.info("🔄 Attempting browser-based token refresh...")
        
        # Navigate to dashboard - this triggers the site's built-in token refresh
        await page.goto('https://admin.ftth.iq/dashboard', wait_until='networkidle', timeout=60000)
        
        # ⚠️ Check if redirected to SSO login (means refresh token expired)
        current_url = page.url
        if 'sso.ftth.iq' in current_url or 'auth/login' in current_url:
            logger.warning("⚠️ Session expired - attempting auto-login...")
            
            # 🔐 Try auto-login with credentials
            if await auto_login(page):
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
            'count': len(self.known)
        }, indent=2))
        logger.info(f"💾 Saved {len(self.known)} tickets")
    
    def is_new(self, tid: str) -> bool:
        return tid not in self.known
    
    def add(self, tid: str):
        self.known.add(tid)


class Telegram:
    def __init__(self):
        self.token = TELEGRAM_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.enabled = bool(self.token and self.chat_id)
    
    async def send(self, text: str) -> bool:
        if not self.enabled:
            return True
        import aiohttp
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={'chat_id': self.chat_id, 'text': text, 'parse_mode': 'HTML', 'disable_web_page_preview': True}
                ) as r:
                    return r.status == 200
        except:
            return False
    
    def format(self, t: Dict) -> str:
        def e(x): return str(x).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;') if x else ''
        st = t.get('status', 'N/A')
        em = {'Open':'🔴','In Progress':'🟡','In progress':'🟡','Resolved':'🟢','Closed':'⚫'}.get(st,'⚪')
        return f"""<b>🔔 تنبيه SLA جديد</b>
━━━━━━━━━━━━━━━━━

🎫 <b>رقم التذكرة:</b> {t.get('displayId', 'N/A')}
🕐 <b>التاريخ:</b> {t.get('createdAt', '')[:19].replace('T', ' ')}

🆔 <b>معرف الوكيل:</b> {t.get('partner', {}).get('id', '')}
👤 <b>اسم الوكيل:</b> {e(t.get('partner', {}).get('displayValue', ''))}

👥 <b>المشترك:</b> {e(t.get('customer', {}).get('displayValue', ''))}
📋 <b>نوع الطلب:</b> {e(t.get('self', {}).get('displayValue', ''))}
📝 <b>الوصف:</b> {e(t.get('summary', ''))[:300]}
📍 <b>المنطقة:</b> {t.get('zone', {}).get('displayValue', '')}
{em} <b>الحالة:</b> {st}

🔗 <a href="https://admin.ftth.iq/tickets/details/{t.get('self', {}).get('id', '')}">فتح التذكرة</a>
━━━━━━━━━━━━━━━━━"""


class Monitor:
    def __init__(self):
        self.state = TicketState()
        self.telegram = Telegram()
        self.browser = None
        self.ctx = None
        self.page = None
    
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
        else:
            logger.warning("⚠️ No session file - will need to auto-login")
        
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
            if await browser_refresh_token(self.page):
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
            
            # If refresh failed, send telegram notification
            await self.telegram.send("""⚠️ <b>تنبيه: الجلسة انتهت!</b>
━━━━━━━━━━━━━━━━━
❌ النظام لم يستطع تجديد التوكن تلقائياً.
الـ Refresh Token ربما انتهى (8 أيام).

🛠️ <b>الحل المطلوب:</b>
1. استخرج Session جديد باستخدام <code>extract_session.py</code>
2. ارفع الملف <code>browser_state.json</code> إلى GitHub يدويًا.
━━━━━━━━━━━━━━━━━""")
            return None
        
        return result
    
    async def run(self):
        logger.info("=" * 50)
        logger.info("🚀 FTTH Monitor")
        logger.info("=" * 50)
        
        startup_delay()
        
        if not await self.setup():
            return False
        
        try:
            result = await self.fetch()
            if not result:
                return False
            
            items = result.get('items', [])
            
            # First run: mark all as known
            if len(self.state.known) == 0:
                logger.info("🎯 First run - saving existing tickets")
                for t in items:
                    if t.get('displayId'):
                        self.state.add(t['displayId'])
                self.state.save()
                await self.telegram.send(f"""🚀 <b>FTTH Monitor Started</b>
━━━━━━━━━━━━━━━━━

✅ النظام يعمل
📊 التذاكر: {result.get('totalCount', 0)}
📋 تم تسجيل: {len(self.state.known)}
━━━━━━━━━━━━━━━━━""")
                return True
            
            # Find new tickets
            new = [t for t in items if t.get('displayId') and self.state.is_new(t['displayId'])]
            
            if new:
                logger.info(f"🆕 {len(new)} NEW tickets!")
                for t in new:
                    self.state.add(t['displayId'])
                    await self.telegram.send(self.telegram.format(t))
                    await asyncio.sleep(random.uniform(1, 3))
            else:
                logger.info("✅ No new tickets")
            
            self.state.save()
            return True
            
        finally:
            if self.browser:
                await self.browser.close()
            if hasattr(self, 'pw'):
                await self.pw.stop()


if __name__ == '__main__':
    success = asyncio.run(Monitor().run())
    sys.exit(0 if success else 1)
