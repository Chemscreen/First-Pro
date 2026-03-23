"""
Drexel Autopilot — Backend
===========================
Flask server that bridges Canvas/Blackboard APIs
and serves the dashboard frontend.
"""

import os
import json
from datetime import datetime, timedelta
from functools import lru_cache

from flask import Flask, jsonify, send_from_directory, request
import requests as http

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__, static_folder="static")

# ── Canvas API Layer ──────────────────────────────────────────────

CANVAS_BASE = os.getenv("CANVAS_BASE_URL", "https://drexel.instructure.com").rstrip("/")
CANVAS_API = f"{CANVAS_BASE}/api/v1"
CANVAS_TOKEN = os.getenv("CANVAS_TOKEN", "")


def canvas_headers():
    return {"Authorization": f"Bearer {CANVAS_TOKEN}"}


def canvas_get(path, params=None):
    resp = http.get(f"{CANVAS_API}{path}", headers=canvas_headers(), params=params or {})
    if resp.ok:
        return resp.json()
    return None


# ── API Routes ────────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    if not CANVAS_TOKEN:
        return jsonify({"connected": False, "error": "CANVAS_TOKEN not set"})
    user = canvas_get("/users/self")
    if user:
        return jsonify({"connected": True, "user": {
            "name": user.get("name", ""),
            "avatar": user.get("avatar_url", ""),
        }})
    return jsonify({"connected": False, "error": "Token invalid or expired"})


@app.route("/api/courses")
def api_courses():
    courses = canvas_get("/courses", {
        "enrollment_state": "active",
        "include[]": ["term", "total_scores", "course_image"],
        "per_page": 50,
    })
    if not courses:
        return jsonify([])

    result = []
    for c in courses:
        if not isinstance(c, dict):
            continue
        # Get enrollment/grade info
        enrollments = canvas_get(f"/courses/{c['id']}/enrollments", {
            "user_id": "self",
            "include[]": "current_points",
        })
        grade_info = {}
        if enrollments and isinstance(enrollments, list):
            for e in enrollments:
                g = e.get("grades", {})
                if g:
                    grade_info = {
                        "letter": g.get("current_grade", ""),
                        "score": g.get("current_score"),
                    }

        result.append({
            "id": c["id"],
            "name": c.get("name", ""),
            "code": c.get("course_code", ""),
            "term": c.get("term", {}).get("name", "") if c.get("term") else "",
            "color": c.get("course_color", ""),
            "image": c.get("image_download_url", ""),
            "grade": grade_info,
        })

    return jsonify(result)


@app.route("/api/courses/<int:course_id>")
def api_course_detail(course_id):
    course = canvas_get(f"/courses/{course_id}", {
        "include[]": ["term", "total_scores"],
    })
    if not course:
        return jsonify({"error": "Not found"}), 404

    # Assignments
    assignments = canvas_get(f"/courses/{course_id}/assignments", {
        "per_page": 100, "order_by": "due_at",
        "include[]": ["submission"],
    }) or []

    # Modules
    modules = canvas_get(f"/courses/{course_id}/modules", {
        "include[]": "items", "per_page": 50,
    }) or []

    # Announcements
    announcements = canvas_get(f"/courses/{course_id}/discussion_topics", {
        "only_announcements": True, "per_page": 5,
    }) or []

    # Grade
    enrollments = canvas_get(f"/courses/{course_id}/enrollments", {
        "user_id": "self", "include[]": "current_points",
    }) or []
    grade_info = {}
    for e in enrollments:
        g = e.get("grades", {})
        if g:
            grade_info = {"letter": g.get("current_grade", ""), "score": g.get("current_score")}

    return jsonify({
        "id": course_id,
        "name": course.get("name", ""),
        "code": course.get("course_code", ""),
        "term": course.get("term", {}).get("name", "") if course.get("term") else "",
        "grade": grade_info,
        "assignments": [{
            "id": a.get("id"),
            "name": a.get("name", ""),
            "due": a.get("due_at", ""),
            "points": a.get("points_possible"),
            "submitted": a.get("submission", {}).get("submitted_at") is not None if a.get("submission") else False,
            "score": a.get("submission", {}).get("score") if a.get("submission") else None,
            "description": (a.get("description") or "")[:500],
            "url": a.get("html_url", ""),
        } for a in assignments if isinstance(a, dict)],
        "modules": [{
            "name": m.get("name", ""),
            "items": [{"title": i.get("title"), "type": i.get("type")} for i in m.get("items", [])],
        } for m in modules if isinstance(m, dict)],
        "announcements": [{
            "title": a.get("title", ""),
            "date": a.get("posted_at", ""),
            "message": (a.get("message") or "")[:400],
        } for a in announcements if isinstance(a, dict)],
    })


@app.route("/api/assignments/upcoming")
def api_upcoming():
    courses = canvas_get("/courses", {
        "enrollment_state": "active", "per_page": 50,
    })
    if not courses:
        return jsonify([])

    now = datetime.utcnow().isoformat() + "Z"
    upcoming = []

    for c in courses:
        if not isinstance(c, dict):
            continue
        assignments = canvas_get(f"/courses/{c['id']}/assignments", {
            "per_page": 50, "order_by": "due_at",
            "bucket": "upcoming",
            "include[]": ["submission"],
        }) or []
        for a in assignments:
            if not isinstance(a, dict):
                continue
            due = a.get("due_at")
            if due and due > now:
                submitted = False
                if a.get("submission") and isinstance(a["submission"], dict):
                    submitted = a["submission"].get("submitted_at") is not None
                upcoming.append({
                    "id": a.get("id"),
                    "name": a.get("name", ""),
                    "course": c.get("name", ""),
                    "course_code": c.get("course_code", ""),
                    "course_id": c["id"],
                    "due": due,
                    "points": a.get("points_possible"),
                    "submitted": submitted,
                    "url": a.get("html_url", ""),
                })

    upcoming.sort(key=lambda x: x.get("due", ""))
    return jsonify(upcoming)


@app.route("/api/grades")
def api_grades():
    courses = canvas_get("/courses", {
        "enrollment_state": "active", "per_page": 50,
    })
    if not courses:
        return jsonify([])

    grades = []
    for c in courses:
        if not isinstance(c, dict):
            continue
        enrollments = canvas_get(f"/courses/{c['id']}/enrollments", {
            "user_id": "self", "include[]": "current_points",
        }) or []
        for e in enrollments:
            g = e.get("grades", {})
            if g and (g.get("current_score") is not None or g.get("current_grade")):
                grades.append({
                    "course": c.get("name", ""),
                    "code": c.get("course_code", ""),
                    "course_id": c["id"],
                    "letter": g.get("current_grade", ""),
                    "score": g.get("current_score"),
                })

    return jsonify(grades)


# ── Frontend Serving ──────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory("static", path)


if __name__ == "__main__":
    print()
    print("  Drexel Autopilot")
    print("  http://localhost:5000")
    print()
    app.run(debug=True, port=5000)
