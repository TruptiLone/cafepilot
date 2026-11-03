"""Send a sample MP4 with curl without putting the upload token in argv or logs.

From business-core/: PYTHONPATH=. python scripts/try_video_upload.py sample.mp4
This submits real processing to the existing Meta model configured on the backend.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
from uuid import UUID, uuid4

from backend.db.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    parser.add_argument("--capture-id", type=UUID)
    parser.add_argument("--captured-at", help="ISO 8601 timestamp with UTC offset; preserve it when retrying an ID.")
    args = parser.parse_args()
    if not args.file.is_file():
        parser.error("MP4 file not found")
    token = get_settings().media_upload_token
    if not token or not token.get_secret_value().strip():
        parser.error("Set MEDIA_UPLOAD_TOKEN in business-core/.env")
    captured_at = args.captured_at or datetime.fromtimestamp(args.file.stat().st_mtime, timezone.utc).isoformat()
    # curl reads a short-lived mode-0600 config; the credential is never printed.
    with tempfile.NamedTemporaryFile("w", prefix="cafepilot-video-auth-", encoding="utf-8") as config:
        config.write("header = " + json.dumps("Authorization: Bearer " + token.get_secret_value()) + "\n")
        config.flush()
        completed = subprocess.run([
            "curl", "--fail-with-body", "--silent", "--show-error", "--config", config.name,
            "--header", "Content-Type: video/mp4", "--header", "X-Capture-Source: test",
            "--header", "X-Capture-ID: " + str(args.capture_id or uuid4()),
            "--header", "X-Captured-At: " + captured_at,
            "--data-binary", "@" + str(args.file.resolve()),
            args.base_url.rstrip("/") + "/api/upload-video",
        ], check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
