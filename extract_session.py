#!/usr/bin/env python3
"""
Extract Browser Session
استخراج الـ session من البراوزر المفتوح

الاستخدام:
1. افتح البراوزر وسجل دخول لـ admin.ftth.iq
2. شغل هذا السكريبت
3. السكريبت هيفتح البراوزر ويحفظ الـ session

python extract_session.py
"""

import asyncio
import json
from pathlib import Path

SESSION_FILE = Path('browser_state.json')

async def main():
    from playwright.async_api import async_playwright
    
    print("=" * 50)
    print("🔐 FTTH Session Extractor")
    print("=" * 50)
    
    print("\n⏳ Opening browser...")
    
    p = await async_playwright().start()
    
    browser = await p.chromium.launch(
        headless=False,  # ⚠️ مهم: نفتح البراوزر عشان المستخدم يشوف ويتعامل مع 2FA
        args=['--no-sandbox'],
        slow_mo=100  # أبطأ شوية عشان نشوف اللي بيحصل
    )
    
    # Try to load existing session if available
    context_args = {
        'viewport': {'width': 1920, 'height': 1080},
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    if SESSION_FILE.exists():
        print(f"📂 Loading existing session from {SESSION_FILE}")
        context_args['storage_state'] = str(SESSION_FILE)
    
    context = await browser.new_context(**context_args)
    page = await context.new_page()
    page.set_default_timeout(120000)  # 2 minutes timeout
    
    print("⏳ Navigating to dashboard...")
    await page.goto('https://admin.ftth.iq/dashboard', timeout=120000)
    await asyncio.sleep(3)
    
    # Check current URL to determine state
    current_url = page.url
    print(f"📍 Current URL: {current_url}")
    
    if 'dashboard' in current_url and 'sso.ftth.iq' not in current_url:
        # Already logged in! Just need to verify token
        print("✅ Already logged in! Checking token...")
        access_token = await page.evaluate("localStorage.getItem('access_token')")
        
        if access_token:
            print("✅ Token found!")
            await context.storage_state(path=str(SESSION_FILE))
            print(f"💾 Session saved to: {SESSION_FILE}")
            
            # Save tokens separately
            refresh_token = await page.evaluate("localStorage.getItem('refresh_token')")
            with open('session.json', 'w') as f:
                json.dump({
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'saved_at': str(asyncio.get_event_loop().time())
                }, f, indent=2)
            print("💾 Tokens saved to: session.json")
        else:
            print("⚠️ No token - session may be expired")
    else:
        # Need to login
        print("🔐 Login required...")
        
        # Wait a bit for SSO redirect
        await asyncio.sleep(3)
        current_url = page.url
        print(f"📍 Login URL: {current_url}")
        
        # Check if we're on Keycloak SSO page
        username_filled = False
        
        # Angular Material uses formcontrolname attributes
        selectors = [
            'input[formcontrolname="Username"]',  # Angular Material - CORRECT!
            'input[formcontrolname="username"]',  # lowercase fallback
            '#mat-input-0',                       # ID fallback
            'input[name="username"]',             # Standard
        ]
        
        for selector in selectors:
            try:
                await page.wait_for_selector(selector, state="visible", timeout=5000)
                print(f"🔑 Found login field: {selector}")
                await page.fill(selector, 'sla')
                username_filled = True
                break
            except:
                continue
        
        if username_filled:
            # Try to fill password - Angular Material uses formcontrolname
            password_selectors = [
                'input[formcontrolname="Password"]',  # Angular Material - CORRECT!
                'input[formcontrolname="password"]',  # lowercase fallback
                '#mat-input-1',                       # ID fallback
                'input[type="password"]',             # Generic
            ]
            for selector in password_selectors:
                try:
                    await page.fill(selector, 'Sla951951sla')
                    print("🔑 Password filled")
                    break
                except:
                    continue
            
            # Try to submit - Angular Material button
            submit_selectors = [
                'button.mat-raised-button',           # Angular Material - CORRECT!
                'button.btn-xl',                      # By class
                'button:has-text("تسجيل الدخول")',
                'button[type="submit"]',
            ]
            for selector in submit_selectors:
                try:
                    await page.click(selector)
                    print("🔑 Form submitted")
                    break
                except:
                    continue
        else:
            print("⚠️ Login form not found")
            print("   📝 Please login manually in the browser window...")
            print("   ⏰ You have 2 minutes...")
        
        # Wait for dashboard
        print("⏳ Waiting for dashboard...")
        try:
            await page.wait_for_url('**/dashboard', timeout=120000)
            print("✅ Login successful!")
        except Exception as e:
            print(f"❌ Login timeout: {e}")
            await page.screenshot(path='login_failed.png')
            await browser.close()
            await p.stop()
            return
        
        # Wait for tokens to be set
        await asyncio.sleep(5)
        
        # Save the session
        await context.storage_state(path=str(SESSION_FILE))
        print(f"💾 Session saved to: {SESSION_FILE}")
        
        # Save tokens separately
        access_token = await page.evaluate("localStorage.getItem('access_token')")
        refresh_token = await page.evaluate("localStorage.getItem('refresh_token')")
        
        if access_token:
            with open('session.json', 'w') as f:
                json.dump({
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'saved_at': str(asyncio.get_event_loop().time())
                }, f, indent=2)
            print("💾 Tokens saved to: session.json")
    
    await browser.close()
    await p.stop()
    
    print("\n" + "=" * 50)
    print("✅ Done! You can now run the monitor:")
    print("   python monitor.py")
    print("=" * 50)

if __name__ == '__main__':
    asyncio.run(main())
