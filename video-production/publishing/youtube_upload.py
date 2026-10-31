#!/usr/bin/env python3
"""youtube_upload — YouTube Data API v3 publisher (resumable upload + captions + thumbnail + playlist).

  youtube_upload.py publish.json --dry-run        # validate metadata, print the API body (no auth)
  youtube_upload.py publish.json                  # upload (OAuth on first run, token cached)

Metadata contract: shared-config/workflow-schemas/publish-metadata.schema.json. Chapters are rendered into
the description as "M:SS Title" lines (YouTube's chapter syntax) after rule checks.

Setup (one-time): Google Cloud console -> enable "YouTube Data API v3" -> OAuth client ID (Desktop app)
-> save as ~/.config/global-agent-skills/video-production/secrets/client_secret.json (or --client-secret).
  pip install google-api-python-client google-auth-oauthlib
Quota: videos.insert costs 1600 units of the default 10 000/day; captions.insert 400; thumbnails.set 50.
Note: videos uploaded from an unverified API project are locked to private until the project passes
Google's audit. Keep privacyStatus=private and publish from Studio until then.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "_lib"))
import vpcore  # noqa: E402

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
SECRETS = Path.home() / ".config/global-agent-skills/video-production/secrets"


def fmt_ts(sec: float) -> str:
    s = int(sec)
    return f"{s // 3600}:{(s // 60) % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def check(meta: dict, base: Path) -> list[str]:
    errs = []
    tags_len = sum(len(t) + (2 if " " in t else 0) for t in meta.get("tags", [])) + max(0, len(meta.get("tags", [])) - 1)
    if tags_len > 500:
        errs.append(f"tags total {tags_len} > 500 chars")
    ch = meta.get("chapters", [])
    if ch:
        if ch[0]["timeSec"] != 0:
            errs.append("first chapter must start at 0:00")
        if len(ch) < 3:
            errs.append("YouTube needs >= 3 chapters")
        for a, b in zip(ch, ch[1:]):
            if b["timeSec"] - a["timeSec"] < 10:
                errs.append(f"chapter '{a['title']}' shorter than 10 s")
    if meta.get("publishAt") and meta["privacyStatus"] != "private":
        errs.append("publishAt requires privacyStatus=private")
    for key in ("video", "thumbnail"):
        if meta.get(key) and not (base / meta[key]).exists():
            errs.append(f"{key} not found: {meta[key]}")
    for c in meta.get("captions", []):
        if not (base / c["file"]).exists():
            errs.append(f"caption not found: {c['file']}")
    if meta.get("thumbnail") and (base / meta["thumbnail"]).exists() and (base / meta["thumbnail"]).stat().st_size > 2 * 1024 * 1024:
        errs.append("thumbnail > 2 MB (YouTube limit)")
    return errs


def build_body(meta: dict) -> dict:
    desc = meta["description"].rstrip()
    if meta.get("chapters"):
        desc += "\n\n" + "\n".join(f"{fmt_ts(c['timeSec'])} {c['title']}" for c in meta["chapters"])
    body = {
        "snippet": {"title": meta["title"], "description": desc, "tags": meta.get("tags", []),
                    "categoryId": meta.get("categoryId", "19"), "defaultLanguage": meta.get("defaultLanguage", "bs")},
        "status": {"privacyStatus": meta["privacyStatus"], "selfDeclaredMadeForKids": meta.get("madeForKids", False),
                   "license": meta.get("license", "youtube"), "embeddable": True},
    }
    if meta.get("defaultAudioLanguage"):
        body["snippet"]["defaultAudioLanguage"] = meta["defaultAudioLanguage"]
    if meta.get("publishAt"):
        body["status"]["publishAt"] = meta["publishAt"]
    if "containsSyntheticMedia" in meta:
        body["status"]["containsSyntheticMedia"] = meta["containsSyntheticMedia"]
    rd = meta.get("recordingDetails") or {}
    if rd.get("recordingDate"):
        body["recordingDetails"] = {"recordingDate": rd["recordingDate"]}
    return body


def service(client_secret: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    token = SECRETS / "youtube_token.json"
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES).run_local_server(port=0)
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json())
        token.chmod(0o600)
    return build("youtube", "v3", credentials=creds)


def upload(meta: dict, base: Path, client_secret: Path) -> dict:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    yt = service(client_secret)
    media = MediaFileUpload(str(base / meta["video"]), chunksize=64 * 1024 * 1024, resumable=True, mimetype="video/*")
    req = yt.videos().insert(part="snippet,status,recordingDetails", body=build_body(meta), media_body=media,
                             notifySubscribers=meta["privacyStatus"] == "public")
    resp, retry = None, 0
    while resp is None:
        try:
            status, resp = req.next_chunk()
            if status:
                print(f"upload {status.progress() * 100:.1f}%", file=sys.stderr)
        except HttpError as e:
            if e.resp.status not in (500, 502, 503, 504) or retry >= 5:
                raise
            retry += 1
            time.sleep(2 ** retry)
        except (ConnectionError, TimeoutError):
            if retry >= 5:
                raise
            retry += 1
            time.sleep(2 ** retry)
    vid = resp["id"]
    out = {"videoId": vid, "url": f"https://youtu.be/{vid}", "captions": [], "thumbnail": False}
    for c in meta.get("captions", []):
        yt.captions().insert(part="snippet", body={"snippet": {"videoId": vid, "language": c["language"],
                                                                 "name": c.get("name", c["language"]), "isDraft": False}},
                             media_body=MediaFileUpload(str(base / c["file"]), mimetype="application/octet-stream")).execute()
        out["captions"].append(c["language"])
    if meta.get("thumbnail"):
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(base / meta["thumbnail"]))).execute()
            out["thumbnail"] = True
        except HttpError as e:  # custom thumbnails need a phone-verified channel
            print(f"thumbnail failed: {e}", file=sys.stderr)
    if meta.get("playlistId"):
        yt.playlistItems().insert(part="snippet", body={"snippet": {"playlistId": meta["playlistId"],
                                                                     "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("metadata")
    ap.add_argument("--client-secret", default=str(SECRETS / "client_secret.json"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    meta = vpcore.read_json(a.metadata)
    vpcore.validate(meta, "publish-metadata")
    base = Path(a.metadata).resolve().parent
    errs = check(meta, base)
    if errs:
        raise SystemExit("metadata errors:\n  - " + "\n  - ".join(errs))
    if a.dry_run:
        print(json.dumps(build_body(meta), indent=2, ensure_ascii=False))
        return 0
    print(json.dumps(upload(meta, base, Path(a.client_secret)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
