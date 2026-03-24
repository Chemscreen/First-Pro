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
            # ── Step 1: Open Blackboard → triggers SAML2 redirect to Microsoft ──
            print("  Opening Blackboard (triggers SAML2 SSO)...")
            await page.goto(BB_URL, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(5000)

            url = page.url.lower()
            print(f"  Redirected to: {url[:120]}")

            # ── Step 2: Handle Microsoft Azure AD SAML login ──
            if "microsoftonline" in url or "login.microsoft" in url or "login.live" in url:
                print("  Microsoft SAML2 login detected...")

                # Email / username field
                try:
                    email_field = page.locator('input[type="email"], input[name="loginfmt"]')
                    await email_field.wait_for(state="visible", timeout=15000)
                    await email_field.fill(username)
                    await page.wait_for_timeout(1000)

                    next_btn = page.locator('input[type="submit"][value="Next"], input#idSIButton9')
                    await next_btn.first.click()
                    print(f"  Email submitted: {username}")
                    await page.wait_for_timeout(5000)
                except Exception as e:
                    print(f"  Email step: {e}")
                    # SAML may skip email if tenant is known, check if already on password
                    pass

                url_now = page.url.lower()
                print(f"  Now on: {url_now[:120]}")

                # Drexel may redirect to their own IdP (e.g. idp.drexel.edu, connect.drexel.edu)
                if "drexel" in url_now and "microsoftonline" not in url_now:
                    print("  Redirected to Drexel IdP, entering credentials there...")
                    try:
                        user_field = page.locator('input[name="username"], input[name="UserName"], input[name="j_username"], input[type="text"]').first
                        await user_field.wait_for(state="visible", timeout=15000)
                        await user_field.fill(username)

                        pwd_field = page.locator('input[type="password"], input[name="password"], input[name="Password"], input[name="j_password"]').first
                        await pwd_field.fill(password)
                        await page.wait_for_timeout(500)

                        submit = page.locator('button[type="submit"], input[type="submit"], button:has-text("Sign in"), button:has-text("Log in"), button:has-text("Login")').first
                        await submit.click()
                        print("  Drexel IdP credentials submitted...")
                        await page.wait_for_timeout(5000)
                    except Exception as e:
                        print(f"  Drexel IdP step: {e}")
                else:
                    # Still on Microsoft — enter password
                    try:
                        pwd_field = page.locator('input[type="password"], input[name="passwd"]')
                        await pwd_field.wait_for(state="visible", timeout=20000)
                        await pwd_field.fill(password)
                        await page.wait_for_timeout(500)

                        sign_in = page.locator('input[type="submit"][value="Sign in"], input#idSIButton9, button[type="submit"]')
                        await sign_in.first.click()
                        print("  Password submitted on Microsoft...")
                        await page.wait_for_timeout(5000)
                    except Exception as e:
                        print(f"  Password step: {e}")

            # ── Step 3: Check for MFA ──
            page_content = await page.content()
            page_url = page.url.lower()
            mfa_indicators = [
                "multi-factor", "mfa", "verify your identity", "authenticator",
                "approve", "additional security", "two-factor", "2fa",
                "strongauth", "kmsi", "push notification",
                "entercode", "verificationcode", "polling"
            ]
            needs_mfa = any(w in page_content.lower() or w in page_url for w in mfa_indicators)
            # Also check if we're still on Microsoft (not yet on Blackboard)
            if not needs_mfa and "microsoftonline" in page_url and "learn.dcollege" not in page_url:
                needs_mfa = True
                print("  Still on Microsoft after password — likely waiting for MFA...")

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
                    if "learn.dcollege" in current_url:
                        print("  MFA approved! Redirected to Blackboard.")
                        break
                    # Check for "Stay signed in?" prompt (means MFA passed)
                    try:
                        stay_signed = page.locator('input[value="Yes"], input[value="No"], #idBtn_Back')
                        if await stay_signed.first.is_visible():
                            print("  MFA approved! Handling 'Stay signed in' prompt...")
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
                try:
                    no_btn = page.locator('input[value="No"], input#idBtn_Back')
                    await no_btn.first.click(timeout=3000)
                    await page.wait_for_timeout(3000)
                except Exception:
                    pass

            # ── Step 5: Wait for Blackboard to load ──
            current = page.url
            print(f"  After SSO: {current[:120]}")
            if "learn.dcollege.net" not in current:
                print("  Waiting for Blackboard redirect...")
                try:
                    await page.wait_for_url("**learn.dcollege.net**", timeout=30000)
                except Exception:
                    print("  Navigating directly to Blackboard...")
                    await page.goto(BB_URL, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(5000)

            final_url = page.url
            print(f"  Final URL: {final_url[:120]}")

            # ── Step 6: Extract cookies ──
            cookies_list = await context.cookies()

            cookie_dict = {}
            for c in cookies_list:
                domain = c.get("domain", "")
                if "dcollege.net" in domain or "blackboard" in domain:
                    cookie_dict[c["name"]] = c["value"]

            if not cookie_dict:
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
