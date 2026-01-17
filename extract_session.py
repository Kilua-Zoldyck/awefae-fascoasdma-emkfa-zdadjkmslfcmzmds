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
    print("   Please login to admin.ftth.iq")
    print("   The browser will close automatically after you login\n")
    
    p = await async_playwright().start()
    
    browser = await p.chromium.launch(
        headless=False,  # Show browser for manual login
        args=['--start-maximized']
    )
    
    context = await browser.new_context(
        viewport={'width': 1400, 'height': 900},
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
    )
    
    page = await context.new_page()
    
    await page.goto('https://admin.ftth.iq/')
    
    print("⏳ Waiting for you to login...")
    print("   (The script will detect when you reach the dashboard)")
    
    # Wait for dashboard URL
    try:
        await page.wait_for_url('**/dashboard**', timeout=300000)  # 5 minutes
    except:
        print("❌ Timeout waiting for login")
        await browser.close()
        await p.stop()
        return
    
    print("\n✅ Login detected!")
    
    # Wait a bit for everything to load
    await asyncio.sleep(3)
    
    # Save the session
    await context.storage_state(path=str(SESSION_FILE))
    
    print(f"💾 Session saved to: {SESSION_FILE}")
    
    # Also save the access token separately
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
    print("   python monitor_production.py")
    print("=" * 50)

if __name__ == '__main__':
    asyncio.run(main())
