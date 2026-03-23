"""
Drexel University LMS Client
=============================
Interactive client for browsing courses, downloading materials,
and checking grades on both Canvas and Blackboard.

For the quick auto-download version, use pull_courses.py instead.

Setup:
  1. pip install requests python-dotenv
  2. cp .env.example .env
  3. Add CANVAS_TOKEN and/or BB_COOKIES
  4. python blackboard_client.py
"""

import os
import sys
import json
import requests
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class DrexelCanvasClient:
    """Client for Drexel's Canvas LMS (new system)."""

    def __init__(self):
        self.base_url = os.getenv("CANVAS_BASE_URL", "https://drexel.instructure.com").rstrip("/")
        self.api = f"{self.base_url}/api/v1"
        self.session = requests.Session()
        token = os.getenv("CANVAS_TOKEN", "")
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        self.available = bool(token)

    def test_connection(self):
        if not self.available:
            return False
        resp = self.session.get(f"{self.api}/users/self")
        if resp.ok:
            user = resp.json()
            print(f"Canvas: Logged in as {user.get('name', 'Unknown')}")
            return True
        print("Canvas: Auth failed — check your CANVAS_TOKEN")
        return False

    def get_courses(self):
        resp = self.session.get(f"{self.api}/courses", params={
            "enrollment_state": "active", "per_page": 100,
            "include[]": ["term", "total_scores"],
        })
        return resp.json() if resp.ok else []

    def get_assignments(self, course_id):
        resp = self.session.get(f"{self.api}/courses/{course_id}/assignments", params={
            "per_page": 100, "order_by": "due_at",
        })
        return resp.json() if resp.ok and isinstance(resp.json(), list) else []

    def get_grades(self, course_id):
        resp = self.session.get(f"{self.api}/courses/{course_id}/enrollments", params={
            "user_id": "self", "include[]": "current_points",
        })
        return resp.json() if resp.ok and isinstance(resp.json(), list) else []

    def download_files(self, course_id, output_dir):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        resp = self.session.get(f"{self.api}/courses/{course_id}/files", params={"per_page": 100})
        files = resp.json() if resp.ok and isinstance(resp.json(), list) else []
        count = 0
        for f in files:
            filename = f.get("display_name", f.get("filename", "unknown"))
            dl_url = f.get("url")
            if dl_url:
                dl_resp = self.session.get(dl_url, stream=True)
                if dl_resp.ok:
                    with open(output_dir / filename, "wb") as out:
                        for chunk in dl_resp.iter_content(chunk_size=8192):
                            out.write(chunk)
                    print(f"  Downloaded: {filename}")
                    count += 1
        return count


class DrexelBlackboardClient:
    """Client for Drexel Learn / Blackboard (legacy system)."""

    def __init__(self):
        self.base_url = os.getenv("BB_BASE_URL", "https://learn.dcollege.net").rstrip("/")
        self.api = f"{self.base_url}/learn/api/public/v1"
        self.session = requests.Session()
        cookies_str = os.getenv("BB_COOKIES", "")
        if cookies_str:
            for pair in cookies_str.split(";"):
                pair = pair.strip()
                if "=" in pair:
                    name, value = pair.split("=", 1)
                    self.session.cookies.set(name.strip(), value.strip())
        self.available = bool(cookies_str)

    def test_connection(self):
        if not self.available:
            return False
        resp = self.session.get(f"{self.api}/users/me")
        if resp.ok:
            user = resp.json()
            name = f"{user.get('name', {}).get('given', '')} {user.get('name', {}).get('family', '')}"
            print(f"Blackboard: Logged in as {name}")
            return True
        print("Blackboard: Auth failed — cookies may be expired")
        return False

    def get_courses(self):
        resp = self.session.get(f"{self.api}/users/me/courses")
        return resp.json().get("results", []) if resp.ok else []

    def get_course_details(self, course_id):
        resp = self.session.get(f"{self.api}/courses/{course_id}")
        return resp.json() if resp.ok else {}

    def get_assignments(self, course_id):
        resp = self.session.get(f"{self.api}/courses/{course_id}/gradebook/columns")
        return resp.json().get("results", []) if resp.ok else []

    def download_files(self, course_id, output_dir):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        resp = self.session.get(f"{self.api}/courses/{course_id}/contents")
        contents = resp.json().get("results", []) if resp.ok else []
        count = 0
        for item in contents:
            content_id = item["id"]
            att_resp = self.session.get(f"{self.api}/courses/{course_id}/contents/{content_id}/attachments")
            if not att_resp.ok:
                continue
            for att in att_resp.json().get("results", []):
                att_id = att["id"]
                filename = att.get("fileName", f"file_{att_id}")
                dl_url = f"{self.api}/courses/{course_id}/contents/{content_id}/attachments/{att_id}/download"
                dl_resp = self.session.get(dl_url, stream=True)
                if dl_resp.ok:
                    with open(output_dir / filename, "wb") as f:
                        for chunk in dl_resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    print(f"  Downloaded: {filename}")
                    count += 1
        return count


def main():
    print("=" * 50)
    print("  Drexel University LMS Client")
    print("=" * 50)
    print()

    canvas = DrexelCanvasClient()
    bb = DrexelBlackboardClient()

    canvas_ok = canvas.test_connection()
    bb_ok = bb.test_connection()
    print()

    if not canvas_ok and not bb_ok:
        print("No LMS connections available.")
        print()
        print("Quick setup (Canvas — 30 seconds):")
        print("  1. Go to https://drexel.instructure.com")
        print("  2. Profile > Settings > + New Access Token")
        print("  3. Paste token into .env as CANVAS_TOKEN=...")
        return

    # Collect all courses
    all_courses = []
    if canvas_ok:
        for c in canvas.get_courses():
            all_courses.append(("canvas", c["id"], c.get("name", f"Course {c['id']}")))
    if bb_ok:
        for e in bb.get_courses():
            cid = e.get("courseId", "")
            details = bb.get_course_details(cid)
            all_courses.append(("bb", cid, details.get("name", cid)))

    while True:
        print("\n── Menu ──")
        print("1. List all courses")
        print("2. Show upcoming assignments")
        print("3. Download course materials")
        print("4. Show grades")
        print("5. Quit")

        choice = input("\nChoice (1-5): ").strip()

        if choice == "1":
            print(f"\n{len(all_courses)} course(s):\n")
            for i, (src, cid, name) in enumerate(all_courses, 1):
                tag = "Canvas" if src == "canvas" else "BB"
                print(f"  {i}. [{tag}] {name}")

        elif choice == "2":
            print("\n── Upcoming Assignments ──\n")
            now = datetime.now().isoformat()
            for src, cid, name in all_courses:
                if src == "canvas":
                    assignments = canvas.get_assignments(cid)
                    upcoming = [a for a in assignments if a.get("due_at") and a["due_at"] > now]
                    if upcoming:
                        print(f"  {name}:")
                        for a in sorted(upcoming, key=lambda x: x.get("due_at", "")):
                            print(f"    - {a['name']} (due: {a['due_at'][:10]}, {a.get('points_possible', '?')} pts)")
                        print()
                else:
                    assignments = bb.get_assignments(cid)
                    upcoming = [a for a in assignments if a.get("due") and a["due"] > now]
                    if upcoming:
                        print(f"  {name}:")
                        for a in sorted(upcoming, key=lambda x: x.get("due", "")):
                            print(f"    - {a['name']} (due: {a.get('due', 'N/A')[:10]})")
                        print()

        elif choice == "3":
            print()
            for i, (src, cid, name) in enumerate(all_courses, 1):
                tag = "Canvas" if src == "canvas" else "BB"
                print(f"  {i}. [{tag}] {name}")
            idx = input("\nCourse number (or 'all'): ").strip()
            if idx.lower() == "all":
                targets = list(enumerate(all_courses))
            elif idx.isdigit() and 1 <= int(idx) <= len(all_courses):
                targets = [(int(idx) - 1, all_courses[int(idx) - 1])]
            else:
                print("Invalid selection.")
                continue
            for _, (src, cid, name) in targets:
                safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()
                subdir = "canvas" if src == "canvas" else "blackboard"
                out = f"course_materials/{subdir}/{safe}"
                print(f"\nDownloading {name}...")
                if src == "canvas":
                    canvas.download_files(cid, out)
                else:
                    bb.download_files(cid, out)

        elif choice == "4":
            print("\n── Grades ──\n")
            for src, cid, name in all_courses:
                if src == "canvas":
                    enrollments = canvas.get_grades(cid)
                    for e in enrollments:
                        g = e.get("grades", {})
                        if g:
                            print(f"  {name}: {g.get('current_grade', 'N/A')} ({g.get('current_score', 'N/A')}%)")

        elif choice == "5":
            print("Done.")
            break


if __name__ == "__main__":
    main()
