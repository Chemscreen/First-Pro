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

DREXEL_CONNECT = "https://connect.drexel.edu"
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
            # ── Step 1: Go to Drexel Connect and click "SIGN IN" ──
            print("  Opening Drexel Connect...")
            await page.goto(DREXEL_CONNECT, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(3000)

            url = page.url.lower()
            print(f"  Landed on: {url[:120]}")

            # Click the yellow "SIGN IN" button on Drexel Connect
            try:
                sign_in_link = page.locator('a:has-text("Sign In"), a:has-text("SIGN IN"), button:has-text("Sign In")')
                await sign_in_link.first.click(timeout=10000)
                print("  Clicked 'SIGN IN' on Drexel Connect...")
                await page.wait_for_timeout(5000)
            except Exception as e:
                print(f"  No Sign In button found ({e}), may already be on SSO...")

            url = page.url.lower()
            print(f"  Now on: {url[:120]}")

            # ── Step 2: Microsoft login (email → password) ──
            if "microsoftonline" in url or "login.microsoft" in url or "login.live" in url:
                print("  Microsoft SSO page detected...")

                # Enter email
                try:
                    email_field = page.locator('input[type="email"], input[name="loginfmt"]')
                    await email_field.wait_for(state="visible", timeout=15000)
                    await email_field.fill(username)
                    await page.wait_for_timeout(1000)

                    next_btn = page.locator('input[type="submit"], input#idSIButton9')
                    await next_btn.first.click()
                    print(f"  Email submitted: {username}")
                    await page.wait_for_timeout(5000)
                except Exception as e:
                    print(f"  Email step: {e}")

                url_now = page.url.lower()
                print(f"  After email: {url_now[:120]}")

                # Enter password (on Microsoft or Drexel IdP)
                try:
                    pwd_field = page.locator('input[type="password"]')
                    await pwd_field.wait_for(state="visible", timeout=20000)
                    await pwd_field.fill(password)
                    await page.wait_for_timeout(500)

                    submit = page.locator('input[type="submit"], input#idSIButton9, button[type="submit"]')
                    await submit.first.click()
                    print("  Password submitted...")
                    await page.wait_for_timeout(5000)
                except Exception as e:
                    print(f"  Password step: {e}")

            # ── Step 3: Check for MFA ──
            page_content = await page.content()
            page_url = page.url.lower()

            # MFA if still on Microsoft after password
            needs_mfa = "microsoftonline" in page_url or "login.microsoft" in page_url
            if not needs_mfa:
                mfa_words = ["verify your identity", "authenticator", "approve",
                             "additional security", "strongauth", "push notification"]
                needs_mfa = any(w in page_content.lower() for w in mfa_words)

            if needs_mfa:
                print()
                print("  ****************************************************")
                print("  *  MFA REQUIRED — Check your phone / Authenticator  *")
                print("  *  Approve the sign-in request, then wait...        *")
                print("  ****************************************************")
                print()

                for i in range(120):
                    await page.wait_for_timeout(1000)
                    current_url = page.url.lower()
                    # Done if we left Microsoft
                    if "microsoftonline" not in current_url and "login.microsoft" not in current_url:
                        print(f"  MFA approved! Now on: {current_url[:80]}")
                        break
                    # Check for "Stay signed in?" prompt (means MFA passed)
                    try:
                        kmsi = page.locator('#KmsiDescription, input[value="Yes"], input[value="No"]')
                        if await kmsi.first.is_visible():
                            print("  MFA approved! 'Stay signed in' prompt detected.")
                            break
                    except Exception:
                        pass
                    if i % 10 == 0 and i > 0:
                        print(f"  Still waiting for MFA... ({i}s)")

            # ── Step 4: "Stay signed in?" — click Yes ──
            try:
                yes_btn = page.locator('input[value="Yes"], input#idSIButton9')
                await yes_btn.first.click(timeout=5000)
                print("  Clicked 'Yes' on stay signed in")
                await page.wait_for_timeout(5000)
            except Exception:
                pass

            current = page.url
            print(f"  After Drexel Connect SSO: {current[:120]}")

            # ── Step 5: Navigate to Blackboard (Drexel Learn) ──
            # Use the SAML login entry point for Blackboard Ultra
            bb_login_url = f"{BB_URL}/ultra/institution-page"
            print(f"  Now navigating to Blackboard ({bb_login_url})...")
            await page.goto(bb_login_url, wait_until="networkidle", timeout=90000)
            await page.wait_for_timeout(5000)

            current = page.url.lower()
            print(f"  After BB nav: {current[:120]}")

            # If Blackboard triggers SSO redirect, handle it
            if "microsoftonline" in current or "login.microsoft" in current:
                print("  Blackboard SSO redirect detected...")

                # If account picker shows, click our account
                try:
                    acct = page.locator(f'small:has-text("{username}"), div[data-test-id="{username}"]')
                    if await acct.first.is_visible(timeout=3000):
                        await acct.first.click()
                        print(f"  Clicked account: {username}")
                        await page.wait_for_timeout(5000)
                except Exception:
                    pass

                # Wait for redirect back to Blackboard (up to 60s)
                print("  Waiting for Blackboard redirect...")
                try:
                    await page.wait_for_url("**learn.dcollege.net**", timeout=60000)
                    await page.wait_for_timeout(5000)
                except Exception:
                    print(f"  Still on: {page.url[:120]}")

            # If still not on Blackboard, try direct URL
            current = page.url
            if "learn.dcollege.net" not in current:
                print(f"  Not on Blackboard yet ({current[:80]}), trying direct...")
                await page.goto(BB_URL, wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(5000)

            final_url = page.url
            print(f"  Final URL: {final_url[:120]}")

            # ── Step 6: Extract cookies ──
            cookies_list = await context.cookies()

            # Log all cookie domains for debugging
            domains = set(c.get("domain", "?") for c in cookies_list)
            print(f"  Cookie domains: {domains}")
            print(f"  Total cookies from browser: {len(cookies_list)}")

            # Prioritize Blackboard-specific cookies
            cookie_dict = {}
            bb_cookies = {}
            for c in cookies_list:
                domain = c.get("domain", "")
                if "dcollege.net" in domain or "blackboard" in domain:
                    bb_cookies[c["name"]] = c["value"]

            if bb_cookies:
                cookie_dict = bb_cookies
                print(f"  Blackboard cookies ({len(bb_cookies)}): {list(bb_cookies.keys())}")
            else:
                print("  WARNING: No dcollege.net/blackboard cookies found!")
                print("  This likely means Blackboard SSO didn't complete.")
                # Grab everything as fallback
                for c in cookies_list:
                    cookie_dict[c["name"]] = c["value"]
                print(f"  Using all {len(cookie_dict)} cookies as fallback")

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
    cookie_names = []
    for pair in cookie_string.split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, value = pair.split("=", 1)
            session.cookies.set(name.strip(), value.strip())
            cookie_names.append(name.strip())

    print(f"  Testing session with cookies: {cookie_names}")
    try:
        resp = session.get(f"{BB_URL}/learn/api/public/v1/users/me", timeout=15)
        print(f"  Test result: {resp.status_code} {resp.reason}")
        if not resp.ok:
            print(f"  Response body: {resp.text[:300]}")
        return resp.ok
    except Exception as e:
        print(f"  Test error: {e}")
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
