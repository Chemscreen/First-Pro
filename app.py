"""
SchuBase Auto Pilot — Backend
==============================
Flask server that bridges Blackboard Ultra APIs
and serves the dashboard frontend.

Auto-logs into Blackboard via Playwright when needed.
Protected by password authentication.
"""

import os
import json
import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, jsonify, send_from_directory, request, redirect, session, make_response
import requests as http

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

# Access password — set this in your environment variables
ACCESS_PASSWORD = os.getenv("SCHUBASE_PASSWORD", "")

# ── Auth ─────────────────────────────────────────────────────────

def require_auth(f):
    """Decorator to require authentication for routes."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not ACCESS_PASSWORD:
            return f(*args, **kwargs)
        if session.get("authenticated"):
            return f(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"error": "Unauthorized"}), 401
        return redirect("/login")
    return decorated


@app.route("/login")
def login_page():
    if session.get("authenticated") or not ACCESS_PASSWORD:
        return redirect("/")
    return send_from_directory("static", "login.html")


@app.route("/auth/login", methods=["POST"])
def auth_login():
    password = request.form.get("password", "")
    if password == ACCESS_PASSWORD:
        session["authenticated"] = True
        session.permanent = True
        app.permanent_session_lifetime = timedelta(days=30)
        return redirect("/")
    return redirect("/login?error=1")


@app.route("/auth/logout")
def auth_logout():
    session.clear()
    return redirect("/login")


# ── Blackboard Session ────────────────────────────────────────────

BB_URL = os.getenv("BB_BASE_URL", "https://learn.dcollege.net").rstrip("/")
BB_API = f"{BB_URL}/learn/api/public/v1"

_bb_session = None
_bb_lock = threading.Lock()
_bb_validated = False  # Track if session has been validated


def get_bb_session() -> http.Session:
    """Get or create a Blackboard requests session with valid cookies."""
    global _bb_session, _bb_validated

    with _bb_lock:
        if _bb_session and _bb_validated:
            return _bb_session

        from drexel_login import load_session, get_cookie_string, test_session

        # Try saved session first
        saved = load_session()
        if saved:
            cookie_str = saved["cookie_string"]
            session_obj = http.Session()
            session_obj.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
            for pair in cookie_str.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    name, value = pair.split("=", 1)
                    session_obj.cookies.set(name.strip(), value.strip())

            # Validate the session
            print("  Validating saved Blackboard session...")
            try:
                resp = session_obj.get(f"{BB_API}/users/me", timeout=15)
                print(f"  Validation: {resp.status_code} {resp.reason}")
                if resp.ok:
                    try:
                        user = resp.json()
                        name = user.get("name", {})
                        print(f"  Connected as: {name.get('given', '')} {name.get('family', '')} ({user.get('userName', '')})")
                    except Exception:
                        pass
                    _bb_session = session_obj
                    _bb_validated = True
                    return session_obj
                else:
                    print(f"  Saved session INVALID: {resp.text[:200]}")
            except Exception as e:
                print(f"  Validation error: {e}")

        # Need fresh login
        print("  Logging into Blackboard (fresh login)...")
        try:
            cookie_str = get_cookie_string()
            if not cookie_str:
                print("  ERROR: get_cookie_string returned empty!")
                raise Exception("Login returned empty cookies")

            # Validate the fresh session
            print("  Validating fresh session...")
            if test_session(cookie_str):
                session_obj = http.Session()
                session_obj.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
                for pair in cookie_str.split(";"):
                    pair = pair.strip()
                    if "=" in pair:
                        name, value = pair.split("=", 1)
                        session_obj.cookies.set(name.strip(), value.strip())
                _bb_session = session_obj
                _bb_validated = True
                print("  Fresh session validated and cached!")
                return session_obj
            else:
                print("  ERROR: Fresh login got cookies but they don't work with the API!")
                print("  This likely means Blackboard requires different authentication for REST API access.")
                raise Exception("Cookies captured but Blackboard API rejected them")
        except Exception as e:
            print(f"  Login failed: {e}")
            raise


def bb_get(path, params=None):
    """Make a GET request to Blackboard API."""
    global _bb_session, _bb_validated
    try:
        session_obj = get_bb_session()
        resp = session_obj.get(f"{BB_API}{path}", params=params or {}, timeout=15)
        print(f"  BB API {path} => {resp.status_code}")

        if resp.status_code == 401:
            print("  Session expired, forcing re-login...")
            _bb_session = None
            _bb_validated = False
            session_obj = get_bb_session()
            resp = session_obj.get(f"{BB_API}{path}", params=params or {}, timeout=15)
            print(f"  BB API {path} (retry) => {resp.status_code}")

        if resp.ok:
            data = resp.json()
            if isinstance(data, dict) and "results" in data:
                print(f"  BB API {path} => {len(data['results'])} results")
            return data
        else:
            print(f"  BB API {path} FAILED: {resp.text[:300]}")
    except Exception as e:
        print(f"  BB API error ({path}): {e}")
    return None


# ── API Routes ────────────────────────────────────────────────────

@app.route("/api/status")
@require_auth
def api_status():
    try:
        user = bb_get("/users/me")
        if user:
            name_data = user.get("name", {})
            full_name = f"{name_data.get('given', '')} {name_data.get('family', '')}".strip()
            return jsonify({
                "connected": True,
                "platform": "blackboard",
                "user": {
                    "name": full_name or user.get("userName", ""),
                    "avatar": user.get("avatar", {}).get("viewUrl", ""),
                    "username": user.get("userName", ""),
                },
            })
    except Exception as e:
        print(f"  /api/status error: {e}")
    return jsonify({"connected": False, "error": "Not connected to Blackboard"})


@app.route("/api/courses")
@require_auth
def api_courses():
    user = bb_get("/users/me")
    if not user:
        print("  /api/courses: user lookup failed")
        return jsonify([])

    user_id = user.get("id", "")
    print(f"  /api/courses: user_id={user_id}")
    memberships = bb_get(f"/users/{user_id}/courses", {"limit": 100})
    if not memberships:
        print("  /api/courses: memberships lookup failed")
        return jsonify([])

    results_list = memberships.get("results", [])
    print(f"  /api/courses: {len(results_list)} memberships found")
    courses = []

    for m in results_list:
        course_id = m.get("courseId", "")
        if not course_id:
            continue

        course = bb_get(f"/courses/{course_id}")
        if not course:
            continue

        if course.get("availability", {}).get("available") == "No":
            continue

        grade_info = {}
        columns = bb_get(f"/courses/{course_id}/gradebook/columns", {
            "limit": 100,
        })
        if columns and columns.get("results"):
            for col in columns["results"]:
                if col.get("name", "").lower() in ("final grade", "total", "weighted total", "final"):
                    grade_data = bb_get(f"/courses/{course_id}/gradebook/columns/{col['id']}/users/{user_id}")
                    if grade_data:
                        grade_info = {
                            "letter": grade_data.get("displayGrade", {}).get("text", ""),
                            "score": grade_data.get("score"),
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

    return jsonify(courses)


@app.route("/api/courses/<course_id>")
@require_auth
def api_course_detail(course_id):
    course = bb_get(f"/courses/{course_id}")
    if not course:
        return jsonify({"error": "Not found"}), 404

    user = bb_get("/users/me")
    user_id = user.get("id", "") if user else ""

    contents = bb_get(f"/courses/{course_id}/contents", {"limit": 200})
    content_items = contents.get("results", []) if contents else []

    columns = bb_get(f"/courses/{course_id}/gradebook/columns", {"limit": 100})
    grade_columns = columns.get("results", []) if columns else []

    assignments = []
    grade_info = {}

    for col in grade_columns:
        col_name = col.get("name", "")

        if col_name.lower() in ("final grade", "total", "weighted total", "final"):
            if user_id:
                gd = bb_get(f"/courses/{course_id}/gradebook/columns/{col['id']}/users/{user_id}")
                if gd:
                    grade_info = {
                        "letter": gd.get("displayGrade", {}).get("text", ""),
                        "score": gd.get("score"),
                    }
            continue

        due = col.get("due", "")
        submitted = False
        score = None
        if user_id:
            gd = bb_get(f"/courses/{course_id}/gradebook/columns/{col['id']}/users/{user_id}")
            if gd:
                score = gd.get("score")
                submitted = gd.get("status") == "Graded" or score is not None

        assignments.append({
            "id": col.get("id", ""),
            "name": col_name,
            "due": due,
            "points": col.get("score", {}).get("possible"),
            "submitted": submitted,
            "score": score,
            "description": col.get("description", "")[:500] if col.get("description") else "",
            "url": f"{BB_URL}/ultra/courses/{course_id}/cl/outline",
        })

    modules = []
    for item in content_items:
        if item.get("hasChildren"):
            children = bb_get(f"/courses/{course_id}/contents/{item['id']}/children", {"limit": 50})
            child_items = children.get("results", []) if children else []
            modules.append({
                "name": item.get("title", ""),
                "items": [
                    {"title": c.get("title", ""), "type": c.get("contentHandler", {}).get("id", "").split("/")[-1]}
                    for c in child_items
                ],
            })

    announcements_data = bb_get(f"/courses/{course_id}/announcements", {"limit": 5})
    announcements = []
    if announcements_data and announcements_data.get("results"):
        for a in announcements_data["results"]:
            announcements.append({
                "title": a.get("title", ""),
                "date": a.get("created", ""),
                "message": (a.get("body", "") or "")[:400],
            })

    return jsonify({
        "id": course_id,
        "name": course.get("name", ""),
        "code": course.get("courseId", ""),
        "term": course.get("term", {}).get("name", "") if course.get("term") else "",
        "grade": grade_info,
        "assignments": assignments,
        "modules": modules,
        "announcements": announcements,
    })


@app.route("/api/assignments/upcoming")
@require_auth
def api_upcoming():
    user = bb_get("/users/me")
    if not user:
        return jsonify([])

    user_id = user.get("id", "")
    memberships = bb_get(f"/users/{user_id}/courses", {"limit": 100})
    if not memberships:
        return jsonify([])

    now = datetime.utcnow().isoformat() + "Z"
    upcoming = []

    for m in memberships.get("results", []):
        course_id = m.get("courseId", "")
        if not course_id:
            continue

        course = bb_get(f"/courses/{course_id}")
        if not course or course.get("availability", {}).get("available") == "No":
            continue

        course_name = course.get("name", "")
        course_code = course.get("courseId", "")

        columns = bb_get(f"/courses/{course_id}/gradebook/columns", {"limit": 100})
        if not columns:
            continue

        for col in columns.get("results", []):
            due = col.get("due", "")
            if not due or due <= now:
                continue

            col_name = col.get("name", "")
            if col_name.lower() in ("final grade", "total", "weighted total", "final"):
                continue

            submitted = False
            if user_id:
                gd = bb_get(f"/courses/{course_id}/gradebook/columns/{col['id']}/users/{user_id}")
                if gd:
                    submitted = gd.get("status") == "Graded" or gd.get("score") is not None

            upcoming.append({
                "id": col.get("id", ""),
                "name": col_name,
                "course": course_name,
                "course_code": course_code,
                "course_id": course_id,
                "due": due,
                "points": col.get("score", {}).get("possible"),
                "submitted": submitted,
                "url": f"{BB_URL}/ultra/courses/{course_id}/cl/outline",
            })

    upcoming.sort(key=lambda x: x.get("due", ""))
    return jsonify(upcoming)


@app.route("/api/grades")
@require_auth
def api_grades():
    user = bb_get("/users/me")
    if not user:
        return jsonify([])

    user_id = user.get("id", "")
    memberships = bb_get(f"/users/{user_id}/courses", {"limit": 100})
    if not memberships:
        return jsonify([])

    grades = []

    for m in memberships.get("results", []):
        course_id = m.get("courseId", "")
        if not course_id:
            continue

        course = bb_get(f"/courses/{course_id}")
        if not course or course.get("availability", {}).get("available") == "No":
            continue

        columns = bb_get(f"/courses/{course_id}/gradebook/columns", {"limit": 100})
        if not columns:
            continue

        for col in columns.get("results", []):
            if col.get("name", "").lower() not in ("final grade", "total", "weighted total", "final"):
                continue

            gd = bb_get(f"/courses/{course_id}/gradebook/columns/{col['id']}/users/{user_id}")
            if gd and (gd.get("score") is not None or gd.get("displayGrade")):
                grades.append({
                    "course": course.get("name", ""),
                    "code": course.get("courseId", ""),
                    "course_id": course_id,
                    "letter": gd.get("displayGrade", {}).get("text", ""),
                    "score": gd.get("score"),
                })
            break

    return jsonify(grades)


_login_status = {"running": False, "success": None, "error": None, "step": ""}


@app.route("/api/login", methods=["POST"])
@require_auth
def api_bb_login():
    """Trigger Blackboard login in background thread."""
    global _bb_session, _bb_validated

    if _login_status["running"]:
        return jsonify({"success": False, "error": "Login already in progress", "status": "running"})

    _bb_session = None
    _bb_validated = False
    _login_status["running"] = True
    _login_status["success"] = None
    _login_status["error"] = None
    _login_status["step"] = "Starting login..."

    def do_login():
        global _bb_session, _bb_validated
        try:
            from drexel_login import get_cookie_string, test_session

            _login_status["step"] = "Running Playwright login (SSO + MFA)..."
            cookie_str = get_cookie_string()
            if not cookie_str:
                raise Exception("Login returned empty cookies")

            _login_status["step"] = "Validating session with Blackboard API..."
            if not test_session(cookie_str):
                raise Exception("Login got cookies but Blackboard API rejected them — session may be invalid")

            # Pre-populate the session
            session_obj = http.Session()
            session_obj.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
            for pair in cookie_str.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    name, value = pair.split("=", 1)
                    session_obj.cookies.set(name.strip(), value.strip())
            _bb_session = session_obj
            _bb_validated = True
            _login_status["success"] = True
            _login_status["error"] = None
            _login_status["step"] = "Connected!"
            print("  Background login completed and validated!")
        except Exception as e:
            _login_status["success"] = False
            _login_status["error"] = str(e)
            _login_status["step"] = f"Failed: {e}"
            print(f"  Background login failed: {e}")
        finally:
            _login_status["running"] = False

    t = threading.Thread(target=do_login, daemon=True)
    t.start()

    # Wait up to 180s for it to finish (MFA can take time)
    t.join(timeout=180)

    if _login_status["running"]:
        return jsonify({"success": False, "error": "Login timed out — MFA may not have been approved", "status": "timeout"})

    if _login_status["success"]:
        return jsonify({"success": True})
    return jsonify({"success": False, "error": _login_status["error"] or "Unknown error"}), 500


@app.route("/api/login/status")
@require_auth
def api_login_status():
    """Check login progress."""
    return jsonify(_login_status)


@app.route("/api/debug")
@require_auth
def api_debug():
    """Debug endpoint showing connection diagnostics."""
    from drexel_login import load_session, get_session_path
    import time as _time

    info = {
        "bb_session_cached": _bb_session is not None,
        "bb_session_validated": _bb_validated,
        "session_file": str(get_session_path()),
        "login_status": _login_status,
    }

    saved = load_session()
    if saved:
        age = _time.time() - saved.get("timestamp", 0)
        info["saved_session"] = {
            "exists": True,
            "age_minutes": round(age / 60, 1),
            "cookie_count": len(saved.get("cookies", {})),
            "cookie_names": list(saved.get("cookies", {}).keys()),
            "has_sso_cookies": len(saved.get("all_cookies", [])) > 0,
            "sso_cookie_count": len(saved.get("all_cookies", [])),
        }
    else:
        info["saved_session"] = {"exists": False}

    # Quick API test
    if _bb_session:
        try:
            resp = _bb_session.get(f"{BB_API}/users/me", timeout=10)
            info["api_test"] = {
                "status": resp.status_code,
                "ok": resp.ok,
                "body_preview": resp.text[:200] if not resp.ok else "OK",
            }
            if resp.ok:
                user = resp.json()
                info["api_test"]["user"] = user.get("userName", "")
        except Exception as e:
            info["api_test"] = {"error": str(e)}

    return jsonify(info)


# ── Frontend Serving ──────────────────────────────────────────────

@app.route("/")
@require_auth
def index():
    return send_from_directory("static", "index.html")


@app.route("/<path:path>")
def static_files(path):
    if path.endswith((".css", ".js", ".woff2", ".woff", ".ttf", ".png", ".ico", ".svg")):
        resp = make_response(send_from_directory("static", path))
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp
    if path == "login" or path == "login.html":
        return send_from_directory("static", "login.html")
    @require_auth
    def serve():
        return send_from_directory("static", path)
    return serve()


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print()
    print("  SchuBase Auto Pilot")
    print("  " + "=" * 25)
    print(f"  http://localhost:{port}")
    print()

    if ACCESS_PASSWORD:
        print(f"  Auth: password protected")
    else:
        print("  Auth: OPEN (set SCHUBASE_PASSWORD to enable)")

    username = os.getenv("DREXEL_USERNAME", "")
    if username:
        print(f"  Drexel user: {username}")
    print()

    app.run(debug=False, host="0.0.0.0", port=port)
