#!/usr/bin/env python3
"""
Drexel Autopilot — One-Command Launcher
=========================================
Run this and everything happens automatically:
  1. Checks dependencies
  2. Logs into Blackboard via browser-use
  3. Starts the web dashboard
  4. Opens your browser

Usage:
  python start.py
"""

import subprocess
import sys
import os
from pathlib import Path

ROOT = Path(__file__).parent
os.chdir(ROOT)


def check_deps():
    """Install missing dependencies."""
    print("  Checking dependencies...")
    missing = []
    for pkg in ["flask", "requests", "dotenv", "playwright"]:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    try:
        __import__("browser_use")
    except ImportError:
        missing.append("browser-use")

    try:
        __import__("langchain_google_genai")
    except ImportError:
        missing.append("langchain-google-genai")

    if missing:
        print(f"  Installing: {', '.join(missing)}")
        subprocess.check_call([
            sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"
        ])
        # Install Playwright browsers
        subprocess.check_call(["playwright", "install", "chromium"])
        print("  Dependencies installed!")
    else:
        print("  All dependencies ready")


def check_env():
    """Verify .env is set up."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        print("\n  .env file not found!")
        print("  Creating from template...")
        import shutil
        shutil.copy(ROOT / ".env.example", env_file)
        print("  Please edit .env with your Drexel credentials, then run again.")
        sys.exit(1)

    from dotenv import load_dotenv
    load_dotenv()

    username = os.getenv("DREXEL_USERNAME", "")
    password = os.getenv("DREXEL_PASSWORD", "")

    if not username or not password or "your_password" in password:
        print("\n  Drexel credentials not set in .env!")
        print("  Edit .env and set DREXEL_USERNAME and DREXEL_PASSWORD")
        sys.exit(1)

    print(f"  Drexel user: {username}")

    api_key = os.getenv("GOOGLE_API_KEY", "")
    if api_key and api_key.startswith("AIza"):
        print("  browser-use AI agent: enabled (Gemini Flash)")
    else:
        print("  browser-use AI agent: disabled (will use direct Playwright)")
        print("  Tip: Add GOOGLE_API_KEY to .env for smarter login handling")
        print("  Get one free at https://aistudio.google.com/apikey")


def test_login():
    """Test Blackboard connection."""
    from drexel_login import load_session, get_cookie_string, test_session

    session = load_session()
    if session:
        print("  Testing saved session...")
        if test_session(session["cookie_string"]):
            print("  Blackboard session: valid")
            return True
        print("  Saved session expired, logging in fresh...")

    print("  Logging into Blackboard...")
    try:
        cookie_str = get_cookie_string()
        if test_session(cookie_str):
            print("  Blackboard: connected!")
            return True
        print("  Login completed but API test failed")
        return False
    except Exception as e:
        print(f"  Login error: {e}")
        return False


def start_server():
    """Launch the Flask dashboard."""
    import webbrowser
    print()
    print("  ╔═══════════════════════════════════╗")
    print("  ║       Drexel Autopilot            ║")
    print("  ║       http://localhost:5000        ║")
    print("  ╚═══════════════════════════════════╝")
    print()
    print("  Opening browser...")
    webbrowser.open("http://localhost:5000")

    from app import app
    app.run(debug=False, port=5000)


def main():
    print()
    print("  Drexel Autopilot — Starting Up")
    print("  " + "=" * 35)
    print()

    check_deps()
    check_env()

    print()
    connected = test_login()

    if not connected:
        print()
        print("  Could not connect to Blackboard.")
        print("  The dashboard will start anyway — you can")
        print("  check the setup page for troubleshooting.")

    start_server()


if __name__ == "__main__":
    main()
