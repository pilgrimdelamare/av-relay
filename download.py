import concurrent.futures
import json
import os
import sys

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES  = ["https://www.googleapis.com/auth/drive.readonly"]
OUT_DIR = "videos"


def get_service():
    creds = Credentials.from_service_account_file("sa.json", scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def download_one(file_id: str, index: int) -> str:
    svc  = get_service()
    path = os.path.join(OUT_DIR, f"{index:04d}.mp4")
    req  = svc.files().get_media(fileId=file_id)
    with open(path, "wb") as f:
        downloader = MediaIoBaseDownload(f, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return path


def download_batch(ids: list[str], start_index: int, max_workers: int = 5) -> list[str]:
    paths = [None] * len(ids)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(download_one, fid, start_index + i): i for i, fid in enumerate(ids)}
        for fut in concurrent.futures.as_completed(futures):
            i = futures[fut]
            try:
                paths[i] = fut.result()
            except Exception:
                pass
    return [p for p in paths if p]


def get_duration(path: str) -> float | None:
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return None


def write_concat(paths: list[str]):
    with open("concat.txt", "w", encoding="utf-8") as f:
        for p in paths:
            f.write(f"file '{p}'\n")
            dur = get_duration(p)
            if dur:
                f.write(f"duration {dur:.6f}\n")


def rebuild_concat_from_dir() -> list[str]:
    paths = sorted(
        os.path.join(OUT_DIR, name) for name in os.listdir(OUT_DIR) if name.endswith(".mp4")
    )
    write_concat(paths)
    return paths


def main():
    phase     = sys.argv[1] if len(sys.argv) > 1 else "all"
    video_ids = json.loads(os.environ["VIDEO_IDS"])
    first_n   = int(os.environ.get("FIRST_N", "5"))
    os.makedirs(OUT_DIR, exist_ok=True)

    if phase == "first":
        batch = video_ids[:first_n]
        if not batch:
            sys.exit(1)
        paths = download_batch(batch, 0)
        if not paths:
            sys.exit(1)
        write_concat(paths)

    elif phase == "rest":
        rest = video_ids[first_n:]
        if rest:
            download_batch(rest, first_n)
        if not rebuild_concat_from_dir():
            sys.exit(1)

    else:
        paths = download_batch(video_ids, 0)
        if not paths:
            sys.exit(1)
        write_concat(paths)


if __name__ == "__main__":
    main()
