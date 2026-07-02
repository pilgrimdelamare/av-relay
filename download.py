import json
import os
import sys

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def main():
    video_ids = json.loads(os.environ["VIDEO_IDS"])
    out_dir = "videos"
    os.makedirs(out_dir, exist_ok=True)

    creds = Credentials.from_service_account_file("sa.json", scopes=SCOPES)
    svc = build("drive", "v3", credentials=creds)

    paths = []
    for i, file_id in enumerate(video_ids):
        path = os.path.join(out_dir, f"{i:04d}.mp4")
        req = svc.files().get_media(fileId=file_id)
        with open(path, "wb") as f:
            downloader = MediaIoBaseDownload(f, req)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        print(f"scaricato {file_id} -> {path}", flush=True)
        paths.append(path)

    with open("concat.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(f"file '{p}'" for p in paths))

    if not paths:
        print("nessun video scaricato", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
