#!/usr/bin/env python3
"""Quick GitHub private-repo access check using token from .env or environment.

Usage examples:
  python scripts/test_github_private_repo_access.py
  python scripts/test_github_private_repo_access.py --repo mohan120105/hackathon-docs
  python scripts/test_github_private_repo_access.py --repo mohan120105/hackathon-docs --path pdfs/Corporate_LC_Limits_2024.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GITHUB_API_BASE = "https://api.github.com"


def load_env_file(env_path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def get_setting(name: str, env_values: Dict[str, str], default: str = "") -> str:
    return os.getenv(name, "").strip() or env_values.get(name, default).strip()


def github_get_json(url: str, token: str) -> tuple[int, dict]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "sentinel-private-repo-check",
    }
    req = Request(url=url, headers=headers, method="GET")

    try:
        with urlopen(req, timeout=20) as resp:
            status = resp.getcode()
            data = json.loads(resp.read().decode("utf-8"))
            return status, data
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"message": body}
        return exc.code, payload
    except URLError as exc:
        return 0, {"message": f"Network error: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Check GitHub token access to a private repo.")
    parser.add_argument(
        "--repo",
        default="",
        help="owner/repo. Defaults to GITHUB_POLICY_CONTENTS_REPO, then GITHUB_REPO.",
    )
    parser.add_argument(
        "--path",
        default="",
        help="Optional file path to test under /contents/, e.g. pdfs/file.pdf",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Path to env file (default: .env)",
    )
    args = parser.parse_args()

    env_values = load_env_file(Path(args.env_file))

    token = get_setting("GITHUB_TOKEN", env_values)
    repo = args.repo.strip() or get_setting("GITHUB_POLICY_CONTENTS_REPO", env_values) or get_setting("GITHUB_REPO", env_values)

    if not token:
        print("FAIL: GITHUB_TOKEN is missing (environment or .env).")
        return 2
    if not repo or "/" not in repo:
        print("FAIL: repo is missing/invalid. Provide --repo owner/name or set GITHUB_POLICY_CONTENTS_REPO.")
        return 2

    print(f"Checking repo access for: {repo}")

    repo_url = f"{GITHUB_API_BASE}/repos/{repo}"
    status, payload = github_get_json(repo_url, token)
    if status != 200:
        msg = payload.get("message", "unknown error")
        print(f"FAIL repo metadata: HTTP {status} - {msg}")
        if status == 404:
            print("Hint: token cannot access this private repo, or repo slug is wrong.")
        elif status == 401:
            print("Hint: token is invalid or expired.")
        elif status == 403:
            print("Hint: token exists but lacks required permissions.")
        return 1

    print("PASS repo metadata: HTTP 200")

    root_url = f"{GITHUB_API_BASE}/repos/{repo}/contents"
    root_status, root_payload = github_get_json(root_url, token)
    if root_status != 200:
        msg = root_payload.get("message", "unknown error")
        print(f"FAIL repo contents root: HTTP {root_status} - {msg}")
        return 1

    print("PASS repo contents root: HTTP 200")

    if args.path:
        normalized = args.path.strip().lstrip("/")
        file_url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{normalized}"
        file_status, file_payload = github_get_json(file_url, token)
        if file_status != 200:
            msg = file_payload.get("message", "unknown error")
            print(f"FAIL file path: HTTP {file_status} - {msg}")
            print(f"Path tested: {normalized}")
            return 1

        print("PASS file path: HTTP 200")
        print(f"Path tested: {normalized}")

    print("SUCCESS: token can read this repo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
