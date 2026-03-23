"""
Drexel Blackboard Auto-Login (browser-use)
============================================
Uses browser-use (AI-powered browser automation) to log into
Drexel's Blackboard via Drexel Connect SSO, then extracts
session cookies for API access.

Requirements:
  pip install browser-use langchain-anthropic

Set in .env:
  DREXEL_USERNAME=as6436@drexel.edu
  DREXEL_PASSWORD=your_password
  ANTHROPIC_API_KEY=sk-...  (for the AI agent)
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


async def login_with_browser_use(username: str, password: str) -> dict:
    """
    Use browser-use AI agent to log into Drexel Blackboard.
    The AI agent navigates the SSO flow automatically.
    Returns a dict of cookies.
    """
    from browser_use import Agent, Browser, BrowserConfig
    from langchain_anthropic import ChatAnthropic

    llm = ChatAnthropic(
        model_name="claude-sonnet-4-20250514",
        temperature=0,
    )

    browser = Browser(config=BrowserConfig(
        headless=True,
    ))

    task = f"""
    Go to {BB_URL} and log in with these Drexel credentials:
    - Username/Email: {username}
    - Password: {password}

    Steps:
    1. Navigate to {BB_URL}
    2. You will be redirected to a Microsoft/Drexel Connect SSO login page
    3. Enter the email/username in the email field and click Next/Submit
    4. Enter the password in the password field and click Sign In/Submit
    5. If asked "Stay signed in?" click No
    6. Wait for the page to redirect back to Blackboard (the URL should contain learn.dcollege.net/ultra)
    7. Once you see the Blackboard dashboard, the login is complete

    IMPORTANT: Do NOT click any other links or navigate away. Just log in and stop.
    If you see an MFA/2FA prompt, just wait — the user will approve it on their phone.
    """

    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
    )

    print("  AI agent starting login flow...")
    await agent.run(max_steps=15)

    # Extract cookies from the browser context
    context = await browser.get_current_context()
    pages = context.pages
    cookies_list = await context.cookies()

    await browser.close()

    # Filter for Blackboard cookies
    cookie_dict = {}
    for c in cookies_list:
        domain = c.get("domain", "")
        if "dcollege.net" in domain or "blackboard" in domain:
            cookie_dict[c["name"]] = c["value"]

    if not cookie_dict:
        raise Exception("No Blackboard cookies found after login")

    print(f"  Got {len(cookie_dict)} cookies from Blackboard")
    return cookie_dict


async def login_with_playwright_direct(username: str, password: str) -> dict:
    """
    Fallback: Direct Playwright login without AI agent.
    Handles Microsoft SSO flow step-by-step.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0"
        )
        page = await context.new_page()

        print("  Opening Blackboard...")
        await page.goto(BB_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        url = page.url.lower()
        print(f"  Landed on: {url[:80]}")

        # Microsoft SSO flow
        if "microsoftonline" in url or "login.microsoft" in url or "login.live" in url:
            print("  Microsoft SSO detected, entering credentials...")

            # Email
            try:
                email_field = page.locator('input[type="email"], input[name="loginfmt"]')
                await email_field.fill(username)
                await page.locator('input[type="submit"]').click()
                await page.wait_for_timeout(3000)
            except Exception as e:
                print(f"  Email step: {e}")

            # Password
            try:
                pwd_field = page.locator('input[type="password"], input[name="passwd"]')
                await pwd_field.fill(password)
                await page.locator('input[type="submit"]').click()
                await page.wait_for_timeout(3000)
            except Exception as e:
                print(f"  Password step: {e}")

            # Stay signed in? No
            try:
                no_btn = page.locator('input[value="No"], button:has-text("No")')
                await no_btn.click(timeout=5000)
                await page.wait_for_timeout(2000)
            except Exception:
                pass

        # Check for MFA
        page_content = await page.content()
        mfa_words = ["multi-factor", "mfa", "verify", "authenticator", "approve"]
        if any(w in page_content.lower() for w in mfa_words):
            print()
            print("  ╔══════════════════════════════════════════╗")
            print("  ║  MFA Required — check your phone/app    ║")
            print("  ║  Approve the login, then wait...        ║")
            print("  ╚══════════════════════════════════════════╝")
            print()
            for _ in range(60):
                await page.wait_for_timeout(1000)
                if "learn.dcollege.net" in page.url:
                    break

        # Wait for Blackboard
        try:
            await page.wait_for_url("**/ultra/**", timeout=30000)
        except Exception:
            await page.goto(BB_URL, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(3000)

        # Extract cookies
        cookies_list = await context.cookies()
        await browser.close()

        cookie_dict = {}
        for c in cookies_list:
            domain = c.get("domain", "")
            if "dcollege.net" in domain or "blackboard" in domain:
                cookie_dict[c["name"]] = c["value"]

        if not cookie_dict:
            raise Exception("No Blackboard cookies found after login")

        print(f"  Got {len(cookie_dict)} cookies")
        return cookie_dict


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

    # Try browser-use first (AI-powered), fall back to direct Playwright
    api_key = os.getenv("ANTHROPIC_API_KEY", "")

    if api_key:
        print("  Using browser-use AI agent for login...")
        try:
            cookies = asyncio.run(login_with_browser_use(username, password))
            save_session(cookies)
            return "; ".join(f"{k}={v}" for k, v in cookies.items())
        except Exception as e:
            print(f"  browser-use failed ({e}), trying direct Playwright...")

    print("  Using direct Playwright for login...")
    cookies = asyncio.run(login_with_playwright_direct(username, password))
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

    resp = session.get(f"{BB_URL}/learn/api/public/v1/users/me", timeout=10)
    return resp.ok


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
