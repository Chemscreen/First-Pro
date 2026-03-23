"""
Drexel University Course Material Downloader
=============================================
Pulls courses, assignments, grades, and files from both
Canvas (new) and Blackboard Learn (legacy).

Setup:
  1. pip install requests python-dotenv
  2. cp .env.example .env
  3. Add your CANVAS_TOKEN (and/or BB_COOKIES) to .env
  4. python pull_courses.py

Canvas token (recommended - takes 30 seconds):
  1. Go to https://drexel.instructure.com
  2. Profile icon > Settings > + New Access Token
  3. Paste the token into .env as CANVAS_TOKEN
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import requests

OUTPUT_DIR = Path("course_materials")


# ── Canvas (New LMS) ───────────────────────────────────────────────

def pull_canvas():
    base = os.getenv("CANVAS_BASE_URL", "https://drexel.instructure.com").rstrip("/")
    token = os.getenv("CANVAS_TOKEN", "")

    if not token:
        print("CANVAS_TOKEN not set — skipping Canvas.")
        print("  To set up: Canvas > Profile > Settings > + New Access Token")
        print()
        return []

    api = f"{base}/api/v1"
    headers = {"Authorization": f"Bearer {token}"}
    session = requests.Session()
    session.headers.update(headers)

    # Test auth
    print(f"Connecting to Canvas ({base})...")
    resp = session.get(f"{api}/users/self")
    if resp.status_code == 401:
        print("ERROR: Canvas token invalid or expired. Generate a new one.")
        return []
    resp.raise_for_status()
    user = resp.json()
    print(f"Logged in as: {user.get('name', 'Unknown')}")
    print()

    # Get active courses
    resp = session.get(f"{api}/courses", params={
        "enrollment_state": "active",
        "include[]": ["term", "total_scores"],
        "per_page": 100,
    })
    resp.raise_for_status()
    courses = resp.json()

    print(f"Found {len(courses)} active course(s) on Canvas:\n")
    summary = []

    for course in courses:
        course_id = course["id"]
        course_name = course.get("name", f"Course {course_id}")
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in course_name).strip()
        course_dir = OUTPUT_DIR / "canvas" / safe_name
        course_dir.mkdir(parents=True, exist_ok=True)

        print(f"── {course_name} ──")

        course_info = {
            "id": course_id,
            "name": course_name,
            "code": course.get("course_code", ""),
            "term": course.get("term", {}).get("name", ""),
            "source": "canvas",
        }

        # ── Assignments ──
        resp = session.get(f"{api}/courses/{course_id}/assignments", params={
            "per_page": 100,
            "order_by": "due_at",
        })
        assignments = resp.json() if resp.ok else []
        if isinstance(assignments, list):
            course_info["assignments"] = []
            upcoming = 0
            for a in assignments:
                due = a.get("due_at", "")
                is_upcoming = due and due > datetime.now().isoformat() if due else False
                course_info["assignments"].append({
                    "name": a.get("name"),
                    "due": due,
                    "points": a.get("points_possible"),
                    "submitted": a.get("has_submitted_submissions", False),
                    "description_snippet": (a.get("description") or "")[:200],
                    "upcoming": is_upcoming,
                })
                if is_upcoming:
                    upcoming += 1
            print(f"  Assignments: {len(assignments)} total, {upcoming} upcoming")

        # ── Files ──
        resp = session.get(f"{api}/courses/{course_id}/files", params={"per_page": 100})
        files = resp.json() if resp.ok and isinstance(resp.json(), list) else []
        downloaded = 0
        for f in files:
            filename = f.get("display_name", f.get("filename", "unknown"))
            dl_url = f.get("url")
            if dl_url:
                dl_resp = session.get(dl_url, stream=True)
                if dl_resp.ok:
                    filepath = course_dir / filename
                    with open(filepath, "wb") as out:
                        for chunk in dl_resp.iter_content(chunk_size=8192):
                            out.write(chunk)
                    downloaded += 1
        print(f"  Files: {downloaded} downloaded")

        # ── Modules (course content structure) ──
        resp = session.get(f"{api}/courses/{course_id}/modules", params={
            "include[]": "items",
            "per_page": 100,
        })
        modules = resp.json() if resp.ok and isinstance(resp.json(), list) else []
        course_info["modules"] = []
        for mod in modules:
            mod_info = {
                "name": mod.get("name"),
                "items": [
                    {"title": item.get("title"), "type": item.get("type")}
                    for item in mod.get("items", [])
                ],
            }
            course_info["modules"].append(mod_info)
        print(f"  Modules: {len(modules)}")

        # ── Announcements ──
        resp = session.get(f"{api}/courses/{course_id}/discussion_topics", params={
            "only_announcements": True,
            "per_page": 10,
        })
        announcements = resp.json() if resp.ok and isinstance(resp.json(), list) else []
        course_info["recent_announcements"] = [
            {"title": a.get("title"), "date": a.get("posted_at"), "message_snippet": (a.get("message") or "")[:300]}
            for a in announcements[:5]
        ]
        print(f"  Announcements: {len(announcements)}")

        # ── Grades ──
        resp = session.get(f"{api}/courses/{course_id}/enrollments", params={
            "user_id": "self",
            "include[]": "current_points",
        })
        enrollments = resp.json() if resp.ok and isinstance(resp.json(), list) else []
        for e in enrollments:
            grades = e.get("grades", {})
            if grades:
                course_info["current_grade"] = grades.get("current_grade", "N/A")
                course_info["current_score"] = grades.get("current_score", "N/A")
                print(f"  Grade: {grades.get('current_grade', 'N/A')} ({grades.get('current_score', 'N/A')}%)")

        # Save course info
        with open(course_dir / "course_info.json", "w") as out:
            json.dump(course_info, out, indent=2)

        summary.append({"name": course_name, "files": downloaded, "source": "canvas"})
        print()

    return summary


# ── Blackboard / Drexel Learn (Legacy) ─────────────────────────────

def pull_blackboard():
    base = os.getenv("BB_BASE_URL", "https://learn.dcollege.net").rstrip("/")
    cookies_str = os.getenv("BB_COOKIES", "")

    if not cookies_str:
        print("BB_COOKIES not set — skipping Blackboard/Drexel Learn.")
        print("  Only needed if you still have courses on Blackboard.")
        print()
        return []

    session = requests.Session()
    for pair in cookies_str.split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, value = pair.split("=", 1)
            session.cookies.set(name.strip(), value.strip())

    api = f"{base}/learn/api/public/v1"

    print(f"Connecting to Drexel Learn ({base})...")
    resp = session.get(f"{api}/users/me")
    if resp.status_code == 401:
        print("ERROR: Blackboard cookies expired. Log in again and copy fresh cookies.")
        return []
    if not resp.ok:
        print(f"ERROR: Blackboard returned {resp.status_code}. Check BB_BASE_URL.")
        return []

    user = resp.json()
    name = f"{user.get('name', {}).get('given', '')} {user.get('name', {}).get('family', '')}"
    print(f"Logged in as: {name}")
    print()

    resp = session.get(f"{api}/users/me/courses")
    enrollments = resp.json().get("results", []) if resp.ok else []
    print(f"Found {len(enrollments)} enrollment(s) on Blackboard:\n")

    summary = []

    for enrollment in enrollments:
        course_id = enrollment.get("courseId", "")
        resp = session.get(f"{api}/courses/{course_id}")
        if not resp.ok:
            continue
        course = resp.json()
        course_name = course.get("name", course_id)
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in course_name).strip()
        course_dir = OUTPUT_DIR / "blackboard" / safe_name
        course_dir.mkdir(parents=True, exist_ok=True)

        print(f"── {course_name} ──")

        course_info = {
            "id": course_id,
            "name": course_name,
            "description": course.get("description", ""),
            "source": "blackboard",
        }

        # Content & attachments
        resp = session.get(f"{api}/courses/{course_id}/contents")
        contents = resp.json().get("results", []) if resp.ok else []
        downloaded = 0
        for item in contents:
            content_id = item["id"]
            att_resp = session.get(f"{api}/courses/{course_id}/contents/{content_id}/attachments")
            if not att_resp.ok:
                continue
            for att in att_resp.json().get("results", []):
                att_id = att["id"]
                filename = att.get("fileName", f"file_{att_id}")
                dl_url = f"{api}/courses/{course_id}/contents/{content_id}/attachments/{att_id}/download"
                dl_resp = session.get(dl_url, stream=True)
                if dl_resp.ok:
                    with open(course_dir / filename, "wb") as f:
                        for chunk in dl_resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    downloaded += 1

        # Assignments
        resp = session.get(f"{api}/courses/{course_id}/gradebook/columns")
        assignments = resp.json().get("results", []) if resp.ok else []
        course_info["assignments"] = [
            {"name": a.get("name"), "due": a.get("due"), "points": a.get("score", {}).get("possible")}
            for a in assignments
        ]

        print(f"  {len(contents)} content items, {downloaded} files, {len(assignments)} assignments")

        with open(course_dir / "course_info.json", "w") as f:
            json.dump(course_info, f, indent=2)

        summary.append({"name": course_name, "files": downloaded, "source": "blackboard"})
        print()

    return summary


# ── Main ───────────────────────────────────────────────────────────

def main():
    print("=" * 50)
    print("  Drexel University Course Downloader")
    print("=" * 50)
    print()
    print("Drexel is transitioning from Blackboard to Canvas.")
    print("This tool checks both systems automatically.")
    print()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    canvas_summary = pull_canvas()
    bb_summary = pull_blackboard()

    all_courses = canvas_summary + bb_summary

    if not all_courses:
        print("No courses found. Make sure you've set credentials in .env")
        print()
        print("Quickest setup (Canvas — 30 seconds):")
        print("  1. Go to https://drexel.instructure.com")
        print("  2. Log in with Drexel Connect")
        print("  3. Profile icon (top left) > Settings")
        print('  4. Scroll down > "+ New Access Token"')
        print('  5. Name it "CourseHelper" > Generate Token > copy it')
        print("  6. Paste into .env as CANVAS_TOKEN=your_token_here")
        print("  7. Run this script again!")
        return

    # Save combined summary
    with open(OUTPUT_DIR / "summary.json", "w") as f:
        json.dump(all_courses, f, indent=2)

    print("=" * 50)
    print(f"Done! {len(all_courses)} course(s) saved to {OUTPUT_DIR}/")
    print()
    print("Next steps — ask Claude to:")
    print('  "Summarize my courses and upcoming assignments"')
    print('  "Create a study guide for [course name]"')
    print('  "Help me with [assignment name]"')
    print('  "What are my grades looking like?"')


if __name__ == "__main__":
    main()
