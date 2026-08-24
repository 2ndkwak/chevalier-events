"""
capture.py -- log into the locally-running app (see run_server.py) and
screenshot real, rendered admin screens for documentation.

USAGE: after seed_screenshots.py and run_server.py are both running,
python3 capture.py. Screenshots land in OUT below.

This file's specific screenshot list (Dashboard, Members, event edit x2,
RSVPs) was what the Aug 15 2026 GS Guide/Tech Reference refresh needed.
For a future documentation pass, treat the pattern -- not the literal
list -- as the reusable part: log in once, then for each screen you need,
goto() the real admin route, wait for network idle + a short settle
timeout, and screenshot(). Click through any UI state you need visible
first (an expanded panel, a toggle) before the screenshot call, same as
the "Show promo email results" click below. Add new screens by copying
one of the numbered blocks.
"""
import sys, os
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5050"
OUT = os.environ.get("SCREENSHOT_OUT_DIR", os.path.join(os.path.dirname(__file__), "screenshots"))
os.makedirs(OUT, exist_ok=True)

ADMIN_EMAIL = "trey@example.com"     # matches seed_screenshots.py
ADMIN_PASSWORD = "changeme"

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1300, "height": 900})

    # Log in
    page.goto(f"{BASE}/login")
    page.fill('input[name="email"]', ADMIN_EMAIL)
    page.fill('input[name="password"]', ADMIN_PASSWORD)
    page.click('button[type="submit"]')
    page.wait_for_load_state("networkidle")
    print("post-login url:", page.url)

    # 1. Dashboard -- new two-row milestone dots
    page.goto(f"{BASE}/admin/")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/new_dashboard.png", full_page=True)
    print("captured dashboard")

    # 2. Members list -- bounce/open badges
    page.goto(f"{BASE}/admin/members/")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/new_members_list.png", full_page=True)
    print("captured members list")

    # 3. Event edit screen -- "Sent" state with test-copy button (Chablis)
    page.goto(f"{BASE}/admin/events/1/edit")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/new_event_edit_sent.png", full_page=True)
    print("captured event edit (sent state)")

    # 3b. Event edit screen -- "Sending..." state (Vendanges)
    page.goto(f"{BASE}/admin/events/2/edit")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    page.screenshot(path=f"{OUT}/new_event_edit_sending.png", full_page=True)
    print("captured event edit (sending state)")

    # 4. RSVP list -- promo results panel
    page.goto(f"{BASE}/admin/events/1/rsvps")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    # Expand the promo results panel before screenshotting
    try:
        page.click("text=Show promo email results")
        page.wait_for_timeout(300)
    except Exception as e:
        print("toggle click failed:", e)
    page.screenshot(path=f"{OUT}/new_rsvps.png", full_page=True)
    print("captured rsvps")

    browser.close()
print("DONE")
