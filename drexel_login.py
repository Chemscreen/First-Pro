"""
Drexel Blackboard Auto-Login
==============================
Uses Playwright to log into Drexel's Blackboard via
Drexel Connect SSO, then extracts session cookies for API access.

KEY DESIGN: Saves ALL cookies (Microsoft SSO + Blackboard) so that
subsequent logins can skip MFA by reusing the Microsoft session.

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
SESSION_DIR = Path("/data")  # Fly.io volume mount point

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


def get_session_path() -> Path:
    """Get the best path to store session data (persistent volume > local)."""
    if SESSION_DIR.exists() and SESSION_DIR.is_dir():
        return SESSION_DIR / ".bb_session.json"
    return SESSION_FILE


async def login_with_playwright(username: str, password: str, saved_cookies: list = None) -> dict:
    """
    Playwright login handling Microsoft SSO flow step-by-step.

    If saved_cookies is provided, injects them first to try skipping MFA.
    Returns dict with 'bb_cookies' and 'all_cookies'.
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
        context.set_default_timeout(90000)

        # Restore saved cookies (Microsoft SSO cookies for MFA-free re-auth)
        if saved_cookies:
            try:
                await context.add_cookies(saved_cookies)
                print(f"  Restored {len(saved_cookies)} saved cookies for SSO re-auth")
            except Exception as e:
                print(f"  Warning: could not restore cookies: {e}")

        page = await context.new_page()

        try:
            # ── Step 1: Go to Blackboard directly (triggers SAML2 SSO) ──
            bb_login_url = f"{BB_URL}/ultra/institution-page"
            print(f"  Opening Blackboard ({bb_login_url})...")
            await page.goto(bb_login_url, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(3000)

            url = page.url.lower()
            print(f"  Redirected to: {page.url[:120]}")

            # Check if we're already on Blackboard (SSO cookies worked!)
            if "learn.dcollege.net" in url and "login" not in url:
                print("  Already authenticated via saved SSO cookies!")
            else:
                # ── Step 2: Handle Drexel Connect if redirected there ──
                if "connect.drexel.edu" in url:
                    try:
                        sign_in_link = page.locator('a:has-text("Sign In"), a:has-text("SIGN IN"), button:has-text("Sign In")')
                        await sign_in_link.first.click(timeout=10000)
                        print("  Clicked 'SIGN IN' on Drexel Connect...")
                        await page.wait_for_timeout(5000)
                    except Exception as e:
                        print(f"  No Sign In button found ({e}), may already be on SSO...")

                    url = page.url.lower()
                    print(f"  Now on: {url[:120]}")

                # ── Step 3: Microsoft login (email → password) ──
                if "microsoftonline" in url or "login.microsoft" in url or "login.live" in url:
                    print("  Microsoft SSO page detected...")

                    # Check if already signed in (account picker)
                    try:
                        acct = page.locator(f'small:has-text("{username}"), div[data-test-id="{username}"]')
                        if await acct.first.is_visible(timeout=3000):
                            await acct.first.click()
                            print(f"  Clicked saved account: {username}")
                            await page.wait_for_timeout(5000)
                            url = page.url.lower()
                            if "learn.dcollege.net" in url:
                                print("  SSO re-auth succeeded without MFA!")
                    except Exception:
                        pass

                    url = page.url.lower()
                    if "microsoftonline" in url or "login.microsoft" in url:
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

                # ── Step 4: Check for MFA ──
                page_content = await page.content()
                page_url = page.url.lower()

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
                        if "microsoftonline" not in current_url and "login.microsoft" not in current_url:
                            print(f"  MFA approved! Redirected to Blackboard.")
                            break
                        try:
                            kmsi = page.locator('#KmsiDescription, input[value="Yes"], input[value="No"]')
                            if await kmsi.first.is_visible():
                                print("  MFA approved! 'Stay signed in' prompt detected.")
                                break
                        except Exception:
                            pass
                        if i % 10 == 0 and i > 0:
                            print(f"  Still waiting for MFA... ({i}s)")

                # ── Step 5: "Stay signed in?" — click Yes (important for SSO persistence) ──
                try:
                    yes_btn = page.locator('input[value="Yes"], input#idSIButton9')
                    await yes_btn.first.click(timeout=5000)
                    print("  Clicked 'Yes' on stay signed in")
                    await page.wait_for_timeout(5000)
                except Exception:
                    pass

                # ── Step 6: Ensure we're on Blackboard ──
                current = page.url
                print(f"  After SSO: {current[:120]}")

                if "learn.dcollege.net" not in current:
                    print(f"  Not on Blackboard yet, navigating directly...")
                    await page.goto(bb_login_url, wait_until="networkidle", timeout=90000)
                    await page.wait_for_timeout(5000)

                    if "learn.dcollege.net" not in page.url:
                        await page.goto(BB_URL, wait_until="networkidle", timeout=60000)
                        await page.wait_for_timeout(5000)

            final_url = page.url
            print(f"  Final URL: {final_url[:120]}")

            # ── Step 7: Navigate to API endpoint in browser to ensure REST cookies ──
            print("  Validating session via Blackboard API in browser...")
            api_url = f"{BB_URL}/learn/api/public/v1/users/me"
            resp = await page.goto(api_url, wait_until="networkidle", timeout=30000)
            api_body = await page.content()

            if resp and resp.status == 200:
                print(f"  Browser API check PASSED (status {resp.status})")
            else:
                status = resp.status if resp else "no response"
                print(f"  Browser API check: status {status}")
                print(f"  Response preview: {api_body[:200]}")

            # ── Step 8: Extract ALL cookies ──
            all_cookies_list = await context.cookies()

            # Log cookie summary
            domains = {}
            for c in all_cookies_list:
                d = c.get("domain", "?")
                domains[d] = domains.get(d, 0) + 1
            print(f"  Total cookies: {len(all_cookies_list)}")
            print(f"  Cookie domains: {dict(sorted(domains.items()))}")

            # Separate Blackboard cookies for API use
            bb_cookies = {}
            for c in all_cookies_list:
                domain = c.get("domain", "")
                if "dcollege.net" in domain or "blackboard" in domain:
                    bb_cookies[c["name"]] = c["value"]

            if bb_cookies:
                print(f"  Blackboard cookies ({len(bb_cookies)}): {list(bb_cookies.keys())}")
            else:
                print("  WARNING: No Blackboard cookies found!")
                print("  Falling back to all cookies...")
                for c in all_cookies_list:
                    bb_cookies[c["name"]] = c["value"]

            return {
                "bb_cookies": bb_cookies,
                "all_cookies": all_cookies_list,  # Full list for SSO re-auth
            }

        finally:
            await browser.close()


def save_session(login_result: dict):
    """Save cookies to file for reuse. Saves both BB cookies and full SSO cookies."""
    bb_cookies = login_result["bb_cookies"]
    all_cookies = login_result.get("all_cookies", [])

    data = {
        "cookies": bb_cookies,
        "cookie_string": "; ".join(f"{k}={v}" for k, v in bb_cookies.items()),
        "all_cookies": all_cookies,  # For SSO re-auth
        "timestamp": time.time(),
    }

    path = get_session_path()
    path.write_text(json.dumps(data, indent=2))
    print(f"  Session saved to {path} ({len(bb_cookies)} BB cookies, {len(all_cookies)} total cookies)")


def load_session() -> dict | None:
    """Load saved session if it exists and is fresh (< 6 hours)."""
    path = get_session_path()
    if not path.exists():
        # Fallback to local file if volume path doesn't work
        if not SESSION_FILE.exists():
            return None
        path = SESSION_FILE

    try:
        data = json.loads(path.read_text())
        age = time.time() - data.get("timestamp", 0)
        age_mins = int(age / 60)
        age_hrs = age / 3600

        if age > 21600:  # 6 hours
            print(f"  Saved session expired ({age_hrs:.1f}h old), need fresh login")
            path.unlink(missing_ok=True)
            return None

        print(f"  Loaded session ({age_mins}m old, {len(data.get('cookies', {}))} cookies)")
        return data
    except Exception as e:
        print(f"  Error loading session: {e}")
        return None


def get_cookie_string() -> str:
    """Get a valid cookie string, logging in if necessary."""
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

    # Try to use saved SSO cookies for MFA-free re-auth
    saved_sso_cookies = None
    try:
        path = get_session_path()
        if path.exists():
            old_data = json.loads(path.read_text())
            saved_sso_cookies = old_data.get("all_cookies", [])
            if saved_sso_cookies:
                print(f"  Found {len(saved_sso_cookies)} saved SSO cookies for re-auth")
    except Exception:
        pass
    # Also check fallback location
    if not saved_sso_cookies:
        try:
            if SESSION_FILE.exists():
                old_data = json.loads(SESSION_FILE.read_text())
                saved_sso_cookies = old_data.get("all_cookies", [])
                if saved_sso_cookies:
                    print(f"  Found {len(saved_sso_cookies)} saved SSO cookies (fallback)")
        except Exception:
            pass

    result = asyncio.run(login_with_playwright(username, password, saved_sso_cookies))

    if not result or not result.get("bb_cookies"):
        raise Exception("Login completed but no cookies were captured")

    save_session(result)
    return "; ".join(f"{k}={v}" for k, v in result["bb_cookies"].items())


def test_session(cookie_string: str) -> bool:
    """Test if a cookie string is still valid by calling Blackboard API."""
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

    print(f"  Testing session with {len(cookie_names)} cookies: {cookie_names}")
    try:
        resp = session.get(f"{BB_URL}/learn/api/public/v1/users/me", timeout=15)
        print(f"  Test result: {resp.status_code} {resp.reason}")
        if resp.ok:
            try:
                user = resp.json()
                name = user.get("name", {})
                print(f"  Logged in as: {name.get('given', '')} {name.get('family', '')} ({user.get('userName', '')})")
            except Exception:
                pass
            return True
        else:
            print(f"  Response: {resp.text[:300]}")
            return False
    except Exception as e:
        print(f"  Test error: {e}")
        return False


if __name__ == "__main__":
    print()
    print("  Drexel Blackboard Auto-Login")
    print("  " + "=" * 30)
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
