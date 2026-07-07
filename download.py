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


def get_pix_fmt(path: str) -> str | None:
    import subprocess
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=pix_fmt",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or None
    except Exception:
        return None


def normalize_pix_fmt(path: str) -> None:
    """Legacy electro-swing renders used yuvj420p (full-range). Re-encode those
    to yuv420p (limited-range) so every source feeds the concat/merge steps
    with the same color range and avoids the encoder bitrate bursts that
    yuvj420p causes downstream."""
    import subprocess
    pix_fmt = get_pix_fmt(path)
    if not pix_fmt or not pix_fmt.startswith("yuvj"):
        return
    tmp = path + ".norm.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path,
             "-vf", "scale=in_range=full:out_range=tv,format=yuv420p",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-c:a", "copy", tmp],
            capture_output=True, timeout=120, check=True,
        )
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)


def download_one(file_id: str, index: int) -> str:
    svc  = get_service()
    path = os.path.join(OUT_DIR, f"{index:04d}.mp4")
    req  = svc.files().get_media(fileId=file_id)
    with open(path, "wb") as f:
        downloader = MediaIoBaseDownload(f, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    normalize_pix_fmt(path)
    return path


def download_batch(ids: list[str], start_index: int, max_workers: int = 5) -> list[str]:
    # Timeout globale: se dopo 300s non tutti i download sono completati,
    # procedi con quelli gia' finiti. NOTA: non usare 'with' — il context manager
    # chiama shutdown(wait=True) che blocca ugualmente sul thread in stallo.
    # shutdown(wait=False, cancel_futures=True) restituisce immediatamente.
    paths = [None] * len(ids)
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    futures = {ex.submit(download_one, fid, start_index + i): i for i, fid in enumerate(ids)}
    try:
        for fut in concurrent.futures.as_completed(futures, timeout=300):
            i = futures[fut]
            try:
                paths[i] = fut.result()
            except Exception:
                pass
    except concurrent.futures.TimeoutError:
        pass  # procedi con i video gia' scaricati
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
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


def main():
    video_ids = json.loads(os.environ["VIDEO_IDS"])
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = download_batch(video_ids, 0)
    if not paths:
        sys.exit(1)
    write_concat(paths)


if __name__ == "__main__":
    main()
