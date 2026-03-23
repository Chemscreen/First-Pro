"""
Drexel Blackboard Auto-Login
==============================
Uses Playwright to log into Drexel's Blackboard via
Drexel Connect SSO, then extracts session cookies for API access.

Optimized for low-memory environments (Render free tier, Docker).

Set in .env:
  DREXEL_USERNAME=as6436@drexel.edu
  DREXEL_PASSWORD=your_password
"""

import asyncio
import json
import os
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BB_URL = "https://learn.dcollege.net"
SESSION_FILE = Path(__file__).parent / ".bb_session.json"

# Chromium flags for low-memory containerized environments
CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-sync",
    "--disable-translate",
    "--no-first-run",
    "--no-zygote",
    "--single-process",
    "--mute-audio",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--js-flags=--max-old-space-size=256",
]


async def login_with_playwright(username: str, password: str) -> dict:
    """
    Playwright login handling Microsoft SSO flow step-by-step.
    Optimized for low-memory Docker/Render environments.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=CHROMIUM_ARGS,
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
        )
        # Longer default timeout for slow SSO redirects
        context.set_default_timeout(90000)
        page = await context.new_page()

        try:
            print("  Opening Blackboard...")
            await page.goto(BB_URL, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(5000)

            url = page.url.lower()
            print(f"  Landed on: {url[:100]}")

            # Microsoft SSO flow
            if "microsoftonline" in url or "login.microsoft" in url or "login.live" in url:
                print("  Microsoft SSO detected, entering credentials...")

                # Step 1: Email
                try:
                    email_field = page.locator('input[type="email"], input[name="loginfmt"]')
                    await email_field.wait_for(state="visible", timeout=15000)
                    await email_field.fill(username)
                    await page.wait_for_timeout(500)
                    submit = page.locator('input[type="submit"], button[type="submit"]')
                    await submit.click()
                    print("  Email submitted, waiting for password page...")
                    await page.wait_for_timeout(5000)
                except Exception as e:
                    print(f"  Email step issue: {e}")

                # Step 2: Password — might redirect to Drexel's own IdP
                try:
                    url_now = page.url.lower()
                    print(f"  Now on: {url_now[:100]}")

                    pwd_field = page.locator('input[type="password"], input[name="passwd"], input[name="password"]')
                    await pwd_field.wait_for(state="visible", timeout=20000)
                    await pwd_field.fill(password)
                    await page.wait_for_timeout(500)

                    # Click submit/sign-in button
                    submit = page.locator('input[type="submit"], button[type="submit"], button:has-text("Sign in"), button:has-text("Log in")')
                    await submit.first.click()
                    print("  Password submitted, waiting...")
                    await page.wait_for_timeout(5000)
                except Exception as e:
                    print(f"  Password step issue: {e}")

                # Step 3: "Stay signed in?" — click No
                try:
                    no_btn = page.locator('input[value="No"], button:has-text("No")')
                    await no_btn.click(timeout=8000)
                    print("  Clicked 'No' on stay signed in prompt")
                    await page.wait_for_timeout(3000)
                except Exception:
                    # May not appear, that's fine
                    pass

            # Check for MFA
            page_content = await page.content()
            page_url = page.url.lower()
            mfa_words = ["multi-factor", "mfa", "verify your identity", "authenticator",
                         "approve", "additional security", "two-factor", "2fa",
                         "strongauth", "kmsi"]
            if any(w in page_content.lower() or w in page_url for w in mfa_words):
                print()
                print("  ****************************************************")
                print("  *  MFA REQUIRED — Check your phone / Authenticator  *")
                print("  *  Approve the sign-in request, then wait...        *")
                print("  ****************************************************")
                print()

                # Wait up to 120 seconds for MFA approval
                for i in range(120):
                    await page.wait_for_timeout(1000)
                    current_url = page.url
                    if "learn.dcollege.net" in current_url:
                        print("  MFA approved! Redirected to Blackboard.")
                        break
                    if i % 10 == 0 and i > 0:
                        print(f"  Still waiting for MFA... ({i}s)")

                # After MFA, might get "Stay signed in?" again
                try:
                    no_btn = page.locator('input[value="No"], button:has-text("No")')
                    await no_btn.click(timeout=5000)
                    await page.wait_for_timeout(3000)
                except Exception:
                    pass

            # Wait for Blackboard to load
            current = page.url
            if "learn.dcollege.net" not in current:
                print(f"  Not on Blackboard yet ({current[:80]}), waiting...")
                try:
                    await page.wait_for_url("**learn.dcollege.net**", timeout=60000)
                except Exception:
                    # Try navigating directly
                    print("  Direct navigation to Blackboard...")
                    await page.goto(BB_URL, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(5000)

            final_url = page.url
            print(f"  Final URL: {final_url[:100]}")

            # Extract cookies
            cookies_list = await context.cookies()

            cookie_dict = {}
            for c in cookies_list:
                domain = c.get("domain", "")
                if "dcollege.net" in domain or "blackboard" in domain:
                    cookie_dict[c["name"]] = c["value"]

            if not cookie_dict:
                # Grab ALL cookies as fallback — some setups use different domains
                print(f"  No dcollege.net cookies found, grabbing all {len(cookies_list)} cookies...")
                for c in cookies_list:
                    cookie_dict[c["name"]] = c["value"]

            print(f"  Got {len(cookie_dict)} cookies from browser")
            return cookie_dict

        finally:
            await browser.close()


def save_session(cookies: dict):
    """Save cookies to file for reuse."""
    data = {
        "cookies": cookies,
        "timestamp": time.time(),
        "cookie_string": "; ".join(f"{k}={v}" for k, v in cookies.items()),
    }
    SESSION_FILE.write_text(json.dumps(data, indent=2))
    print(f"  Session saved to {SESSION_FILE.name}")


def load_session() -> dict | None:
    """Load saved session if it exists and is fresh (< 2 hours)."""
    if not SESSION_FILE.exists():
        return None
    try:
        data = json.loads(SESSION_FILE.read_text())
        age = time.time() - data.get("timestamp", 0)
        if age > 7200:  # 2 hours
            print("  Saved session expired, need fresh login")
            SESSION_FILE.unlink(missing_ok=True)
            return None
        return data
    except Exception:
        return None


def get_cookie_string() -> str:
    """Get a valid cookie string, logging in if necessary."""
    # Check saved session
    session = load_session()
    if session:
        print("  Using saved session")
        return session["cookie_string"]

    username = os.getenv("DREXEL_USERNAME", "")
    password = os.getenv("DREXEL_PASSWORD", "")

    if not username or not password:
        raise Exception(
            "Set DREXEL_USERNAME and DREXEL_PASSWORD in .env\n"
            "  DREXEL_USERNAME=as6436@drexel.edu\n"
            "  DREXEL_PASSWORD=your_password"
        )

    print("  Launching Playwright for Blackboard login...")
    cookies = asyncio.run(login_with_playwright(username, password))

    if not cookies:
        raise Exception("Login completed but no cookies were captured")

    save_session(cookies)
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def test_session(cookie_string: str) -> bool:
    """Test if a cookie string is still valid."""
    import requests
    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
    for pair in cookie_string.split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, value = pair.split("=", 1)
            session.cookies.set(name.strip(), value.strip())

    try:
        resp = session.get(f"{BB_URL}/learn/api/public/v1/users/me", timeout=15)
        return resp.ok
    except Exception:
        return False


if __name__ == "__main__":
    print()
    print("  ╔═══════════════════════════════════╗")
    print("  ║  Drexel Blackboard Auto-Login     ║")
    print("  ╚═══════════════════════════════════╝")
    print()

    try:
        cookie_str = get_cookie_string()
        print()
        print("  Testing session...")
        if test_session(cookie_str):
            print("  SUCCESS — connected to Blackboard!")
        else:
            print("  Got cookies but API test failed.")
            print("  You may need to approve MFA or try again.")
    except Exception as e:
        print(f"  Error: {e}")
