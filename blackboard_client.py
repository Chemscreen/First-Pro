"""
Blackboard LMS Client
=====================
Connects to your college's Blackboard instance to pull courses,
assignments, grades, and course materials.

Usage:
  1. Copy .env.example to .env and fill in your credentials
  2. Run: python blackboard_client.py

Authentication methods supported:
  - REST API (OAuth2 client credentials)
  - Session cookies (from your browser)
"""

import os
import json
import requests
from pathlib import Path
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env loading is optional


class BlackboardClient:
    def __init__(self):
        self.base_url = os.getenv("BB_BASE_URL", "").rstrip("/")
        self.session = requests.Session()
        self.token = None

        if not self.base_url:
            raise ValueError(
                "BB_BASE_URL not set. Set it in .env or as an environment variable.\n"
                "Example: BB_BASE_URL=https://yourcollege.blackboard.com"
            )

    # ── Authentication ──────────────────────────────────────────────

    def auth_oauth(self):
        """Authenticate via OAuth2 client credentials (REST API)."""
        key = os.getenv("BB_API_KEY")
        secret = os.getenv("BB_API_SECRET")
        if not key or not secret:
            raise ValueError("BB_API_KEY and BB_API_SECRET must be set in .env")

        resp = self.session.post(
            f"{self.base_url}/learn/api/public/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(key, secret),
        )
        resp.raise_for_status()
        self.token = resp.json()["access_token"]
        self.session.headers["Authorization"] = f"Bearer {self.token}"
        print("Authenticated via OAuth2.")

    def auth_cookies(self):
        """Authenticate using browser session cookies."""
        cookie_str = os.getenv("BB_COOKIES")
        if not cookie_str:
            raise ValueError(
                "BB_COOKIES not set. Log into Blackboard in your browser, then:\n"
                "  1. Open DevTools (F12) > Application > Cookies\n"
                "  2. Copy all cookies as a single string\n"
                "  3. Set BB_COOKIES in your .env file"
            )
        for pair in cookie_str.split(";"):
            pair = pair.strip()
            if "=" in pair:
                name, value = pair.split("=", 1)
                self.session.cookies.set(name.strip(), value.strip())
        print("Authenticated via session cookies.")

    def authenticate(self):
        """Auto-detect and use the best available auth method."""
        if os.getenv("BB_API_KEY"):
            self.auth_oauth()
        elif os.getenv("BB_COOKIES"):
            self.auth_cookies()
        else:
            raise ValueError(
                "No credentials found. Set one of:\n"
                "  - BB_API_KEY + BB_API_SECRET (for REST API)\n"
                "  - BB_COOKIES (for browser session)\n"
                "See .env.example for details."
            )

    # ── API Methods ─────────────────────────────────────────────────

    def _get(self, endpoint, params=None):
        """Make a GET request to the Blackboard REST API."""
        url = f"{self.base_url}/learn/api/public/v1{endpoint}"
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_user_info(self):
        """Get info about the authenticated user."""
        return self._get("/users/me")

    def get_courses(self):
        """Get all courses the user is enrolled in."""
        data = self._get("/users/me/courses")
        return data.get("results", [])

    def get_course_details(self, course_id):
        """Get details for a specific course."""
        return self._get(f"/courses/{course_id}")

    def get_course_contents(self, course_id):
        """Get the content tree for a course."""
        data = self._get(f"/courses/{course_id}/contents")
        return data.get("results", [])

    def get_assignments(self, course_id):
        """Get assignments/columns for a course."""
        data = self._get(f"/courses/{course_id}/gradebook/columns")
        return data.get("results", [])

    def get_grades(self, course_id):
        """Get the user's grades for a course."""
        user = self.get_user_info()
        user_id = user["id"]
        data = self._get(f"/courses/{course_id}/gradebook/users/{user_id}")
        return data.get("results", [])

    def download_attachment(self, course_id, content_id, output_dir="course_materials"):
        """Download attachments from a content item."""
        data = self._get(f"/courses/{course_id}/contents/{content_id}/attachments")
        attachments = data.get("results", [])

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        for att in attachments:
            att_id = att["id"]
            filename = att.get("fileName", f"attachment_{att_id}")
            url = f"{self.base_url}/learn/api/public/v1/courses/{course_id}/contents/{content_id}/attachments/{att_id}/download"
            resp = self.session.get(url, stream=True)
            resp.raise_for_status()

            filepath = output_path / filename
            with open(filepath, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"  Downloaded: {filepath}")

        return attachments

    # ── High-level Actions ──────────────────────────────────────────

    def list_all_courses(self):
        """Print a summary of all enrolled courses."""
        courses = self.get_courses()
        print(f"\nFound {len(courses)} course(s):\n")
        for i, c in enumerate(courses, 1):
            course_id = c.get("courseId", "unknown")
            details = self.get_course_details(course_id)
            name = details.get("name", course_id)
            print(f"  {i}. {name} ({course_id})")
        return courses

    def download_all_materials(self, course_id, output_dir=None):
        """Download all content attachments for a course."""
        if output_dir is None:
            details = self.get_course_details(course_id)
            course_name = details.get("name", course_id).replace(" ", "_")
            output_dir = f"course_materials/{course_name}"

        contents = self.get_course_contents(course_id)
        print(f"\nDownloading materials for course {course_id}...")
        print(f"Found {len(contents)} content item(s).\n")

        for item in contents:
            title = item.get("title", "Untitled")
            content_id = item["id"]
            print(f"  [{title}]")
            try:
                self.download_attachment(course_id, content_id, output_dir)
            except requests.HTTPError:
                pass  # Not all content items have attachments

    def show_upcoming_assignments(self):
        """Show upcoming assignments across all courses."""
        courses = self.get_courses()
        print("\n=== Upcoming Assignments ===\n")

        for c in courses:
            course_id = c.get("courseId", "unknown")
            try:
                details = self.get_course_details(course_id)
                course_name = details.get("name", course_id)
                assignments = self.get_assignments(course_id)

                upcoming = [
                    a for a in assignments
                    if a.get("due") and a["due"] > datetime.now().isoformat()
                ]

                if upcoming:
                    print(f"  {course_name}:")
                    for a in sorted(upcoming, key=lambda x: x.get("due", "")):
                        name = a.get("name", "Unnamed")
                        due = a.get("due", "No due date")
                        print(f"    - {name} (due: {due})")
                    print()
            except requests.HTTPError:
                continue


def main():
    client = BlackboardClient()

    try:
        client.authenticate()
    except ValueError as e:
        print(f"Auth error: {e}")
        return

    print("\n=== Blackboard Client ===")
    print("1. List courses")
    print("2. Show upcoming assignments")
    print("3. Download course materials")
    print("4. Show grades")

    choice = input("\nSelect an option (1-4): ").strip()

    if choice == "1":
        client.list_all_courses()
    elif choice == "2":
        client.show_upcoming_assignments()
    elif choice == "3":
        courses = client.list_all_courses()
        idx = input("\nEnter course number to download: ").strip()
        if idx.isdigit() and 1 <= int(idx) <= len(courses):
            course_id = courses[int(idx) - 1]["courseId"]
            client.download_all_materials(course_id)
    elif choice == "4":
        courses = client.list_all_courses()
        idx = input("\nEnter course number: ").strip()
        if idx.isdigit() and 1 <= int(idx) <= len(courses):
            course_id = courses[int(idx) - 1]["courseId"]
            grades = client.get_grades(course_id)
            for g in grades:
                col = g.get("columnName", "Unknown")
                score = g.get("displayGrade", {}).get("text", "N/A")
                print(f"  {col}: {score}")
    else:
        print("Invalid option.")


if __name__ == "__main__":
    main()
