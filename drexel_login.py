"""
Drexel Blackboard Auto-Login
==============================
Uses Playwright (headless browser) to log into Drexel's Blackboard
via Drexel Connect SSO automatically, then extracts session cookies
for API access.

Stores credentials securely in .env — never transmitted anywhere
except to Drexel's own login servers.
"""

import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BB_URL = "https://learn.dcollege.net"
SESSION_FILE = Path(__file__).parent / ".bb_session.json"


def login_to_blackboard(username: str, password: str, headless: bool = True) -> dict:
    """
    Log into Drexel Blackboard via Drexel Connect SSO.
    Returns a dict of cookies on success.
    """
    print("  Launching browser...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        # Go to Blackboard — it'll redirect to Drexel Connect SSO
        print("  Opening Blackboard...")
        page.goto(BB_URL, wait_until="networkidle", timeout=30000)

        # Wait for login form — could be Microsoft/SAML login
        print("  Waiting for login page...")
        page.wait_for_timeout(2000)

        # Try to detect the login form type
        url = page.url.lower()

        if "microsoftonline" in url or "login.microsoft" in url:
            # Microsoft SSO flow (Drexel uses Microsoft/Azure AD)
            print("  Microsoft SSO detected...")
            _handle_microsoft_login(page, username, password)
        elif "connect.drexel.edu" in url:
            # Direct Drexel Connect
            print("  Drexel Connect detected...")
            _handle_drexel_connect(page, username, password)
        elif "learn.dcollege.net" in url and "ultra" in url:
            # Already logged in (cached session)
            print("  Already logged in!")
        else:
            # Generic form — try to find username/password fields
            print(f"  Login page: {page.url[:80]}...")
            _handle_generic_login(page, username, password)

        # Wait for Blackboard to load after login
        print("  Waiting for Blackboard to load...")
        try:
            page.wait_for_url("**/ultra/**", timeout=30000)
        except Exception:
            # Maybe we need to navigate back to BB after auth
            page.goto(BB_URL, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)

        # Check if we're on Blackboard
        final_url = page.url
        if "learn.dcollege.net" not in final_url:
            browser.close()
            raise Exception(f"Login may have failed. Ended up at: {final_url}")

        # Extract cookies
        cookies = context.cookies()
        browser.close()

        # Convert to simple dict
        cookie_dict = {}
        for c in cookies:
            if "dcollege.net" in c.get("domain", "") or "blackboard" in c.get("domain", ""):
                cookie_dict[c["name"]] = c["value"]

        if not cookie_dict:
            raise Exception("No Blackboard cookies found after login")

        print(f"  Got {len(cookie_dict)} cookies")
        return cookie_dict


def _handle_microsoft_login(page, username, password):
    """Handle Microsoft/Azure AD SSO login."""
    # Enter email
    try:
        email_input = page.wait_for_selector('input[type="email"], input[name="loginfmt"]', timeout=10000)
        email_input.fill(username if "@" in username else f"{username}@drexel.edu")
        page.click('input[type="submit"], button[type="submit"]')
        page.wait_for_timeout(2000)
    except Exception:
        pass

    # Enter password
    try:
        pwd_input = page.wait_for_selector('input[type="password"], input[name="passwd"]', timeout=10000)
        pwd_input.fill(password)
        page.click('input[type="submit"], button[type="submit"]')
        page.wait_for_timeout(3000)
    except Exception:
        pass

    # Handle "Stay signed in?" prompt
    try:
        stay_signed = page.wait_for_selector('input[value="No"], button:has-text("No")', timeout=5000)
        stay_signed.click()
        page.wait_for_timeout(2000)
    except Exception:
        pass

    # Handle MFA if prompted
    _check_mfa(page)


def _handle_drexel_connect(page, username, password):
    """Handle Drexel Connect login form."""
    try:
        user_input = page.wait_for_selector('input[name="username"], input[name="j_username"], input[type="text"]', timeout=10000)
        user_input.fill(username)
    except Exception:
        pass

    try:
        pwd_input = page.wait_for_selector('input[name="password"], input[name="j_password"], input[type="password"]', timeout=5000)
        pwd_input.fill(password)
    except Exception:
        pass

    try:
        page.click('button[type="submit"], input[type="submit"], .btn-primary, button:has-text("Sign In"), button:has-text("Log In")')
        page.wait_for_timeout(3000)
    except Exception:
        pass

    _check_mfa(page)


def _handle_generic_login(page, username, password):
    """Fallback for unrecognized login forms."""
    try:
        inputs = page.query_selector_all('input[type="text"], input[type="email"]')
        if inputs:
            inputs[0].fill(username if "@" in username else f"{username}@drexel.edu")
    except Exception:
        pass

    try:
        pwd = page.query_selector('input[type="password"]')
        if pwd:
            pwd.fill(password)
    except Exception:
        pass

    try:
        page.click('button[type="submit"], input[type="submit"]')
        page.wait_for_timeout(3000)
    except Exception:
        pass

    _check_mfa(page)


def _check_mfa(page):
    """Check if MFA/2FA is required and wait for user to complete it."""
    url = page.url.lower()
    page_text = page.content().lower()

    mfa_indicators = [
        "multi-factor", "mfa", "two-factor", "2fa", "verification",
        "authenticator", "approve", "push notification",
        "additional security", "verify your identity",
    ]

    if any(ind in page_text for ind in mfa_indicators) or "mfa" in url:
        print()
        print("  ╔══════════════════════════════════════════╗")
        print("  ║  MFA Required — check your phone/app    ║")
        print("  ║  Approve the login, then wait...        ║")
        print("  ╚══════════════════════════════════════════╝")
        print()
        # Wait up to 60 seconds for MFA
        for i in range(60):
            time.sleep(1)
            new_url = page.url.lower()
            if "learn.dcollege.net" in new_url or "ultra" in new_url:
                print("  MFA approved!")
                return
            if new_url != url:
                # URL changed, MFA probably done
                page.wait_for_timeout(2000)
                return
        print("  MFA timeout — you may need to try again")


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
            print("  Saved session expired (>2h), need fresh login")
            return None
        return data
    except Exception:
        return None


def get_cookie_string() -> str:
    """Get a valid cookie string, logging in if necessary."""
    # Check for saved session
    session = load_session()
    if session:
        print("  Using saved session")
        return session["cookie_string"]

    # Check env
    username = os.getenv("DREXEL_USERNAME", "")
    password = os.getenv("DREXEL_PASSWORD", "")

    if not username or not password:
        raise Exception(
            "Set DREXEL_USERNAME and DREXEL_PASSWORD in .env\n"
            "  DREXEL_USERNAME=abc123  (your Drexel ID)\n"
            "  DREXEL_PASSWORD=your_password"
        )

    cookies = login_to_blackboard(username, password)
    save_session(cookies)
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def test_session(cookie_string: str) -> bool:
    """Test if a cookie string is still valid."""
    import requests
    session = requests.Session()
    for pair in cookie_string.split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, value = pair.split("=", 1)
            session.cookies.set(name.strip(), value.strip())

    resp = session.get(f"{BB_URL}/learn/api/public/v1/users/me")
    return resp.ok


if __name__ == "__main__":
    print("Drexel Blackboard Auto-Login")
    print("=" * 40)
    print()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    try:
        cookie_str = get_cookie_string()
        print()
        print("Testing session...")
        if test_session(cookie_str):
            print("SUCCESS — session is valid!")
            print()
            print("Cookie string (first 80 chars):")
            print(f"  {cookie_str[:80]}...")
        else:
            print("Session obtained but API test failed.")
            print("Cookies may be incomplete. Try running with headless=False to debug.")
    except Exception as e:
        print(f"Error: {e}")
