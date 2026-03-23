"""
Drexel Autopilot — Backend
===========================
Flask server that bridges Blackboard Ultra APIs
and serves the dashboard frontend.

Auto-logs into Blackboard via Playwright when needed.
"""

import os
import json
import threading
from datetime import datetime, timedelta

from flask import Flask, jsonify, send_from_directory, request
import requests as http

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder="static")

# ── Blackboard Session ────────────────────────────────────────────

BB_URL = os.getenv("BB_BASE_URL", "https://learn.dcollege.net").rstrip("/")
BB_API = f"{BB_URL}/learn/api/public/v1"

_bb_session = None
_bb_lock = threading.Lock()


def get_bb_session() -> http.Session:
    """Get or create a Blackboard requests session with valid cookies."""
    global _bb_session

    with _bb_lock:
        if _bb_session:
            return _bb_session

        session = http.Session()
        session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"

        # Try saved session first
        from drexel_login import load_session, get_cookie_string, test_session

        saved = load_session()
        if saved:
            cookie_str = saved["cookie_string"]
            for pair in cookie_str.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    name, value = pair.split("=", 1)
                    session.cookies.set(name.strip(), value.strip())

            # Quick validity check
            resp = session.get(f"{BB_API}/users/me", timeout=10)
            if resp.ok:
                _bb_session = session
                return session

        # Need fresh login
        print("  Logging into Blackboard...")
        cookie_str = get_cookie_string()
        session = http.Session()
        session.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                name, value = pair.split("=", 1)
                session.cookies.set(name.strip(), value.strip())

        _bb_session = session
        return session


def bb_get(path, params=None):
    """Make a GET request to Blackboard API."""
    try:
        session = get_bb_session()
        resp = session.get(f"{BB_API}{path}", params=params or {}, timeout=15)
        if resp.status_code == 401:
            # Session expired, force re-login
            global _bb_session
            _bb_session = None
            session = get_bb_session()
            resp = session.get(f"{BB_API}{path}", params=params or {}, timeout=15)
        if resp.ok:
            return resp.json()
    except Exception as e:
        print(f"  BB API error ({path}): {e}")
    return None


# ── API Routes ────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
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
    return jsonify({"connected": False, "error": "Not connected to Blackboard"})


@app.route("/api/courses")
def api_courses():
    # Get user's course memberships
    user = bb_get("/users/me")
    if not user:
        return jsonify([])

    user_id = user.get("id", "")
    memberships = bb_get(f"/users/{user_id}/courses", {"limit": 100})
    if not memberships:
        return jsonify([])

    results_list = memberships.get("results", [])
    courses = []

    for m in results_list:
        course_id = m.get("courseId", "")
        if not course_id:
            continue

        # Get course details
        course = bb_get(f"/courses/{course_id}")
        if not course:
            continue

        # Skip unavailable courses
        if course.get("availability", {}).get("available") == "No":
            continue

        # Get grade columns for this course
        grade_info = {}
        columns = bb_get(f"/courses/{course_id}/gradebook/columns", {
            "limit": 100,
        })
        if columns and columns.get("results"):
            # Look for the final/total grade column
            for col in columns["results"]:
                if col.get("name", "").lower() in ("final grade", "total", "weighted total", "final"):
                    # Get the user's grade for this column
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
def api_course_detail(course_id):
    course = bb_get(f"/courses/{course_id}")
    if not course:
        return jsonify({"error": "Not found"}), 404

    user = bb_get("/users/me")
    user_id = user.get("id", "") if user else ""

    # Contents (files, links, assignments)
    contents = bb_get(f"/courses/{course_id}/contents", {"limit": 200})
    content_items = contents.get("results", []) if contents else []

    # Grade columns (assignments)
    columns = bb_get(f"/courses/{course_id}/gradebook/columns", {"limit": 100})
    grade_columns = columns.get("results", []) if columns else []

    assignments = []
    grade_info = {}

    for col in grade_columns:
        col_name = col.get("name", "")

        # Check for final grade
        if col_name.lower() in ("final grade", "total", "weighted total", "final"):
            if user_id:
                gd = bb_get(f"/courses/{course_id}/gradebook/columns/{col['id']}/users/{user_id}")
                if gd:
                    grade_info = {
                        "letter": gd.get("displayGrade", {}).get("text", ""),
                        "score": gd.get("score"),
                    }
            continue

        # Regular assignment column
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

    # Build modules from content tree
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

    # Announcements
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


@app.route("/api/login", methods=["POST"])
def api_login():
    """Trigger login from the frontend."""
    global _bb_session
    _bb_session = None

    try:
        from drexel_login import get_cookie_string
        cookie_str = get_cookie_string()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ── Frontend Serving ──────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


if __name__ == "__main__":
    print()
    print("  ╔═══════════════════════════════════╗")
    print("  ║       Drexel Autopilot            ║")
    print("  ║       http://localhost:5000        ║")
    print("  ╚═══════════════════════════════════╝")
    print()

    # Pre-login at startup
    username = os.getenv("DREXEL_USERNAME", "")
    if username:
        print(f"  Auto-login enabled for: {username}")
        try:
            get_bb_session()
            print("  Connected to Blackboard!")
        except Exception as e:
            print(f"  Login will happen on first request: {e}")
    else:
        print("  Set DREXEL_USERNAME and DREXEL_PASSWORD in .env")
        print("  to enable auto-login")

    print()
    app.run(debug=True, port=5000)
