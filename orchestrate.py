import datetime
import json
import logging
import os
import random
import time
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("orchestrate")

BATCH_SIZE          = 25
MIN_VIDEOS_FOR_LIVE = 5
RELAY_DURATION_MIN  = 290
PREP_LEAD_MIN       = 40
HANDOFF_BUFFER_S    = 5
STATE_FOLDER_NAME   = "_orchestrator_state"

GENRES = ["electro-swing", "rock", "pop", "k-pop", "lofi-chillout"]

LIVE_DESCRIPTIONS = {
    "electro-swing": (
        "Non-stop Electro Swing music, 24/7 — the perfect soundtrack for aperitivo hour, "
        "cocktail bars, speakeasy lounges, dinner parties, restaurant ambience, vintage swing "
        "dance nights, retro parties, and stylish brunch playlists. New AI-generated songs "
        "added daily, always fresh, always dancing between the 1920s and today.\n"
        "#MajestyMusic #ElectroSwing #AperitivoMusic #CocktailBarMusic #SwingMusic "
        "#LoungeMusic #PartyPlaylist #DinnerMusic #RestaurantMusic"
    ),
    "rock": (
        "Non-stop Rock music, 24/7 — high-energy tracks for gym workouts, road trips, garage "
        "sessions, house parties, studying with a beat, motorcycle rides, and driving "
        "playlists. New AI-generated songs added daily, built to keep the energy up all day "
        "long.\n"
        "#MajestyMusic #RockMusic #WorkoutMusic #RoadTripPlaylist #GymMusic #DrivingMusic "
        "#PartyRock #GarageRock"
    ),
    "pop": (
        "Non-stop Pop music, 24/7 — feel-good tracks for the office, studying, running, "
        "cooking, road trips, shopping playlists, background music for work, and everyday "
        "good vibes. New AI-generated songs added daily, catchy and fresh around the clock.\n"
        "#MajestyMusic #PopMusic #StudyMusic #WorkMusic #RunningPlaylist #BackgroundMusic "
        "#FeelGoodMusic #DrivingPlaylist"
    ),
    "k-pop": (
        "Non-stop K-Pop music, 24/7 — perfect for dance practice, gaming sessions, study "
        "breaks, workout playlists, parties, and aesthetic vlog background music. New "
        "AI-generated songs added daily, bilingual Korean-English titles, always fresh "
        "energy.\n"
        "#MajestyMusic #KPop #DancePractice #StudyWithMe #GamingMusic #WorkoutPlaylist "
        "#KPopPlaylist #AestheticMusic"
    ),
    "lofi-chillout": (
        "Non-stop Lofi Chillout music, 24/7 — calm beats for studying, working, focusing, "
        "relaxing, sleeping, rainy days, coffee shop vibes, and late-night unwinding. New "
        "AI-generated songs added daily, smooth and endless chill.\n"
        "#MajestyMusic #LofiHipHop #StudyMusic #ChillBeats #RelaxMusic #SleepMusic "
        "#FocusMusic #CafeMusic"
    ),
}


def get_drive_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        token=None,
        refresh_token=os.environ["DRIVE_REFRESH_TOKEN"],
        client_id=os.environ["DRIVE_CLIENT_ID"],
        client_secret=os.environ["DRIVE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/drive.file"],
    )
    return build("drive", "v3", credentials=creds)


def _list_children(drive, parent_id: str, mime_type: str = None) -> list[dict]:
    q = f"'{parent_id}' in parents and trashed=false"
    if mime_type:
        q += f" and mimeType='{mime_type}'"
    items, token = [], None
    while True:
        res = drive.files().list(
            q=q, fields="nextPageToken, files(id, name)",
            pageToken=token, pageSize=200,
        ).execute()
        items.extend(res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            break
    return items


def find_folder(drive, name: str, parent_id: str) -> str | None:
    q = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
        f"and trashed=false and '{parent_id}' in parents"
    )
    res = drive.files().list(q=q, fields="files(id)").execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


def get_or_create_state_folder(drive, root_folder_id: str) -> str:
    existing = find_folder(drive, STATE_FOLDER_NAME, root_folder_id)
    if existing:
        return existing
    meta = {
        "name": STATE_FOLDER_NAME,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [root_folder_id],
    }
    return drive.files().create(body=meta, fields="id").execute()["id"]


def census_genre(drive, root_folder_id: str, genre: str) -> list[str]:
    genre_folder_id = find_folder(drive, genre, root_folder_id)
    if not genre_folder_id:
        return []
    video_ids = []
    for date_folder in _list_children(drive, genre_folder_id, "application/vnd.google-apps.folder"):
        for f in _list_children(drive, date_folder["id"], "video/mp4"):
            video_ids.append(f["id"])
    return video_ids


def read_state_file(drive, folder_id: str, filename: str) -> dict | None:
    res = drive.files().list(
        q=f"name='{filename}' and trashed=false and '{folder_id}' in parents",
        fields="files(id)",
    ).execute()
    files = res.get("files", [])
    if not files:
        return None
    raw = drive.files().get_media(fileId=files[0]["id"]).execute()
    return json.loads(raw)


def write_state_file(drive, folder_id: str, filename: str, data: dict):
    from googleapiclient.http import MediaInMemoryUpload
    media = MediaInMemoryUpload(json.dumps(data, indent=2).encode("utf-8"), mimetype="application/json")
    res = drive.files().list(
        q=f"name='{filename}' and trashed=false and '{folder_id}' in parents",
        fields="files(id)",
    ).execute()
    files = res.get("files", [])
    if files:
        drive.files().update(fileId=files[0]["id"], media_body=media).execute()
    else:
        meta = {"name": filename, "parents": [folder_id]}
        drive.files().create(body=meta, media_body=media, fields="id").execute()


def get_youtube_service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials(
        token=None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/youtube"],
    )
    return build("youtube", "v3", credentials=creds)


def create_persistent_broadcast(yt, genre: str) -> tuple | None:
    try:
        scheduled = (
            datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=60)
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        title = f"Majesty Music \u2014 {genre.title()} \U0001f3b5 Live 24/7"

        broadcast = yt.liveBroadcasts().insert(
            part="snippet,status,contentDetails",
            body={
                "snippet": {
                    "title": title[:100],
                    "description": LIVE_DESCRIPTIONS.get(genre, f"Non-stop {genre} music, 24/7."),
                    "scheduledStartTime": scheduled,
                },
                "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
                "contentDetails": {
                    "enableAutoStart": True,
                    "enableAutoStop": False,
                    "latencyPreference": "normal",
                    "enableDvr": False,
                },
            },
        ).execute()
        broadcast_id = broadcast["id"]

        stream = yt.liveStreams().insert(
            part="snippet,cdn",
            body={
                "snippet": {"title": title[:100]},
                "cdn": {"frameRate": "30fps", "ingestionType": "rtmp", "resolution": "1080p"},
            },
        ).execute()
        stream_id = stream["id"]
        ingest    = stream["cdn"]["ingestionInfo"]
        rtmp_url  = f"{ingest['ingestionAddress']}/{ingest['streamName']}"

        yt.liveBroadcasts().bind(part="id,contentDetails", id=broadcast_id, streamId=stream_id).execute()
        logger.info(f"{genre}: ok")
        return broadcast_id, stream_id, rtmp_url
    except Exception as e:
        logger.error(f"{genre}: err {e}")
        return None


def broadcast_is_alive(yt, broadcast_id: str) -> bool:
    try:
        res = yt.liveBroadcasts().list(part="status", id=broadcast_id).execute()
        items = res.get("items", [])
        if not items:
            return False
        return items[0]["status"]["lifeCycleStatus"] not in ("complete", "revoked")
    except Exception:
        return False


def end_broadcast(yt, broadcast_id: str, stream_id: str):
    try:
        yt.liveBroadcasts().transition(broadcastStatus="complete", id=broadcast_id, part="status").execute()
    except Exception:
        pass
    try:
        yt.liveStreams().delete(id=stream_id).execute()
    except Exception:
        pass


def stream_is_healthy(yt, stream_id: str) -> bool:
    """False se lo stream YouTube e' in stato bad: il broadcast va ricreato."""
    try:
        res = yt.liveStreams().list(part="status", id=stream_id).execute()
        items = res.get("items", [])
        if not items:
            return False
        health = items[0]["status"].get("healthStatus", {}).get("status", "noData")
        logger.info(f"stream {stream_id}: health={health}")
        return health != "bad"
    except Exception:
        return True


def cancel_run(run_id):
    """Cancella un job live su GitHub Actions."""
    if not run_id or run_id == "pending":
        return
    repo  = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    try:
        _gh_request(repo, token, "POST", f"actions/runs/{run_id}/cancel")
        logger.info(f"run {run_id}: cancellato")
    except Exception as e:
        logger.warning(f"run {run_id}: cancel fallito ({e})")


def _gh_request(repo: str, token: str, method: str, path: str, payload: dict = None):
    url  = f"https://api.github.com/repos/{repo}/{path}"
    data = json.dumps(payload).encode("utf-8") if payload else None
    req  = urllib.request.Request(
        url, data=data,
        headers={
            "Authorization":        f"Bearer {token}",
            "Accept":               "application/vnd.github+json",
            "Content-Type":         "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r) if r.length != 0 else {}


def dispatch_relay(genre: str, video_ids: list[str], rtmp_url: str, duration_minutes: int, start_at: float) -> int | None:
    repo  = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    ts_before = time.time()
    try:
        _gh_request(repo, token, "POST", "actions/workflows/live.yml/dispatches", {
            "ref": "main",
            "inputs": {
                "genre":            genre,
                "video_ids":        json.dumps(video_ids),
                "rtmp_url":         rtmp_url,
                "duration_minutes": str(duration_minutes),
                "start_at":         str(int(start_at)),
            },
        })
    except urllib.error.HTTPError as e:
        logger.error(f"{genre}: err {e.code}")
        return None

    for _ in range(10):
        time.sleep(3)
        try:
            runs = _gh_request(repo, token, "GET", "actions/workflows/live.yml/runs?per_page=5")
            for run in runs.get("workflow_runs", []):
                created_ts = datetime.datetime.fromisoformat(
                    run.get("created_at", "").replace("Z", "+00:00")
                ).timestamp()
                if created_ts >= ts_before - 5:
                    return run["id"]
        except Exception:
            pass
    logger.error(f"{genre}: err no-run")
    return None


def _pick_batch(drive, root_folder_id: str, genre: str, state: dict) -> list[str] | None:
    if len(state["pool"]) < MIN_VIDEOS_FOR_LIVE:
        all_ids = census_genre(drive, root_folder_id, genre)
        if len(all_ids) < MIN_VIDEOS_FOR_LIVE:
            logger.warning(f"{genre}: skip ({len(all_ids)})")
            return None
        random.shuffle(all_ids)
        state["pool"]  = all_ids
        state["cycle"] = state.get("cycle", 0) + 1
    batch, state["pool"] = state["pool"][:BATCH_SIZE], state["pool"][BATCH_SIZE:]
    return batch


def _dispatch_fresh(drive, yt, root_folder_id: str, state_folder_id: str, genre: str, state: dict,
                     start_at: datetime.datetime):
    """Dispatcha un job che diventa corrente subito (nessun handoff in corso)."""
    batch = _pick_batch(drive, root_folder_id, genre, state)
    if batch is None:
        return
    if not state.get("broadcast_id") or not broadcast_is_alive(yt, state["broadcast_id"]):
        result = create_persistent_broadcast(yt, genre)
        if not result:
            return
        state["broadcast_id"], state["stream_id"], state["rtmp_url"] = result

    run_id = dispatch_relay(genre, batch, state["rtmp_url"], RELAY_DURATION_MIN, start_at.timestamp())
    if not run_id:
        return
    state["run_id"]           = run_id
    state["last_dispatch_at"] = start_at.isoformat()
    write_state_file(drive, state_folder_id, f"{genre}.json", state)
    logger.info(f"{genre}: ok {len(batch)} (fresh)")


def _dispatch_next(drive, root_folder_id: str, state_folder_id: str, genre: str, state: dict,
                    start_at: datetime.datetime):
    """Pre-carica il prossimo lotto. Scrive un marker 'pending' su Drive PRIMA del dispatch
    per evitare doppi invii se orchestrate viene interrotto tra dispatch e scrittura run_id."""
    if not state.get("rtmp_url"):
        return
    batch = _pick_batch(drive, root_folder_id, genre, state)
    if batch is None:
        return

    state["next_run_id"]   = "pending"
    state["next_start_at"] = start_at.isoformat()
    write_state_file(drive, state_folder_id, f"{genre}.json", state)

    run_id = dispatch_relay(genre, batch, state["rtmp_url"], RELAY_DURATION_MIN, start_at.timestamp())
    if not run_id:
        state["next_run_id"]   = None
        state["next_start_at"] = None
        write_state_file(drive, state_folder_id, f"{genre}.json", state)
        return
    state["next_run_id"] = run_id
    write_state_file(drive, state_folder_id, f"{genre}.json", state)
    logger.info(f"{genre}: prep ok {len(batch)}, start_at={start_at.isoformat()}")


def process_genre(drive, yt, root_folder_id: str, state_folder_id: str, genre: str, control: dict):
    state = read_state_file(drive, state_folder_id, f"{genre}.json") or {
        "cycle": 0, "pool": [], "last_dispatch_at": None,
        "broadcast_id": None, "stream_id": None, "rtmp_url": None, "run_id": None,
        "next_run_id": None, "next_start_at": None,
    }
    state.setdefault("next_run_id", None)
    state.setdefault("next_start_at", None)

    enabled = control.get(genre, True)
    if not enabled:
        if state.get("broadcast_id"):
            cancel_run(state.get("run_id"))
            cancel_run(state.get("next_run_id"))
            end_broadcast(yt, state["broadcast_id"], state["stream_id"])
            state["broadcast_id"] = state["stream_id"] = state["rtmp_url"] = None
            state["run_id"] = state["next_run_id"] = state["next_start_at"] = None
            write_state_file(drive, state_folder_id, f"{genre}.json", state)
        return

    now = datetime.datetime.now(datetime.timezone.utc)

    if state.get("next_run_id") and state.get("next_start_at"):
        if state["next_run_id"] == "pending":
            # Dispatch precedente interrotto prima di ricevere il run_id reale:
            # non ri-dispatchare (rischierebbe doppio stream), aspetta il prossimo run.
            logger.info(f"{genre}: next dispatch pending, skip")
            return
        next_start = datetime.datetime.fromisoformat(state["next_start_at"])
        if now >= next_start:
            state["run_id"]           = state["next_run_id"]
            state["last_dispatch_at"] = state["next_start_at"]
            state["next_run_id"]      = None
            state["next_start_at"]    = None
            write_state_file(drive, state_folder_id, f"{genre}.json", state)
            logger.info(f"{genre}: handoff completato")
        return

    last = state.get("last_dispatch_at")

    if last is None:
        _dispatch_fresh(drive, yt, root_folder_id, state_folder_id, genre, state, start_at=now)
        return

    current_end = datetime.datetime.fromisoformat(last) + datetime.timedelta(minutes=RELAY_DURATION_MIN)

    if state.get("broadcast_id") and state.get("stream_id"):
        if not stream_is_healthy(yt, state["stream_id"]):
            logger.warning(f"{genre}: stream bad, forzo ricreazione")
            cancel_run(state.get("run_id"))
            cancel_run(state.get("next_run_id"))
            end_broadcast(yt, state["broadcast_id"], state["stream_id"])
            state["broadcast_id"] = state["stream_id"] = state["rtmp_url"] = None
            state["next_run_id"] = state["next_start_at"] = None
            _dispatch_fresh(drive, yt, root_folder_id, state_folder_id, genre, state, start_at=now)
            return

    if now >= current_end - datetime.timedelta(minutes=PREP_LEAD_MIN):
        start_at = current_end + datetime.timedelta(seconds=HANDOFF_BUFFER_S)
        _dispatch_next(drive, root_folder_id, state_folder_id, genre, state, start_at=start_at)
        return


def main():
    root_folder_id = os.environ["DRIVE_ROOT_FOLDER_ID"]
    drive = get_drive_service()
    yt    = get_youtube_service()

    state_folder_id = get_or_create_state_folder(drive, root_folder_id)
    control = read_state_file(drive, state_folder_id, "live_control.json") or {}

    for genre in GENRES:
        try:
            process_genre(drive, yt, root_folder_id, state_folder_id, genre, control)
        except Exception as e:
            logger.error(f"{genre}: err {e}")


if __name__ == "__main__":
    main()
