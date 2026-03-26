"""
Blackboard Data Client
======================
Uses Playwright to:
1. Log into Drexel Blackboard via Microsoft SSO
2. Fetch data from Blackboard REST API using the authenticated browser session
3. Cache results for fast serving by Flask

The browser session authenticates API calls naturally — no OAuth keys needed.
We use page.evaluate() with fetch() to call the REST API from within the
authenticated browser context, which bypasses the OAuth requirement.
"""

import asyncio
import json
import os
import time
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BB_URL = os.getenv("BB_BASE_URL", "https://learn.dcollege.net").rstrip("/")
BB_API_PATH = "/learn/api/public/v1"

# Persistence paths (Fly.io volume > local)
DATA_DIR = Path("/data")
LOCAL_DIR = Path(__file__).parent

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


class BlackboardClient:
    """Fetches and caches Blackboard data using Playwright browser sessions."""

    def __init__(self):
        self.user = None
        self.courses = []
        self.assignments = []
        self.grades = []
        self.last_refresh = 0
        self._lock = threading.Lock()
        self._refreshing = False
        self.status = {
            "connected": False,
            "error": None,
            "step": "",
            "last_refresh": None,
        }
        # Try loading cached data from disk on startup
        self._load_cache()

    # ── Paths ─────────────────────────────────────────

    @property
    def _cookies_path(self):
        if DATA_DIR.exists():
            return DATA_DIR / "sso_cookies.json"
        return LOCAL_DIR / ".sso_cookies.json"

    @property
    def _cache_path(self):
        if DATA_DIR.exists():
            return DATA_DIR / "bb_cache.json"
        return LOCAL_DIR / ".bb_cache.json"

    # ── Cache ─────────────────────────────────────────

    def _load_cache(self):
        """Load cached data from disk."""
        try:
            if not self._cache_path.exists():
                return False
            data = json.loads(self._cache_path.read_text())
            age = time.time() - data.get("timestamp", 0)
            if age > 7200:  # 2 hour disk cache
                print(f"  Disk cache expired ({int(age/60)}m old)")
                return False
            self.user = data.get("user")
            self.courses = data.get("courses", [])
            self.assignments = data.get("assignments", [])
            self.grades = data.get("grades", [])
            self.last_refresh = data.get("timestamp", 0)
            self.status["connected"] = bool(self.user)
            self.status["last_refresh"] = self.last_refresh
            print(f"  Loaded cached data ({int(age/60)}m old, {len(self.courses)} courses)")
            return True
        except Exception as e:
            print(f"  Cache load error: {e}")
            return False

    def _save_cache(self):
        """Save data to disk for persistence across restarts."""
        try:
            data = {
                "user": self.user,
                "courses": self.courses,
                "assignments": self.assignments,
                "grades": self.grades,
                "timestamp": self.last_refresh,
            }
            self._cache_path.write_text(json.dumps(data, indent=2))
            print(f"  Cache saved to {self._cache_path}")
        except Exception as e:
            print(f"  Cache save error: {e}")

    def _load_sso_cookies(self):
        """Load saved SSO cookies for MFA-free re-auth."""
        try:
            if self._cookies_path.exists():
                data = json.loads(self._cookies_path.read_text())
                cookies = data.get("cookies", [])
                if cookies:
                    age = time.time() - data.get("timestamp", 0)
                    print(f"  Loaded {len(cookies)} SSO cookies ({int(age/60)}m old)")
                    return cookies
        except Exception as e:
            print(f"  SSO cookies load error: {e}")
        return []

    def _save_sso_cookies(self, cookies):
        """Save ALL browser cookies for SSO re-auth."""
        try:
            data = {"cookies": cookies, "timestamp": time.time()}
            self._cookies_path.write_text(json.dumps(data))
            print(f"  Saved {len(cookies)} SSO cookies")
        except Exception as e:
            print(f"  SSO cookies save error: {e}")

    # ── Public API ────────────────────────────────────

    @property
    def is_stale(self):
        return time.time() - self.last_refresh > 3600

    def refresh_in_background(self):
        """Start a background refresh thread."""
        if self._refreshing:
            return False
        t = threading.Thread(target=self._refresh_sync, daemon=True)
        t.start()
        return True

    def refresh_and_wait(self, timeout=180):
        """Refresh and wait for completion. Returns True on success."""
        if self._refreshing:
            return False
        t = threading.Thread(target=self._refresh_sync, daemon=True)
        t.start()
        t.join(timeout=timeout)
        return self.status["connected"]

    # ── Core Refresh ──────────────────────────────────

    def _refresh_sync(self):
        """Synchronous wrapper for async refresh."""
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True

        try:
            asyncio.run(self._refresh())
        except Exception as e:
            self.status["error"] = str(e)
            self.status["step"] = f"Failed: {e}"
            print(f"  Refresh failed: {e}")
        finally:
            self._refreshing = False

    async def _refresh(self):
        """Full refresh: login + fetch all data via browser."""
        from playwright.async_api import async_playwright

        print()
        print("  ══════════════════════════════════════")
        print("  Starting Blackboard data refresh...")
        print("  ══════════════════════════════════════")
        self.status["step"] = "Launching browser..."
        self.status["error"] = None

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=CHROMIUM_ARGS)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
            )
            context.set_default_timeout(90000)

            # Restore SSO cookies for MFA-free re-auth
            sso_cookies = self._load_sso_cookies()
            if sso_cookies:
                try:
                    await context.add_cookies(sso_cookies)
                except Exception as e:
                    print(f"  Warning: could not restore SSO cookies: {e}")

            page = await context.new_page()

            try:
                # ── Step 1: Login ──
                self.status["step"] = "Logging into Drexel SSO..."
                await self._login(page)

                # Verify we ended up on Blackboard
                final_url = page.url.lower()
                if "learn.dcollege.net" not in final_url:
                    raise Exception(f"Login failed — ended up on {page.url[:120]} instead of Blackboard")

                print(f"  On Blackboard: {page.url[:80]}")

                # ── Step 2: Test API access via browser fetch ──
                self.status["step"] = "Testing API access..."
                user = await self._bb_api(page, "/users/me")
                if not user or isinstance(user, dict) and user.get("_error"):
                    err = user.get("_error", "unknown") if isinstance(user, dict) else "no response"
                    raise Exception(f"Blackboard API test failed: {err}")

                name = user.get("name", {})
                full_name = f"{name.get('given', '')} {name.get('family', '')}".strip()
                user_id = user.get("id", "")
                print(f"  API works! Logged in as: {full_name} ({user.get('userName', '')})")

                self.user = {
                    "name": full_name or user.get("userName", ""),
                    "username": user.get("userName", ""),
                    "id": user_id,
                }

                # ── Step 3: Fetch courses ──
                self.status["step"] = "Fetching courses..."
                self.courses = await self._fetch_courses(page, user_id)
                print(f"  Fetched {len(self.courses)} courses")

                # ── Step 4: Fetch upcoming assignments ──
                self.status["step"] = "Fetching assignments..."
                self.assignments = await self._fetch_assignments(page, user_id)
                print(f"  Fetched {len(self.assignments)} upcoming assignments")

                # ── Step 5: Fetch grades ──
                self.status["step"] = "Fetching grades..."
                self.grades = await self._fetch_grades(page, user_id)
                print(f"  Fetched {len(self.grades)} grades")

                # ── Step 6: Save everything ──
                self.last_refresh = time.time()
                self.status["connected"] = True
                self.status["error"] = None
                self.status["step"] = "Connected!"
                self.status["last_refresh"] = self.last_refresh

                self._save_cache()

                # Save ALL cookies for SSO re-auth next time
                all_cookies = await context.cookies()
                self._save_sso_cookies(all_cookies)

                print()
                print("  ══════════════════════════════════════")
                print(f"  Refresh complete! {len(self.courses)} courses, "
                      f"{len(self.assignments)} assignments, {len(self.grades)} grades")
                print("  ══════════════════════════════════════")
                print()

            finally:
                await browser.close()

    # ── Browser API Calls ─────────────────────────────

    async def _bb_api(self, page, path, params=None):
        """Call Blackboard REST API from within the authenticated browser session."""
        url = f"{BB_API_PATH}{path}"
        if params:
            url += "?" + urlencode(params)

        try:
            result = await page.evaluate("""
                async (url) => {
                    try {
                        const resp = await fetch(url, { credentials: 'same-origin' });
                        if (!resp.ok) {
                            const text = await resp.text().catch(() => '');
                            return { _error: resp.status, _text: text.substring(0, 200) };
                        }
                        return await resp.json();
                    } catch(e) {
                        return { _error: e.message };
                    }
                }
            """, url)

            if isinstance(result, dict) and "_error" in result:
                print(f"  BB API {path} => ERROR {result['_error']}")
                if result.get("_text"):
                    print(f"    {result['_text'][:150]}")
                return None
            return result
        except Exception as e:
            print(f"  BB API {path} => EXCEPTION: {e}")
            return None

    # ── Login Flow ────────────────────────────────────

    async def _login(self, page):
        """Handle the full Drexel SSO login flow."""
        username = os.getenv("DREXEL_USERNAME", "")
        password = os.getenv("DREXEL_PASSWORD", "")

        if not username or not password:
            raise Exception("DREXEL_USERNAME and DREXEL_PASSWORD must be set")

        # Navigate to Blackboard — triggers SAML SSO redirect
        bb_entry = f"{BB_URL}/ultra/institution-page"
        print(f"  Navigating to {bb_entry}...")
        await page.goto(bb_entry, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(3000)

        url = page.url.lower()
        print(f"  Landed on: {page.url[:120]}")

        # Already authenticated? (SSO cookies worked)
        if "learn.dcollege.net" in url and "login" not in url and "saml" not in url:
            print("  Already authenticated via saved SSO cookies!")
            return

        # ── Handle Drexel Connect portal ──
        if "connect.drexel.edu" in url:
            print("  On Drexel Connect, clicking Sign In...")
            try:
                sign_in = page.locator('a:has-text("Sign In"), a:has-text("SIGN IN"), button:has-text("Sign In")')
                await sign_in.first.click(timeout=10000)
                await page.wait_for_timeout(5000)
                url = page.url.lower()
                print(f"  After Sign In click: {url[:120]}")
            except Exception as e:
                print(f"  No Sign In button: {e}")

        # ── Handle Microsoft SSO ──
        url = page.url.lower()
        if "microsoftonline" in url or "login.microsoft" in url or "login.live" in url:
            print("  Microsoft SSO detected...")

            # Check for account picker (saved account)
            try:
                acct = page.locator(f'small:has-text("{username}")')
                if await acct.first.is_visible(timeout=3000):
                    await acct.first.click()
                    print(f"  Clicked saved account: {username}")
                    await page.wait_for_timeout(5000)
                    url = page.url.lower()
                    if "learn.dcollege.net" in url:
                        print("  Re-authenticated without password!")
                        return
            except Exception:
                pass

            # Enter email
            url = page.url.lower()
            if "microsoftonline" in url or "login.microsoft" in url:
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
                    print(f"  Email step issue: {e}")

                # Enter password
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
                    print(f"  Password step issue: {e}")

            # ── Check for MFA ──
            page_url = page.url.lower()
            page_content = await page.content()

            needs_mfa = "microsoftonline" in page_url or "login.microsoft" in page_url
            if not needs_mfa:
                mfa_keywords = ["verify your identity", "authenticator", "approve",
                                "additional security", "strongauth", "push notification"]
                needs_mfa = any(w in page_content.lower() for w in mfa_keywords)

            if needs_mfa:
                print()
                print("  ****************************************************")
                print("  *  MFA REQUIRED — Check your phone / Authenticator  *")
                print("  *  Approve the sign-in request, then wait...        *")
                print("  ****************************************************")
                print()
                self.status["step"] = "Waiting for MFA approval..."

                for i in range(120):
                    await page.wait_for_timeout(1000)
                    current_url = page.url.lower()
                    if "microsoftonline" not in current_url and "login.microsoft" not in current_url:
                        print(f"  MFA approved! Redirected away from Microsoft.")
                        break
                    try:
                        kmsi = page.locator('#KmsiDescription, input[value="Yes"], input[value="No"]')
                        if await kmsi.first.is_visible():
                            print("  MFA approved! Stay-signed-in prompt detected.")
                            break
                    except Exception:
                        pass
                    if i % 10 == 0 and i > 0:
                        print(f"  Waiting for MFA... ({i}s)")
                else:
                    raise Exception("MFA timed out after 120 seconds")

            # ── "Stay signed in?" — click Yes ──
            try:
                yes_btn = page.locator('input[value="Yes"], input#idSIButton9')
                await yes_btn.first.click(timeout=5000)
                print("  Clicked 'Yes' on stay signed in")
                await page.wait_for_timeout(5000)
            except Exception:
                pass

        # ── Handle Drexel Connect portal after SSO ──
        url = page.url.lower()
        print(f"  After SSO: {page.url[:120]}")

        if "connect.drexel.edu" in url:
            print("  Back on Drexel Connect, clicking Drexel Learn...")
            try:
                # Try clicking "Drexel Learn" link
                learn_link = page.locator('a:has-text("Drexel Learn"), a:has-text("Learn"), a[href*="learn.dcollege"]')
                await learn_link.first.click(timeout=10000)
                await page.wait_for_timeout(5000)
                print(f"  Navigated to: {page.url[:120]}")
            except Exception:
                # Direct navigation fallback
                print("  No Learn link found, navigating directly...")
                await page.goto(bb_entry, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(5000)

        # ── Final check: are we on Blackboard? ──
        if "learn.dcollege.net" not in page.url.lower():
            print(f"  Not on Blackboard yet ({page.url[:80]}), trying direct nav...")
            await page.goto(BB_URL, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)

        if "learn.dcollege.net" not in page.url.lower():
            raise Exception(f"Could not reach Blackboard. Ended up on: {page.url[:120]}")

        print(f"  Login complete! On: {page.url[:80]}")

    # ── Data Fetching ─────────────────────────────────

    async def _fetch_courses(self, page, user_id):
        """Fetch all active courses with grades."""
        memberships = await self._bb_api(page, f"/users/{user_id}/courses", {"limit": 100})
        if not memberships:
            print("  No course memberships found")
            return []

        results = memberships.get("results", [])
        print(f"  Found {len(results)} course memberships")
        courses = []

        for m in results:
            course_id = m.get("courseId", "")
            if not course_id:
                continue

            course = await self._bb_api(page, f"/courses/{course_id}")
            if not course:
                continue
            if course.get("availability", {}).get("available") == "No":
                continue

            # Get final grade
            grade_info = {}
            columns = await self._bb_api(page, f"/courses/{course_id}/gradebook/columns", {"limit": 100})
            if columns and columns.get("results"):
                for col in columns["results"]:
                    if col.get("name", "").lower() in ("final grade", "total", "weighted total", "final"):
                        gd = await self._bb_api(page, f"/courses/{course_id}/gradebook/columns/{col['id']}/users/{user_id}")
                        if gd:
                            grade_info = {
                                "letter": gd.get("displayGrade", {}).get("text", ""),
                                "score": gd.get("score"),
                            }
                        break

            courses.append({
                "id": course_id,
                "name": course.get("name", ""),
                "code": course.get("courseId", ""),
                "term": course.get("term", {}).get("name", "") if course.get("term") else "",
                "description": course.get("description", ""),
                "grade": grade_info,
            })

        return courses

    async def _fetch_assignments(self, page, user_id):
        """Fetch upcoming assignments across all courses."""
        now = datetime.utcnow().isoformat() + "Z"
        upcoming = []

        for course in self.courses:
            course_id = course["id"]
            columns = await self._bb_api(page, f"/courses/{course_id}/gradebook/columns", {"limit": 100})
            if not columns:
                continue

            for col in columns.get("results", []):
                due = col.get("due", "")
                col_name = col.get("name", "")
                if not due or due <= now:
                    continue
                if col_name.lower() in ("final grade", "total", "weighted total", "final"):
                    continue

                submitted = False
                gd = await self._bb_api(page, f"/courses/{course_id}/gradebook/columns/{col['id']}/users/{user_id}")
                if gd:
                    submitted = gd.get("status") == "Graded" or gd.get("score") is not None

                upcoming.append({
                    "id": col.get("id", ""),
                    "name": col_name,
                    "course": course["name"],
                    "course_code": course["code"],
                    "course_id": course_id,
                    "due": due,
                    "points": col.get("score", {}).get("possible"),
                    "submitted": submitted,
                    "url": f"{BB_URL}/ultra/courses/{course_id}/cl/outline",
                })

        upcoming.sort(key=lambda x: x.get("due", ""))
        return upcoming

    async def _fetch_grades(self, page, user_id):
        """Fetch final grades for all courses."""
        grades = []

        for course in self.courses:
            course_id = course["id"]
            columns = await self._bb_api(page, f"/courses/{course_id}/gradebook/columns", {"limit": 100})
            if not columns:
                continue

            for col in columns.get("results", []):
                if col.get("name", "").lower() not in ("final grade", "total", "weighted total", "final"):
                    continue

                gd = await self._bb_api(page, f"/courses/{course_id}/gradebook/columns/{col['id']}/users/{user_id}")
                if gd and (gd.get("score") is not None or gd.get("displayGrade")):
                    grades.append({
                        "course": course["name"],
                        "code": course["code"],
                        "course_id": course_id,
                        "letter": gd.get("displayGrade", {}).get("text", ""),
                        "score": gd.get("score"),
                    })
                break

        return grades
