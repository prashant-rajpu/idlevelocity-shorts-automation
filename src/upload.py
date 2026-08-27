import json
import os
from datetime import datetime, timezone
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).resolve().parents[1]


def main():
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    meta = json.loads((ROOT / "output/metadata.json").read_text(encoding="utf-8"))
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    description = meta["description"].strip() + "\n\n" + " ".join(meta["hashtags"])
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {"title": meta["title"], "description": description,
                        "categoryId": cfg["category_id"], "defaultLanguage": "hi"},
            "status": {"privacyStatus": cfg["privacy_status"], "selfDeclaredMadeForKids": False},
        },
        media_body=MediaFileUpload(str(ROOT / "output/short.mp4"), mimetype="video/mp4", resumable=True),
    )
    response = None
    while response is None:
        _, response = request.next_chunk()
    history_path = ROOT / "data/history.json"
    history = json.loads(history_path.read_text(encoding="utf-8"))
    history.append({"topic": meta["topic"], "video_id": response["id"],
                    "published_at": datetime.now(timezone.utc).isoformat(), "title": meta["title"]})
    history_path.write_text(json.dumps(history[-500:], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Uploaded: https://youtube.com/shorts/{response['id']}")


if __name__ == "__main__":
    main()

