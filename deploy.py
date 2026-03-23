#!/usr/bin/env python3
"""
SchuBase Auto Pilot — One-Click Cloud Deploy
=============================================
Automates deployment to Render.com using browser-use CLI.

Usage:
  pip install browser-use
  browser-use install
  python deploy.py

What it does:
  1. Opens Render.com in a real browser (uses your existing login)
  2. Creates a new Web Service from your GitHub repo
  3. Configures Docker, env vars, and free plan
  4. Deploys → gives you https://schubase.onrender.com
"""

import subprocess
import sys
import os
import time
import json

# ── Config ─────────────────────────────────────────
REPO_URL = "https://github.com/ByteSizeData/-First-Pro"
SERVICE_NAME = "schubase"
BRANCH = "claude/auto-research-blackboard-e38bX"

ENV_VARS = {
    "DREXEL_USERNAME": os.getenv("DREXEL_USERNAME", ""),
    "DREXEL_PASSWORD": os.getenv("DREXEL_PASSWORD", ""),
    "GOOGLE_API_KEY": os.getenv("GOOGLE_API_KEY", ""),
    "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY", ""),
    "SCHUBASE_PASSWORD": os.getenv("SCHUBASE_PASSWORD", "schubase2024"),
}


def run(cmd):
    """Run a browser-use command and return output."""
    result = subprocess.run(
        f"browser-use --profile Default {cmd}",
        shell=True, capture_output=True, text=True, timeout=30
    )
    return result.stdout.strip()


def run_quiet(cmd):
    subprocess.run(
        f"browser-use --profile Default {cmd}",
        shell=True, capture_output=True, text=True, timeout=30
    )


def wait(seconds=2):
    time.sleep(seconds)


def screenshot(name="deploy"):
    run(f"screenshot /tmp/{name}.png")
    print(f"  Screenshot saved: /tmp/{name}.png")


def main():
    print()
    print("  ╔═══════════════════════════════════╗")
    print("  ║   SchuBase — Cloud Deploy Script  ║")
    print("  ╚═══════════════════════════════════╝")
    print()

    # Collect env vars if not set
    for key in ENV_VARS:
        if not ENV_VARS[key]:
            ENV_VARS[key] = input(f"  Enter {key}: ").strip()

    print()
    print("  Step 1: Opening Render.com dashboard...")
    run("open https://dashboard.render.com")
    wait(3)

    # Check if logged in
    state = run("state")
    if "Sign In" in state or "Log In" in state or "sign-in" in state:
        print("  You need to log into Render.com first.")
        print("  Please sign in with GitHub in the browser window...")
        input("  Press Enter when you're logged in: ")
        wait(2)

    print("  Step 2: Creating new Web Service...")
    run("open https://dashboard.render.com/select-repo?type=web")
    wait(3)

    # Look for the repo or "Public Git repository" option
    state = run("state")
    screenshot("step2")

    if "Public Git repository" in state or "public" in state.lower():
        # Use public repo URL
        print("  Entering repo URL...")
        # Find the public git repo input
        run(f'eval "document.querySelector(\'input[placeholder*=\\\"repository\\\"]\').focus()"')
        wait(1)
        run(f'type "{REPO_URL}"')
        wait(1)
        run('keys "Enter"')
        wait(3)
    else:
        # Try to find and click the repo in the list
        print("  Looking for your repo in connected repos...")
        # Search for the repo
        run(f'type "First-Pro"')
        wait(2)
        state = run("state")
        # Click the Connect button next to the repo
        run('eval "document.querySelector(\'button[class*=\\\"connect\\\"]\')?.click()"')
        wait(3)

    screenshot("step3")
    print("  Step 3: Configuring service settings...")

    # Set service name
    run(f'eval "const nameInput = document.querySelector(\'input[name=\\\"name\\\"]\'); if(nameInput) {{ nameInput.value = \\\"\\\"; nameInput.focus(); }}"')
    wait(1)
    run('keys "Control+a"')
    run(f'type "{SERVICE_NAME}"')
    wait(1)

    # Set branch
    state = run("state")
    if "Branch" in state:
        run(f'eval "const branchInput = document.querySelector(\'input[name=\\\"branch\\\"]\'); if(branchInput) {{ branchInput.value = \\\"\\\"; branchInput.focus(); }}"')
        wait(1)
        run('keys "Control+a"')
        run(f'type "{BRANCH}"')
        wait(1)

    # Select Docker runtime
    run('eval "document.querySelectorAll(\'button, div\').forEach(el => {{ if(el.textContent.includes(\'Docker\')) el.click(); }})"')
    wait(1)

    # Select Free plan
    run('eval "document.querySelectorAll(\'button, div, label\').forEach(el => {{ if(el.textContent.trim() === \'Free\') el.click(); }})"')
    wait(1)

    screenshot("step4")
    print("  Step 4: Adding environment variables...")

    # Try to find "Add Environment Variable" button
    for key, value in ENV_VARS.items():
        if not value:
            continue
        run('eval "document.querySelectorAll(\'button\').forEach(b => {{ if(b.textContent.includes(\'Add Environment Variable\')) b.click(); }})"')
        wait(1)
        # Fill key
        run('eval "const inputs = document.querySelectorAll(\'input[placeholder*=\\\"key\\\"]\'); inputs[inputs.length-1]?.focus()"')
        run(f'type "{key}"')
        wait(0.5)
        # Fill value
        run('keys "Tab"')
        run(f'type "{value}"')
        wait(0.5)

    screenshot("step5")
    print("  Step 5: Deploying...")

    # Click "Create Web Service" button
    run('eval "document.querySelectorAll(\'button\').forEach(b => {{ if(b.textContent.includes(\'Create Web Service\') || b.textContent.includes(\'Deploy\')) b.click(); }})"')
    wait(5)

    screenshot("deployed")

    print()
    print("  ╔═══════════════════════════════════════════╗")
    print("  ║   Deployment started!                     ║")
    print(f"  ║   URL: https://{SERVICE_NAME}.onrender.com       ║")
    print("  ║                                           ║")
    print("  ║   It takes ~5 min for the first build.    ║")
    print("  ║   Check the Render dashboard for status.  ║")
    print("  ╚═══════════════════════════════════════════╝")
    print()


if __name__ == "__main__":
    main()
