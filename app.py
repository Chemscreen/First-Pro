"""
SchuBase Auto Pilot — Backend
==============================
Flask server serving Blackboard data from cache.
Uses BlackboardClient (Playwright) for authentication and data fetching.
"""

import os
import secrets
import threading
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, jsonify, send_from_directory, request, redirect, session, make_response

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from blackboard import BlackboardClient

app = Flask(__name__, static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))

ACCESS_PASSWORD = os.getenv("SCHUBASE_PASSWORD", "")

# Single shared Blackboard client
bb = BlackboardClient()


# ── Auth ─────────────────────────────────────────────────────────

def require_auth(f):
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


# ── API Routes (all serve from cache) ─────────────────────────────

@app.route("/api/status")
@require_auth
def api_status():
    if bb.user and bb.status["connected"]:
        return jsonify({
            "connected": True,
            "platform": "blackboard",
            "user": bb.user,
        })
    return jsonify({
        "connected": False,
        "error": bb.status.get("error") or "Not connected to Blackboard",
    })


@app.route("/api/courses")
@require_auth
def api_courses():
    return jsonify(bb.courses)


@app.route("/api/courses/<course_id>")
@require_auth
def api_course_detail(course_id):
    # Find course in cache
    course = None
    for c in bb.courses:
        if str(c["id"]) == str(course_id):
            course = c
            break

    if not course:
        return jsonify({"error": "Not found"}), 404

    # Build assignments for this course from cached data
    now = datetime.utcnow().isoformat() + "Z"
    course_assignments = [a for a in bb.assignments if str(a.get("course_id")) == str(course_id)]

    # Get grade from cached grades
    grade_info = course.get("grade", {})
    for g in bb.grades:
        if str(g.get("course_id")) == str(course_id):
            grade_info = {"letter": g.get("letter", ""), "score": g.get("score")}
            break

    return jsonify({
        "id": course_id,
        "name": course.get("name", ""),
        "code": course.get("code", ""),
        "term": course.get("term", ""),
        "grade": grade_info,
        "assignments": course_assignments,
        "modules": [],
        "announcements": [],
    })


@app.route("/api/assignments/upcoming")
@require_auth
def api_upcoming():
    now = datetime.utcnow().isoformat() + "Z"
    upcoming = [a for a in bb.assignments if not a.get("submitted") and a.get("due", "") > now]
    upcoming.sort(key=lambda x: x.get("due", ""))
    return jsonify(upcoming)


@app.route("/api/grades")
@require_auth
def api_grades():
    return jsonify(bb.grades)


@app.route("/api/login", methods=["POST"])
@require_auth
def api_bb_login():
    """Trigger Blackboard data refresh."""
    if bb._refreshing:
        return jsonify({
            "success": False,
            "error": "Refresh already in progress",
            "status": "running",
            "step": bb.status.get("step", ""),
        })

    # Run refresh in background, wait for it
    t = threading.Thread(target=bb._refresh_sync, daemon=True)
    t.start()
    t.join(timeout=180)

    if bb._refreshing:
        return jsonify({
            "success": False,
            "error": "Login timed out — MFA may not have been approved in time",
            "status": "timeout",
        })

    if bb.status["connected"]:
        return jsonify({"success": True})

    return jsonify({
        "success": False,
        "error": bb.status.get("error") or "Unknown error",
    }), 500


@app.route("/api/login/status")
@require_auth
def api_login_status():
    return jsonify({
        "running": bb._refreshing,
        "success": bb.status["connected"],
        "error": bb.status.get("error"),
        "step": bb.status.get("step", ""),
    })


@app.route("/api/debug")
@require_auth
def api_debug():
    """Debug endpoint showing connection diagnostics."""
    import time as _time
    info = {
        "connected": bb.status["connected"],
        "refreshing": bb._refreshing,
        "step": bb.status.get("step", ""),
        "error": bb.status.get("error"),
        "user": bb.user,
        "courses_count": len(bb.courses),
        "assignments_count": len(bb.assignments),
        "grades_count": len(bb.grades),
        "cache_age_minutes": round((_time.time() - bb.last_refresh) / 60, 1) if bb.last_refresh else None,
        "cache_path": str(bb._cache_path),
        "cookies_path": str(bb._cookies_path),
        "cache_exists": bb._cache_path.exists(),
        "cookies_exist": bb._cookies_path.exists(),
    }
    return jsonify(info)


@app.route("/api/refresh", methods=["POST"])
@require_auth
def api_refresh():
    """Trigger a background data refresh."""
    if bb.refresh_in_background():
        return jsonify({"success": True, "message": "Refresh started"})
    return jsonify({"success": False, "message": "Refresh already in progress"})


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
    if path in ("login", "login.html"):
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
        print("  Auth: password protected")
    else:
        print("  Auth: OPEN (set SCHUBASE_PASSWORD to enable)")
    username = os.getenv("DREXEL_USERNAME", "")
    if username:
        print(f"  Drexel user: {username}")
    print()
    app.run(debug=False, host="0.0.0.0", port=port)
