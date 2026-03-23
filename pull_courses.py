"""
Quick script to pull all your Blackboard course materials.

Setup:
  1. pip install requests python-dotenv
  2. cp .env.example .env
  3. Fill in BB_BASE_URL and BB_COOKIES in .env
  4. python pull_courses.py

How to get cookies:
  - Log into Blackboard in ANY browser (Chrome, Firefox, Safari, etc.)
  - Press F12 > Network tab > refresh the page
  - Click any request to your Blackboard domain
  - Find the "Cookie" header in the request headers
  - Copy the entire value and paste it as BB_COOKIES in .env
"""

import os
import sys
import json
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("Tip: pip install python-dotenv (optional, you can also export env vars directly)")

import requests


def main():
    base_url = os.getenv("BB_BASE_URL", "").rstrip("/")
    cookies_str = os.getenv("BB_COOKIES", "")

    if not base_url:
        print("ERROR: Set BB_BASE_URL in .env (e.g. https://yourcollege.blackboard.com)")
        sys.exit(1)

    if not cookies_str:
        print("ERROR: Set BB_COOKIES in .env")
        print()
        print("How to get cookies:")
        print("  1. Log into Blackboard in your browser")
        print("  2. Press F12 > Network tab > refresh the page")
        print("  3. Click any request > Headers > Cookie")
        print("  4. Copy the full value into BB_COOKIES in .env")
        sys.exit(1)

    # Build session with cookies
    session = requests.Session()
    for pair in cookies_str.split(";"):
        pair = pair.strip()
        if "=" in pair:
            name, value = pair.split("=", 1)
            session.cookies.set(name.strip(), value.strip())

    # Also try OAuth if available
    api_key = os.getenv("BB_API_KEY")
    api_secret = os.getenv("BB_API_SECRET")
    if api_key and api_secret:
        resp = session.post(
            f"{base_url}/learn/api/public/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(api_key, api_secret),
        )
        if resp.ok:
            token = resp.json()["access_token"]
            session.headers["Authorization"] = f"Bearer {token}"
            print("Authenticated via API key.")
        else:
            print("API auth failed, falling back to cookies.")

    api = f"{base_url}/learn/api/public/v1"

    # Test connection
    print(f"\nConnecting to {base_url}...")
    try:
        resp = session.get(f"{api}/users/me")
        if resp.status_code == 401:
            print("ERROR: Authentication failed. Your cookies may have expired.")
            print("Log into Blackboard again and copy fresh cookies.")
            sys.exit(1)
        resp.raise_for_status()
        user = resp.json()
        print(f"Logged in as: {user.get('name', {}).get('given', '')} {user.get('name', {}).get('family', '')}")
    except requests.ConnectionError:
        print(f"ERROR: Cannot connect to {base_url}")
        print("Check that BB_BASE_URL is correct.")
        sys.exit(1)

    # Get courses
    print("\nFetching courses...")
    resp = session.get(f"{api}/users/me/courses")
    resp.raise_for_status()
    enrollments = resp.json().get("results", [])
    print(f"Found {len(enrollments)} enrollment(s).\n")

    output_base = Path("course_materials")
    summary = []

    for enrollment in enrollments:
        course_id = enrollment.get("courseId", "")

        # Get course details
        resp = session.get(f"{api}/courses/{course_id}")
        if not resp.ok:
            continue
        course = resp.json()
        course_name = course.get("name", course_id)
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in course_name).strip()

        print(f"── {course_name} ──")

        course_dir = output_base / safe_name
        course_dir.mkdir(parents=True, exist_ok=True)

        # Save course info
        info = {
            "id": course_id,
            "name": course_name,
            "description": course.get("description", ""),
            "term": course.get("termId", ""),
        }

        # Get content
        resp = session.get(f"{api}/courses/{course_id}/contents")
        contents = resp.json().get("results", []) if resp.ok else []
        info["content_items"] = len(contents)

        downloaded = 0
        for item in contents:
            content_id = item["id"]
            title = item.get("title", "Untitled")

            # Try to download attachments
            att_resp = session.get(f"{api}/courses/{course_id}/contents/{content_id}/attachments")
            if not att_resp.ok:
                continue

            attachments = att_resp.json().get("results", [])
            for att in attachments:
                att_id = att["id"]
                filename = att.get("fileName", f"file_{att_id}")
                dl_url = f"{api}/courses/{course_id}/contents/{content_id}/attachments/{att_id}/download"

                dl_resp = session.get(dl_url, stream=True)
                if dl_resp.ok:
                    filepath = course_dir / filename
                    with open(filepath, "wb") as f:
                        for chunk in dl_resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    print(f"  Downloaded: {filename}")
                    downloaded += 1

        # Get assignments
        resp = session.get(f"{api}/courses/{course_id}/gradebook/columns")
        assignments = resp.json().get("results", []) if resp.ok else []
        info["assignments"] = [
            {"name": a.get("name"), "due": a.get("due"), "points": a.get("score", {}).get("possible")}
            for a in assignments
        ]

        # Save course summary
        with open(course_dir / "course_info.json", "w") as f:
            json.dump(info, f, indent=2)

        print(f"  {len(contents)} content items, {downloaded} files downloaded, {len(assignments)} assignments")
        summary.append({"name": course_name, "files": downloaded, "assignments": len(assignments)})
        print()

    # Write overall summary
    with open(output_base / "summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print("=== Done ===")
    print(f"Materials saved to: {output_base}/")
    print()
    print("Next: Ask Claude to help you study!")
    print('  Example: "Read my course materials and create a study guide"')


if __name__ == "__main__":
    main()
