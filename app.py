#import select
# COPY .streamlit/ ./.streamlit/
# [server]
# enableXsrfProtection = false
# enableCORS = false
# https://ywangperl-jj7.hf.space/?journey=7events.json (lower case)
# video_url = f"https://storage.googleapis.com/journey-journal/{v.replace('gs://journey-journal/', '')}"
# Remember to run gsutil iam ch allUsers:objectViewer gs://journey-journal once)
# After running, open the .kml in Google Earth Pro or Google My Maps → click markers to see videos play and photos zoom.
import base64
import html
import io
import json
import logging
import mimetypes
import os
import re
import time
import urllib.parse
import zipfile
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import folium
import simplekml
import streamlit as st
import streamlit.components.v1 as components  # ← Correct import for current Streamlit
import toml
from folium.plugins import MarkerCluster  # Add AntPath here
from google.cloud import storage
from google.oauth2 import service_account
from streamlit_folium import st_folium
from streamlit_oauth import OAuth2Component

DEFAULT_ACTIVE_JSON="YourFirstJourney.json"
#ALLOWED_EDIT_EMAILS = ["your.email@gmail.com", "family.member@gmail.com"]
BASE_DIR = Path(os.getcwd()).resolve()

# === DEFINE FOLDERS (CRITICAL - you were missing this!) ===
GCS_BUCKET_PREFIX = "https://storage.googleapis.com/journey-journal/"
BUCKET_NAME = "journey-journal"  # Your GCS bucket name
JOURNEYS_FOLDER = "journeys"      # Folder for JSON files
PHOTOS_FOLDER = "photos"
VIDEOS_FOLDER = "videos"
MIN_DATE = date(1800, 1, 1)              # ← you can lower to 1850 or 1800 if needed
MAX_DATE = date(2026,12,30)


st.session_state.latitude = 1.11
st.session_state.longitude = 1.11

# ----------------------------
# Config (use env vars or st.secrets)
# ----------------------------
CLIENT_ID = st.secrets.get("GOOGLE_CLIENT_ID", os.getenv("GOOGLE_CLIENT_ID", ""))
CLIENT_SECRET = st.secrets.get("GOOGLE_CLIENT_SECRET", os.getenv("GOOGLE_CLIENT_SECRET", ""))

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REFRESH_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

REDIRECT_URI = st.secrets.get(
    "GOOGLE_REDIRECT_URI",
    os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8501/component/streamlit_oauth.authorize_button"),
)

SCOPE = "openid email profile"

# st.title("Google OAuth (streamlit-oauth v0.1.14)")
oauth2 = OAuth2Component(
    CLIENT_ID,
    CLIENT_SECRET,
    AUTHORIZE_URL,
    TOKEN_URL,
    REFRESH_URL,
    REVOKE_URL,
)

# ==================== LOGGING & PATHS ====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create a formatter with timestamp
formatter = logging.Formatter(
    fmt='%(asctime)s | %(levelname)8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# StreamHandler sends output to console (visible in Streamlit Cloud logs)
handler = logging.StreamHandler()
handler.setFormatter(formatter)

# Only add handler once (important for Streamlit reruns)
if not logger.handlers:
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)   # Change to DEBUG for more verbose output

# Quick test log on startup
logger.info("🚀 App started")
if "edit_lat" not in st.session_state:
    st.session_state.edit_lat = None
if "edit_lon" not in st.session_state:
    st.session_state.edit_lon = None
if "default_location" not in st.session_state:
    st.session_state.default_name= None
if "current_journey_locked" not in st.session_state:
    st.session_state.current_journey_locked = False
if "add_new_memory" not in st.session_state:
    st.session_state.add_new_memory = False
if "name" not in st.session_state:
    st.session_state.name = "Logged in User"
if "goto_marker" not in st.session_state:
    st.session_state.goto_marker = False
if "selected_json_file" not in st.session_state:
    st.session_state.selected_json_file = DEFAULT_ACTIVE_JSON
if "reset_map" not in st.session_state:
    st.session_state.reset_map = True
if "edit_lat" not in st.session_state:
    st.session_state.edit_lat = None
if "edit_lon" not in st.session_state:
    st.session_state.edit_lon = None
if "editing_event_id" not in st.session_state:
    st.session_state.editing_event_id = None
if "map_center" not in st.session_state:
    st.session_state.map_center = [20, 0]
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 2
if "force_map_refresh" not in st.session_state:
    st.session_state.force_map_refresh = 0
if "app_mode" not in st.session_state:
    st.session_state.app_mode = "View Mode"  # Default
if "lat_edit" not in st.session_state:
    st.session_state.edit_lat = None
if "lon_edit" not in st.session_state:
    st.session_state.edit_lon = None
if "selected_json_file" not in st.session_state:
    st.session_state.selected_json_file = DEFAULT_ACTIVE_JSON
if "selected_event_id" not in st.session_state:
    st.session_state.selected_event_id = None


# ── Auth state initialization (MUST BE FIRST) ───────────────────────

if "auth" not in st.session_state:
    st.session_state.auth = {
        "token": None,
        "refresh_token": None,
        "user_info": None,
        "is_logged_in": False,
    }

# ── Silent re-auth using refresh token ───────────────────────────────

def is_logged_in():
    return st.session_state.auth.get("is_logged_in", False)
if not is_logged_in():
    refresh_token = st.session_state.auth.get("refresh_token")

    if refresh_token:
        try:
            new_token = oauth2.refresh_token({
                "refresh_token": refresh_token
            })

            st.session_state.auth["token"] = new_token
            st.session_state.auth["is_logged_in"] = True
            st.session_state.current_journey_locked = False

            # Optional: update stored refresh token if Google rotates it
            if new_token.get("refresh_token"):
                st.session_state.auth["refresh_token"] = new_token["refresh_token"]

        except Exception as e:
            # Refresh failed → user must log in again
            st.session_state.auth = {
                "token": None,
                "user_info": None,
                "is_logged_in": False
            }


def upload_to_gcs(file_data, destination_blob_name, content_type="application/octet-stream"):
    """
    Accepts: bytes | bytearray | memoryview | BytesIO | streamlit UploadedFile
    """
    blob = bucket.blob(destination_blob_name)

    # --- normalize to bytes ---
    if file_data is None:
        raise TypeError("upload_to_gcs: file_data is None")

    if isinstance(file_data, (bytes, bytearray, memoryview)):
        data = bytes(file_data)
    elif isinstance(file_data, BytesIO):
        data = file_data.getvalue()
    elif hasattr(file_data, "getvalue"):  # streamlit UploadedFile supports this
        data = file_data.getvalue()
    elif hasattr(file_data, "read"):      # any file-like object
        data = file_data.read()
    else:
        raise TypeError(f"upload_to_gcs: unsupported type {type(file_data)}")

    blob.upload_from_string(data, content_type=content_type)
    return f"gs://{bucket.name}/{destination_blob_name}"
    #return f"{destination_blob_name}"

def download_from_gcs(blob_name):
    blob = bucket.blob(blob_name)
    return blob.download_as_bytes()


def list_journey_blobs():
    # returns full blob names like "journeys/xxx.json"
    return [
        blob.name
        for blob in bucket.list_blobs(prefix=f"{JOURNEYS_FOLDER}/")
        if blob.name.endswith(".json")
    ]

def get_json_path(json_name):
    return f"{JOURNEYS_FOLDER}/{json_name}"

def to_relative_path(path: Path) -> str:
    """Convert absolute Path to path relative to CWD (for JSON storage)"""
    return path.relative_to(BASE_DIR).as_posix()

# def is_journey_locked(json_filename):
#     if IS_CLOUD:
#         lock_blob = bucket.blob(f"{JOURNEYS_FOLDER}/{json_filename}_lock")
#         return lock_blob.exists()
#     else:
#         lock_path = BASE_DIR / f"{json_filename}_lock"
#         return lock_path.exists()

def is_journey_locked(json_filename):
    #if not st.session_state.auth.get("is_logged_in"):
    if not is_logged_in():
        logger.info(f"Journey '{json_filename}' is locked: user not authenticated")
        return True

    if IS_CLOUD:
        lock_blob_name = f"{JOURNEYS_FOLDER}/{json_filename}_lock"
        lock_exists = bucket.blob(lock_blob_name).exists()
        logger.debug(f"Cloud lock check for '{json_filename}': {lock_exists}")
        return lock_exists
    else:
        lock_path = BASE_DIR / f"{json_filename}_lock"
        lock_exists = lock_path.exists()
        logger.debug(f"Local lock check for '{json_filename}': {lock_exists}")
        return lock_exists

def make_public_url(path):
    if path.startswith("https://storage.googleapis.com/"):
        return path
    if path.startswith("gs://journey-journal/"):
        return GCS_BUCKET_PREFIX + path[len("gs://journey-journal/"):]
    if path.startswith(("photos/", "videos/")):
        return GCS_BUCKET_PREFIX + path
    if "uploads/" in path:
        # last resort - only for very old local entries
        return GCS_BUCKET_PREFIX + path.split("uploads/", 1)[-1]
    # fallback - return as is (might not work)
    return path

def export_to_kml(events, output_filename="my_journey_with_timeline.kml"):
    """
    Updated version – 2026-02
    • Uses direct public GCS URLs for both photos and videos
    • Embedded <video> player for videos in popup
    • Thumbnail <img> tags for photos with hover zoom
    • Correct Snippet usage for tooltip
    • Safe handling of missing/None values
    """
    kml = simplekml.Kml(name="My Journey • Photos + Videos", open=1)

    sorted_events = sorted(events, key=lambda e: e.get("date", "0000-00-00"))

    path_coords = []

    journey_line = kml.newlinestring(name="Journey Path")
    journey_line.style.linestyle.color = simplekml.Color.hex("008080c0")  # teal + ~75% opacity
    journey_line.style.linestyle.width = 5
    journey_line.altitudemode = simplekml.AltitudeMode.clamptoground

    skipped = 0

    for idx, event in enumerate(sorted_events, 1):
        try:
            # Coordinates
            loc = event.get("location", {})
            lat = float(loc.get("latitude"))
            lon = float(loc.get("longitude"))
            coord = (lon, lat)
            path_coords.append(coord)

            title    = event.get("title", f"Memory #{idx}")
            date_str = event.get("date", "Unknown date")
            desc     = event.get("description") or ""
            loc_name = loc.get("name", "Unnamed location")

            # Tooltip (mouse hover)
            short_desc = (desc[:100] + "...") if len(desc) > 100 else desc
            tooltip_text = f"{idx}. {date_str} – {title}\n{short_desc or loc_name}"
            tooltip = simplekml.Snippet(tooltip_text)

            # Build popup HTML
            popup_html = f"""
            <div style="font-family:Arial,sans-serif; max-width:540px; line-height:1.58; font-size:15px;">
                <h2 style="margin:0 0 14px; color:#1a4976; text-align:center; font-size:23px;">
                    {title}
                </h2>
                <p style="text-align:center; color:#444; font-weight:bold; margin:8px 0 18px;">
                    📅 {date_str}  •  📍 {loc_name}
                </p>
            """

            if desc:
                popup_html += f"""
                <p style="margin:16px 0 24px; padding:12px; background:#f8f9fa; border-left:4px solid #1a4976; white-space:pre-wrap;">
                    {desc.replace("\n", "<br>")}
                </p>
                """

            photos = event.get("media", {}).get("photos", [])
            videos = event.get("media", {}).get("videos", [])

            # ── VIDEOS ──────────────────────────────────────────────────────
            if videos:
                popup_html += '<h3 style="color:#d35400; margin:28px 0 14px; font-size:19px;">🎬 Videos</h3>'
                for v in videos:
                    # Normalize path to public HTTPS URL

                    video_url = make_public_url(v)
                    safe_url = urllib.parse.quote(video_url, safe=":/")

                    popup_html += '<h3 style="color:#d35400; margin:28px 0 14px; font-size:19px;">🎬 Videos</h3>'
                    for v in videos:
                        video_url = make_public_url(v)
                        safe_url = video_url  # usually safe without extra quoting

                        popup_html += f"""
                            <div style="margin:18px 0 24px; text-align:center; padding:10px; border:1px solid #eee; border-radius:8px;">
                                <video controls style="width:100%; max-height:340px; border-radius:12px; box-shadow:0 4px 12px rgba(0,0,0,0.15);">
                                    <source src="{safe_url}" type="video/mp4">
                                    <!-- No fallback text - we use link instead -->
                                </video>
                                <div style="margin-top:10px; font-size:14px;">
                                    <a href="{safe_url}" target="_blank" style="color:#0066cc; text-decoration:none; font-weight:bold;">
                                        ▶️ Play {v.split('/')[-1]} in new tab
                                    </a>
                                </div>
                            </div>
                            """

            # ── PHOTOS ──────────────────────────────────────────────────────
            if photos:
                popup_html += f'<h3 style="color:#27ae60; margin:32px 0 14px; font-size:19px;">🖼 Photos ({len(photos)})</h3>'
                popup_html += '<div style="display:flex; flex-wrap:wrap; gap:14px; justify-content:center;">'
                for p in photos:
                    # Normalize path to public HTTPS URL
                    img_url = make_public_url(p)

                    popup_html += f"""
                    <a href="{img_url}" target="_blank">
                        <img src="{img_url}"
                             style="width:160px; height:160px; object-fit:cover; border-radius:10px; box-shadow:0 4px 12px rgba(0,0,0,0.18); transition:transform 0.2s;"
                             onmouseover="this.style.transform='scale(1.06)'"
                             onmouseout="this.style.transform='scale(1)'">
                    </a>
                    """
                popup_html += '</div>'

            if not photos and not videos:
                popup_html += '<p style="text-align:center; color:#777; font-style:italic; margin:32px 0;">No media attached</p>'

            popup_html += """
                <p style="text-align:center; color:#888; font-size:12px; margin-top:30px;">
                    Journey Journal export • 旅行日志 🔥
                </p>
            </div>
            """

            # Create placemark
            pnt = kml.newpoint(
                name=f"{idx}. {date_str} – {title}",
                description=popup_html,
                coords=[coord]
            )

            # Tooltip
            pnt.snippet = tooltip

            # Marker icon
            pnt.style.iconstyle.icon.href = "http://maps.google.com/mapfiles/kml/paddle/red-circle.png"
            pnt.style.iconstyle.scale = 1.2

            # Timeline support
            if date_str and len(date_str) == 10:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    pnt.timestamp.when = dt.replace(hour=12, minute=0, second=0).isoformat() + "Z"
                except ValueError:
                    pass

        except Exception as e:
            print(f"Skipped event #{idx}: {str(e)}")
            skipped += 1
            continue

    # Final path
    if len(path_coords) >= 2:
        journey_line.coords = path_coords
    elif path_coords:
        note = kml.newpoint(name="Only one location – no path drawn")
        note.coords = [path_coords[0]]
        note.style.iconstyle.icon.href = "http://maps.google.com/mapfiles/kml/shapes/info.png"

    try:
        kml.save(output_filename)
        print(f"KML saved successfully: {output_filename}")
        print(f"  • Placemarks created: {len(path_coords)}")
        print(f"  • Events skipped: {skipped}")
    except Exception as save_err:
        print(f"Failed to save KML file: {save_err}")

    return output_filename

def export_to_kml_bytes(events) -> bytes:
    logger.info("=== KML EXPORT START ===")
    logger.info(f"Input events count: {len(events)}")

    if not events:
        logger.info("No events received → returning minimal empty KML")
        kml = simplekml.Kml(name="Empty Journey")
        return kml.kml().encode("utf-8")

    kml = simplekml.Kml(name="My Journey with Timeline", open=1)

    sorted_events = sorted(events, key=lambda e: e.get("date", "0000-00-00"))
    logger.info(f"Sorted events count: {len(sorted_events)}")

    if sorted_events:
        first = sorted_events[0]
        logger.info("First event in sorted list:")
        logger.info(f"  title    : {first.get('title', '(no title)')}")
        logger.info(f"  date     : {first.get('date', '(no date)')}")
        logger.info(f"  location : {first.get('location', '(no location)')}")

    path_coords = []
    valid_count = 0
    skipped_count = 0

    journey_line = kml.newlinestring(name="Journey Path")
    journey_line.style.linestyle.color = simplekml.Color.teal
    journey_line.style.linestyle.width = 5
    journey_line.altitudemode = simplekml.AltitudeMode.clamptoground

    for idx, event in enumerate(sorted_events, 1):
        logger.info(f"── Event {idx} ──")
        logger.info(f"  title: {event.get('title', '(no title)')}")

        try:
            loc = event.get("location", {})
            lat_raw = loc.get("latitude")
            lon_raw = loc.get("longitude")

            logger.info(f"  latitude raw  : {lat_raw!r} (type: {type(lat_raw).__name__})")
            logger.info(f"  longitude raw : {lon_raw!r} (type: {type(lon_raw).__name__})")

            if lat_raw is None or lon_raw is None:
                logger.info(f"  → SKIPPED: missing lat or lon")
                skipped_count += 1
                continue

            lat = float(lat_raw)
            lon = float(lon_raw)

            logger.info(f"  parsed → lat = {lat:.6f}, lon = {lon:.6f}")

            # <=== your current coordinate logic goes here ===>

            coord = (lon, lat)
            path_coords.append(coord)
            valid_count += 1

            logger.info(f"  → ADDED placemark (valid count now: {valid_count})")

        except Exception as e:
            logger.info(f"  → EXCEPTION: {type(e).__name__}: {str(e)}")
            skipped_count += 1

    logger.info(f"Loop finished. Valid placemarks: {valid_count}")
    logger.info(f"Skipped events     : {skipped_count}")
    logger.info(f"Path coords count  : {len(path_coords)}")

    if len(path_coords) >= 2:
        journey_line.coords = path_coords
        logger.info("Journey path added")
    else:
        logger.info("No journey path added (too few valid points)")

    logger.info("=== KML EXPORT FINISHED ===")

    return kml.kml().encode("utf-8")

def get_sorted_events_with_index():
    events = st.session_state.data.get("events", [])
    sorted_events = sorted(events, key=lambda x: x.get("date", "0000-00-00"))
    return list(enumerate(sorted_events, start=1))  # (1-based index, event)

def get_local_json_files():
    if IS_CLOUD:
        blobs = list_journey_blobs()
        return sorted([os.path.basename(b) for b in blobs])
    else:
        return sorted([
            f.name for f in BASE_DIR.iterdir()
            if f.is_file() and f.suffix == ".json" and not f.name.startswith(".")
        ])

def save_data_to_storage(data):
    json_text = json.dumps(data, indent=4, ensure_ascii=False)
    if IS_CLOUD:
        logger.info(f" Save to cloud {JSON_BLOB_NAME}")
        upload_to_gcs(json_text.encode("utf-8"), JSON_BLOB_NAME, "application/json")
    else:
        logger.info(f" Save to local {JSON_FILE}")
        Path(JSON_FILE).write_text(json_text, encoding="utf-8")

def ensure_valid_json():
    if not JSON_FILE.exists() or JSON_FILE.stat().st_size == 0:
        default_data = {
            "autobiography": {
                "title": "My Life Journey",
                "author": "Your Name",
                "created_date": datetime.now().strftime("%Y-%m-%d"),
                "last_updated": datetime.now().strftime("%Y-%m-%d")
            },
            "events": []
        }
        # todo JSON_FILE.write_text(json.dumps(default_data, indent=4, ensure_ascii=False), encoding="utf-8")
        save_data_to_storage(st.session_state.data)

# Load data from GCS or local
@st.cache_data(show_spinner=False)
def load_data_from_file(blob_or_path):
    try:
        logger.info(f"📂 Attempting to load data from: {blob_or_path}")
        if IS_CLOUD:
            data_bytes = download_from_gcs(blob_or_path)
            text = data_bytes.decode("utf-8")
        else:
            logger.info(f" blog_or path {Path(blob_or_path)}")
            text = Path(blob_or_path).read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("Empty")
        data = json.loads(text)
        data["events"] = sorted(data["events"], key=lambda x: x.get("date", "0000-00-00"))
        return data
    except Exception as e:
        # Create default if missing
        default_data = {
            "autobiography": {
                "title": "My Life Journey",
                "author": "Your Name",
                "created_date": datetime.now().strftime("%Y-%m-%d"),
                "last_updated": datetime.now().strftime("%Y-%m-%d")
            },
            "events": []
        }
        save_data_to_storage(default_data)
        return default_data

def get_media_bytes(media_path: str):
    """Fetch bytes from GCS (gs://...) or local path."""
    if not media_path:
        return None

    if isinstance(media_path, str) and media_path.startswith("gs://"):
        # gs://bucket/blob
        parts = media_path[5:].split("/", 1)
        bucket_name = parts[0]
        blob_path = parts[1] if len(parts) > 1 else ""

        # IMPORTANT: reuse authenticated client
        client = bucket.client
        return client.bucket(bucket_name).blob(blob_path).download_as_bytes()
    else:
        lp = resolve_local_path(media_path)
        if not lp.exists():
            return None
        return lp.read_bytes()

def get_image_base64(p):
    try:
        data = get_media_bytes(p)
        return base64.b64encode(data).decode('utf-8') if data else None
    except Exception:
        return None

def get_video_base64(p):
    try:
        data = get_media_bytes(p)
        if data and len(data) > 15 * 1024 * 1024:  # 15MB limit
            return None
        return base64.b64encode(data).decode('utf-8') if data else None
    except Exception:
        return None

def get_color_by_year(d):
    y = int(d[:4])
    if y < 1990:
        return "purple"
    elif y < 2000:
        return "blue"
    elif y < 2010:
        return "green"
    elif y < 2020:
        return "orange"
    else:
        return "red"

def _norm_rel_path(p: str) -> str:
    # store paths consistently for zip internal names (no leading ./)
    return p.lstrip("/").lstrip("\\")

def resolve_local_path(p: str) -> Path:
    """
    Your JSON sometimes stores relative paths (uploads/photos/..).
    Convert to absolute local filesystem path for reading/writing.
    """
    pp = Path(p)
    if pp.is_absolute():
        return pp
    return (BASE_DIR / pp).resolve()

def gcs_bytes_from_gs_uri(gs_uri: str) -> bytes:
    # gs://bucket/blob
    parts = gs_uri[5:].split("/", 1)
    bucket_name = parts[0]
    blob_path = parts[1] if len(parts) > 1 else ""

    client = bucket.client
    return  client.bucket(bucket_name).blob(blob_path).download_as_bytes()
    #return storage.Client().bucket(bucket_name).blob(blob_path).download_as_bytes()

def media_bytes_anywhere(media_path: str) -> bytes | None:
    try:
        if media_path.startswith("gs://"):
            return gcs_bytes_from_gs_uri(media_path)
        else:
            lp = resolve_local_path(media_path)
            if lp.exists():
                return lp.read_bytes()
            return None
    except Exception:
        return None

def guess_mime(filename: str) -> str:
    mt, _ = mimetypes.guess_type(filename)
    return mt or "application/octet-stream"

def zip_journey_package(journey_filename: str) -> bytes:
    """
    Create a ZIP that contains:
      - journeys/<journey_filename>  (the JSON)
      - media/photos/<files>
      - media/videos/<files>
      - manifest.json (mapping original media paths -> zip paths)
    """
    # Load JSON bytes
    if IS_CLOUD:
        json_bytes = download_from_gcs(get_json_path(journey_filename))
    else:
        json_bytes = (BASE_DIR / journey_filename).read_bytes()

    data = json.loads(json_bytes.decode("utf-8"))

    # Build manifest + collect media
    manifest = {
        "journey_filename": journey_filename,
        "created_at": datetime.now().isoformat(),
        "media": []  # list of { "original": "...", "zip_path": "media/photos/xxx.jpg" }
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        # JSON inside zip (keep under journeys/)
        z.writestr(f"journeys/{journey_filename}", json_bytes)

        # walk events media
        for ev in data.get("events", []):
            media = ev.get("media", {}) or {}
            for kind in ["photos", "videos"]:
                for mp in media.get(kind, []) or []:
                    b = media_bytes_anywhere(mp)
                    if not b:
                        continue

                    fn = os.path.basename(mp) or f"{kind}_{ev.get('id','x')}"
                    zip_path = f"media/{kind}/{fn}"
                    # avoid collisions by prefixing event id if needed
                    if zip_path in z.namelist():
                        zip_path = f"media/{kind}/event{ev.get('id','x')}_{fn}"

                    z.writestr(zip_path, b)
                    manifest["media"].append({"original": mp, "zip_path": zip_path, "kind": kind})

        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"))

    return buf.getvalue()

def restore_journey_package(zip_bytes: bytes) -> tuple[str, dict]:
    """
    Restore a ZIP created by zip_journey_package().
    Writes media to:
      - GCS: photos/... and videos/...
      - Local: uploads/photos/... and uploads/videos/...
    Then writes the JSON journey file and updates media paths to the new storage locations.

    Returns: (restored_journey_filename, restored_data)
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as z:
        # Load manifest
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))

        journey_filename = manifest["journey_filename"]
        json_zip_path = f"journeys/{journey_filename}"
        data = json.loads(z.read(json_zip_path).decode("utf-8"))

        # Map original -> new path after restore
        path_map = {}

        # Restore media files
        for item in manifest.get("media", []):
            original = item["original"]
            zip_path = item["zip_path"]
            kind = item.get("kind", "photos")

            file_bytes = z.read(zip_path)
            fn = os.path.basename(zip_path)

            if IS_CLOUD:
                # store in your bucket folders: photos/ and videos/
                if kind == "photos":
                    new_url = upload_to_gcs(file_bytes, f"{PHOTOS_FOLDER}/{fn}", guess_mime(fn))
                else:
                    new_url = upload_to_gcs(file_bytes, f"{VIDEOS_FOLDER}/{fn}", guess_mime(fn))
                path_map[original] = new_url
            else:
                if kind == "photos":
                    out = UPLOADS_PHOTOS / fn
                else:
                    out = UPLOADS_VIDEOS / fn
                out.write_bytes(file_bytes)
                path_map[original] = to_relative_path(out)

        # Rewrite JSON media paths
        for ev in data.get("events", []):
            media = ev.get("media", {}) or {}
            for kind in ["photos", "videos"]:
                new_list = []
                for mp in media.get(kind, []) or []:
                    new_list.append(path_map.get(mp, mp))
                media[kind] = new_list
            ev["media"] = media

        # Save JSON back to storage
        json_text = json.dumps(data, indent=4, ensure_ascii=False).encode("utf-8")
        if IS_CLOUD:
            upload_to_gcs(json_text, get_json_path(journey_filename), "application/json")
        else:
            (BASE_DIR / journey_filename).write_bytes(json_text)

        return journey_filename, data

def build_popup_html(event):
    title = html.escape(event.get('title', 'Untitled'))
    desc = html.escape(event.get('description', '') or 'No description')
    if event['location']['name']:
        loc = html.escape(event['location']['name'])
    else:
        loc = html.escape("Location and Name ")

    popup = f"""
    <div style="width:380px;max-height:550px;overflow-y:auto;padding:8px;font-family:sans-serif;">
        <h3 style="text-align:center;margin:0 0 8px 0;">{title}</h3>
        <p style="text-align:center;color:#555;margin:0 0 10px 0;">{event['date']} • {loc}</p>
        <p style="line-height:1.4;margin-bottom:15px;">{desc}</p>
        <hr style="margin:15px 0;">
    """

    photos = event["media"].get("photos", [])
    videos = event["media"].get("videos", [])

    # Add this (working base64 logic - also ensure get_image_base64 and get_video_base64 are present/unchanged):
    if photos:
        popup += "<strong>Photos:</strong><div style='display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:8px;'>"
        for p in photos:
            b64 = get_image_base64(p)
            fn = os.path.basename(p)
            if b64:
                dl = f"data:image/jpeg;base64,{b64}"
                popup += f"""
                <div style="text-align:center;">
                    <img src="{dl}" style="width:100px;height:100px;object-fit:cover;border-radius:8px;cursor:pointer;"
                         onclick="this.style.width='100%';this.style.height='auto';this.onclick=null;">
                    <br><small><a href="{dl}" download="{fn}">📥 Download</a></small>
                </div>
                """
        popup += "</div>"

    if videos:
        popup += "<strong style='margin-top:15px;display:block;'>Videos:</strong><div style='display:flex;flex-direction:column;gap:12px;'>"
        for v in videos:
            b64 = get_video_base64(v)
            fn = os.path.basename(v)
            if b64:
                dl = f"data:video/mp4;base64,{b64}"
                popup += f"""
                <div style="text-align:center;">
                    <video controls style="max-width:100%;border-radius:8px;">
                        <source src="{dl}" type="video/mp4">
                    </video>
                    <br><small><a href="{dl}" download="{fn}">📥 Download</a></small>
                </div>
                """
        popup += "</div>"

    # === FALLBACK MESSAGES ===
    if not photos and not videos:
        popup += "<p style='text-align:center;color:#888;'><em>No media</em></p>"
    else:
        popup += f"<p style='text-align:center;color:#666;margin-top:12px;'><em>{len(photos)} photo(s) • {len(videos)} video(s)</em></p>"

    popup += "</div>"
    return popup

def create_map():
    events = st.session_state.data["events"]

    mode = st.session_state.get("search_mode", "normal")
    value = st.session_state.get("search_value", "")

    if value:
        if mode == "regex":
            try:
                pattern = re.compile(value, re.IGNORECASE)
                filtered_events = [e for e in events if
                            pattern.search(e.get("title", "")) or pattern.search(e.get("description", ""))]
            except re.error:
                st.error("Invalid regex pattern")
                filtered = events
        else:
            lower_search = value.lower()
            filtered_events = [
                e for e in events
                if lower_search in e.get("title", "").lower() or
                   lower_search in e.get("description", "").lower()
            ]
        events = filtered_events

    # # todo remove session variable search_text = st.session_state.get("search_text", "")
    # search_text = value
    #
    # if search_text:
    #     lower_search = search_text.lower()
    #     filtered_events = [
    #         e for e in events
    #         if lower_search in e.get("title", "").lower() or
    #            lower_search in e.get("description", "").lower()
    #     ]
    # else:
    #     filtered_events = events

    if not events:
        m = folium.Map(location=[20, 0], zoom_start=2, tiles="OpenStreetMap")
        return m

    sorted_events = sorted(events, key=lambda x: x["date"])
    coords = [[e["location"]["latitude"], e["location"]["longitude"]] for e in sorted_events]

    m = folium.Map(tiles="OpenStreetMap")
    cluster = MarkerCluster().add_to(m)

    # Add numbered markers
    for idx, e in enumerate(sorted_events, start=1):
        escaped_desc = html.escape(f"{e['description']}")
        tooltip_html = f"""
                <div style="
                    font-family: sans-serif;
                    min-width: 200px;
                    max-width: 300px;   /* Limits width so text wraps */
                    padding: 8px;
                    line-height: 1.4;
                ">
                    <strong style="font-size: 15px;">{idx}.{e['date']} {html.escape(e['title'])} </strong>
                    <div style="
                        font-size: 14px;
                        color: #333;
                        font-style: italic;
                        white-space: normal;   /* Ensures wrapping */
                        word-wrap: break-word; /* Breaks long words if needed */
                    ">
                        {escaped_desc}
                    </div>
                </div>
                """
        folium.Marker(
            [e["location"]["latitude"], e["location"]["longitude"]],
            popup=folium.Popup(build_popup_html(e), max_width=450),
            #tooltip=f"{idx}. {e['title']} ({e['date']})",
            #tooltip=f"{idx}. <b>{e['date']} {e['title']}</b> {e['description']}",
            #tooltip=f"{idx}. <b>{e['date']} {e['title']}</b> {e['description']}",
            tooltip=folium.Tooltip(tooltip_html, perment=False, sticky=True),
            icon=folium.Icon(color=get_color_by_year(e["date"]), icon="circle", prefix="fa")
        ).add_to(cluster)

        # Number label above marker
        label_html = f"""
        <div style="
            font-size: 14pt;
            color: #333333;
            background: rgba(255, 255, 255, 0.9);
            padding: 6px 12px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
            white-space: nowrap;
            font-weight: bold;
            border: 1px solid #ccc;
        ">
            {idx}
        </div>
        """
        folium.Marker(
            [e["location"]["latitude"], e["location"]["longitude"]],
            icon=folium.DivIcon(
                html=label_html,
                icon_size=(None, None),
                icon_anchor=(10, -10)
            )
        ).add_to(m)

    # === CURVED + ANIMATED JOURNEY LINE USING ANTPath ===
    if len(coords) > 1:
        # Import and add AntPath plugin (curved, animated, pulsing flow)
        from folium.plugins import AntPath

        AntPath(
            locations=coords,
            color="#50E3C2",           # Teal/cyan flowing color
            weight=2,                  # Thin but visible
            opacity=0.8,
            pulse_color="#ffffff",
            delay=800,                 # Animation speed
            dash_array=[10, 20],
            smooth_factor=50,           # Higher = more curved/smoother
            hardware_accelerated=True,
            tooltip="Your life journey →"
        ).add_to(m)

        # Optional: Add a subtle static curved baseline (great circle feel)
        folium.PolyLine(
            locations=coords,
            weight=3,
            color="#4A90E2",
            opacity=0.4,
            smooth_factor=50           # Very high for natural Earth curve
        ).add_to(m)

    #m.fit_bounds(coords, padding=(80, 80))
    return m

from datetime import datetime
import os
from pathlib import Path
import time

def get_today_log_identifier() -> str:
    """Returns the date part for today's log file, e.g. '2026-02-04'"""
    return datetime.now().strftime("%Y-%m-%d")


def get_log_path_or_blob() -> str:
    """Returns the full path/blob name for today's log file"""
    date_str = get_today_log_identifier()
    if IS_CLOUD:
        return f"{LOG_PREFIX}_{date_str}.txt"
    else:
        return LOG_DIR / f"{LOG_BASE_NAME}_{date_str}.txt"


def append_to_log(message: str, message_type: str = "general", throttle: bool = True):
    """
    Append one log line with timestamp.
    Features:
      - Daily rotation (new file each day)
      - Throttling: avoid logging same message_type too frequently
    """
    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {message}\n"

    # Throttling check
    if throttle:
        last_time = _last_log_times.get(message_type, 0)
        if time.time() - last_time < MIN_SECONDS_BETWEEN_SAME_MESSAGE:
            return  # skip logging - too soon

    # Update last log time
    _last_log_times[message_type] = time.time()

    if IS_CLOUD:
        # ── GCS ────────────────────────────────────────────────────────
        blob_name = get_log_path_or_blob()
        blob = bucket.blob(blob_name)
        try:
            if blob.exists():
                existing = blob.download_as_text(encoding="utf-8")
                new_content = existing.rstrip() + "\n" + log_line.strip()
            else:
                new_content = log_line

            blob.upload_from_string(
                new_content,
                content_type="text/plain; charset=utf-8"
            )
            logger.debug(f"Appended to GCS: {blob_name}")
        except Exception as e:
            logger.error(f"GCS log append failed ({blob_name}): {e}")

    else:
        # ── Local filesystem ──────────────────────────────────────────
        log_path = get_log_path_or_blob()
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(log_line)
            logger.debug(f"Appended to local: {log_path}")
        except Exception as e:
            logger.error(f"Local log write failed ({log_path}): {e}")


def get_recent_log_content(lines: int = 30) -> str:
    """Read last N lines from today's log file (for sidebar preview)"""
    date_str = get_today_log_identifier()
    if IS_CLOUD:
        blob_name = f"{LOG_PREFIX}_{date_str}.txt"
        try:
            blob = bucket.blob(blob_name)
            if not blob.exists():
                return f"(No log for today {date_str} yet)"
            content = blob.download_as_text(encoding="utf-8")
            all_lines = content.strip().split("\n")
            recent = all_lines[-lines:] if len(all_lines) >= lines else all_lines
            return "\n".join(recent) if recent else "(empty)"
        except Exception as e:
            return f"Cannot read GCS log: {str(e)}"
    else:
        log_path = LOG_DIR / f"{LOG_BASE_NAME}_{date_str}.txt"
        if not log_path.exists():
            return f"(No log file for {date_str} yet)"
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                content = f.read()
            all_lines = content.strip().split("\n")
            recent = all_lines[-lines:] if len(all_lines) >= lines else all_lines
            return "\n".join(recent) if recent else "(empty)"
        except Exception as e:
            return f"Cannot read log file: {str(e)}"

    # Visitor access


def get_audit_actor_info() -> str:
    parts = []

    if is_logged_in():
        parts.append(f"user={st.session_state.get('email', 'unknown')}")
        parts.append(f"name=\"{st.session_state.get('name', 'Unknown')}\"")
    else:
        parts.append("user=anonymous")

    parts.append(f"device={st.session_state.get('device_type', 'unknown')}")
    parts.append(f"journey={st.session_state.selected_json_file}")
    parts.append("location=Portland, Oregon, US")  # from your provided info

    return " | ".join(parts)

# === DETECT IF RUNNING ON STREAMLIT CLOUD ===
IS_HF = bool(os.getenv("SPACE_ID"))  # HF sets SPACE_ID automatically
IS_CLOUD = IS_HF                     # treat HF as cloud backend

# ───────────────────────────────────────────────────────────────
# Log configuration
# ───────────────────────────────────────────────────────────────

LOG_DIR_NAME = "logs"
LOG_BASE_NAME = "jj7_log"

if IS_CLOUD:
    LOG_PREFIX = f"{LOG_DIR_NAME}/{LOG_BASE_NAME}"
    logger.info(f"Logging target: GCS → {LOG_PREFIX}_YYYY-MM-DD.txt")
else:
    LOG_DIR = BASE_DIR / LOG_DIR_NAME
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Logging target: local → {LOG_DIR}/{LOG_BASE_NAME}_YYYY-MM-DD.txt")

# Throttling settings
MIN_SECONDS_BETWEEN_SAME_MESSAGE = 30      # same message type won't log more often than this
_last_log_times = {}                        # message_type → last timestamp


try:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / ".streamlit" / "config.toml"

    config = toml.load(config_path)
    xsrf = config.get("server", {}).get("enableXsrfProtection", "not set")
    st.sidebar.info(f"XSRF protection status: {xsrf}")

except Exception as e:
    st.sidebar.warning(f"Could not read config.toml: {e}")

def init_gcs_bucket_from_env():
    sa_json = os.getenv("GCP_SA_JSON")
    bucket_name = os.getenv("GCS_BUCKET_NAME")

    if not sa_json:
        raise RuntimeError("Missing HF Secret: GCP_SA_JSON")
    if not bucket_name:
        raise RuntimeError("Missing HF Variable: GCS_BUCKET_NAME")

    sa_info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(sa_info)
    client = storage.Client(credentials=creds, project=sa_info["project_id"])
    return client.bucket(bucket_name)

if IS_CLOUD:
    sa_json = os.getenv("GCP_SA_JSON")
    bucket_name = os.getenv("GCS_BUCKET_NAME")

    if not sa_json:
        raise RuntimeError("Missing HF Secret: GCP_SA_JSON")
    if not bucket_name:
        raise RuntimeError("Missing HF Variable: GCS_BUCKET_NAME")

    sa_info = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(sa_info)
    storage_client = storage.Client(credentials=creds, project=sa_info["project_id"])
    bucket = storage_client.bucket(bucket_name)
    st.sidebar.success("✅ Running on Hugging Face (GCS enabled)")
    bucket = init_gcs_bucket_from_env()
else:
    st.sidebar.info("🖥️ Running locally (filesystem)")
    UPLOADS_PHOTOS = BASE_DIR / "uploads" / "photos"
    UPLOADS_VIDEOS = BASE_DIR / "uploads" / "videos"
    UPLOADS_PHOTOS.mkdir(parents=True, exist_ok=True)
    UPLOADS_VIDEOS.mkdir(parents=True, exist_ok=True)

class init_user:
    is_logged_in = False
    name = "init_user"
    email = "init_user_email"
    sub = "init_user_sub"

st.user = init_user()
# ──────────────────────────────────────────────────────────────
#          TEMP BYPASS – Google login is broken right now
# ──────────────────────────────────────────────────────────────

# Force login for everyone (temporary dev workaround)
if False:  # ← change to False when you fix real auth
    if "bypass_auth" not in st.session_state:
        st.session_state.bypass_auth = True
        st.session_state.user_info = {
            "name": "Test User 🔥",
            "email": "test@example.com",
            "sub": "bypass-20260119",
        }

    class FakeUser:
        is_logged_in = True
        name = st.session_state.user_info["name"]
        email = st.session_state.user_info["email"]
        sub = st.session_state.user_info["sub"]

    st.user = FakeUser()

    # Show warning banner so you don't forget
    #st.warning("⚠️  AUTH BYPASS ACTIVE  – Google login is temporarily disabled")

else:
    pass

# ==================== DEVICE DETECTION ====================
if "device_type" not in st.session_state:
    detect_js = """
    <script>
        function detectDevice() { const width = window.innerWidth; const hasTouch = 'ontouchstart' in window || navigator.maxTouchPoints > 0; const ua = navigator.userAgent.toLowerCase(); const isMobileUA = /android|webos|iphone|ipad|ipod|blackberry|iemobile|opera mini/i.test(ua);

            if (width <= 768 && (hasTouch || isMobileUA)) {
                return "mobile";
            } else if (width <= 1024) {
                return "tablet";
            } else {
                return "desktop";
            }
        }

        const device = detectDevice();

        if (window.parent && window.parent.postMessage) {
            window.parent.postMessage({
                type: 'streamlit:setComponentValue',
                value: device
            }, '*');
        }
    </script>
    """

    returned_value = components.html(detect_js, height=0, width=0)
    st.session_state.device_type = returned_value or "desktop"

# Then set the initial sidebar based on device
initial_sidebar = "collapsed" if st.session_state.device_type == "mobile" else "expanded"

# ==================== JSON FILE PATH WITH ARGUMENT SUPPORT ====================
# parser = argparse.ArgumentParser(description="My Life Journey App")
# parser.add_argument(
#    "--file",
#    type=str,
#    default=DEFAULT_ACTIVE_JSON,
#    help=f"Path to the life events JSON file (default: {DEFAULT_ACTIVE_JSON})"
# )
# args = parser.parse_args()

# ── Handle shared journey via URL query param ────────────────────────────────
if "journey" in st.query_params:
    requested = st.query_params["journey"][0] if isinstance(st.query_params["journey"], list) else st.query_params[
        "journey"]
    # Basic safety: must end with .json and no dangerous characters
    if requested.endswith(".json") and all(c.isalnum() or c in "-_" for c in requested.replace(".json", "")):
        # Optional: normalize (you can skip if filenames are already clean)
        requested = requested.lower().replace(" ", "-") + ".json" if not requested.endswith(".json") else requested

        # Check if this journey actually exists in GCS / local
        blob_name = get_json_path(requested) if IS_CLOUD else str(BASE_DIR / requested)
        try:
            # Try a quick existence check (lightweight)
            if IS_CLOUD:
                bucket.blob(blob_name).exists()
            else:
                Path(blob_name).exists()

            st.session_state.selected_json_file = requested
            #st.session_state.app_mode = "View Mode"  # force read-only for shared links
            st.toast(f"Opened shared journey: {requested.replace('.json', '').replace('-', ' ').title()}", icon="🔗")
        except:
            st.warning(f"Journey '{requested}' not found or inaccessible.")
    else:
        st.warning("Invalid journey link.")

# st.sidebar.caption(f"📄 Using data file: `{JSON_FILE.name}`") # todo
#if "selected_json_file" not in st.session_state:
#    st.session_state.selected_json_file = DEFAULT_ACTIVE_JSON

JSON_BLOB_NAME = get_json_path(st.session_state.selected_json_file) if IS_CLOUD else str(BASE_DIR / st.session_state.selected_json_file)
JSON_FILE = BASE_DIR / st.session_state.selected_json_file

# === AUTO-CREATE FIRST JOURNEY IF NONE EXIST ===
available_journeys = get_local_json_files()

if not available_journeys:
    default_filename = DEFAULT_ACTIVE_JSON  # "YourFirstJourney.json"
    st.session_state.selected_json_file = default_filename

    # Recompute paths with the new selected file
    #global JSON_BLOB_NAME, JSON_FILE
    JSON_BLOB_NAME = get_json_path(default_filename) if IS_CLOUD else str(BASE_DIR / default_filename)
    JSON_FILE = BASE_DIR / default_filename

    default_data = {
        "autobiography": {
            "title": "Your First Journey",
            "author": "Your Name",
            "created_date": datetime.now().strftime("%Y-%m-%d"),
            "last_updated": datetime.now().strftime("%Y-%m-%d")
        },
        "events": []
    }

    # Now safe to save — all paths are defined
    save_data_to_storage(default_data)
    logger.info(f"🌟 Created default journey: {default_filename}")

    # Reload data into session state
    st.session_state.data = default_data
    data = default_data

    # Refresh list
    available_journeys = get_local_json_files()
else:
    # Normal case: journeys exist
    pass

# Right after st.session_state.selected_json_file = json_name
st.session_state.current_journey_locked = is_journey_locked(st.session_state.selected_json_file)

local_json_files = available_journeys

# ==================== DYNAMIC TITLE BASED ON JSON FILENAME ====================
# Get filename without extension and path
json_filename = st.session_state.selected_json_file # e.g., "life_events", "my_family_memories", "john_2025"

###### TODO Clean up common patterns for nicer display
display_name = json_filename.replace("_", " ").replace("-", " ")
# Capitalize each word
display_name = " ".join(word.capitalize() for word in display_name.split())

# Fallback if somehow empty
if not display_name.strip():
    display_name = "My Journey"

# Get available journeys (local or cloud)
available_journeys = get_local_json_files()
local_json_files   = available_journeys

# If NO journeys exist at all → create the default one
if not available_journeys:
    default_filename = DEFAULT_ACTIVE_JSON  # "YourFirstJourney.json"
    default_path_or_blob = get_json_path(default_filename) if IS_CLOUD else str(BASE_DIR / default_filename)

    # Only create if it really doesn't exist (safety)
    exists = default_filename in available_journeys
    if not exists:
        default_data = {
            "autobiography": {
                "title": "Your First Journey",
                "author": "Your Name",
                "created_date": datetime.now().strftime("%Y-%m-%d"),
                "last_updated": datetime.now().strftime("%Y-%m-%d")
            },
            "events": []
        }
        save_data_to_storage(default_data)  # This uses upload_to_gcs or local write correctly
        logger.info(f"Created default journey: {default_filename}")

        # Ensure it's selected
        st.session_state.selected_json_file = default_filename

    available_journeys = get_local_json_files()  # Refresh list

#local_json_files = get_local_json_files()
local_json_files = available_journeys

if "data" not in st.session_state:
    #st.session_state.data = load_data_from_file(JSON_FILE)
    st.session_state.data = load_data_from_file(JSON_BLOB_NAME)

ensure_valid_json()

data = st.session_state.data

local_json_files = get_local_json_files()

# Calculate timeline year range (only if there are events)
timeline_info = ""
if data["events"]:
    sorted_events = sorted(data["events"], key=lambda x: x["date"])
    dates = [datetime.strptime(e["date"], "%Y-%m-%d") for e in sorted_events]
    if dates:
        start_year = min(dates).year
        end_year = max(dates).year
        timeline_info = f" ({start_year} – {end_year})"
        timeline_info = f" ({start_year})" if start_year == end_year else f" ({start_year}–{end_year})" if data[
            "events"] else ""

# ==================== DYNAMIC TITLE WITH FILENAME AND MEMORY COUNT ====================
json_filename = JSON_FILE.name
if st.session_state.selected_json_file:
    json_filename = st.session_state.selected_json_file

display_name = json_filename.replace(".json", "").replace("_", " ").replace("-", " ")
display_name = " ".join(word.capitalize() for word in display_name.split())

memory_count = len(st.session_state.data.get("events", []))

if data["events"]:
    sorted_events = sorted(data["events"], key=lambda x: x["date"])
    dates = [datetime.strptime(e["date"], "%Y-%m-%d") for e in sorted_events]
    start_year = min(dates).year
    end_year = max(dates).year
    timeline_info = f" ({start_year}–{end_year})"
    timeline_info = f" ({start_year})" if start_year == end_year else f" ({start_year}–{end_year})" if data[
        "events"] else ""
else:
    timeline_info = ""

event_count = len(st.session_state.data.get("events", []))
place_text = "memory" if event_count == 1 else "memories"
memory_count_str = f"{event_count} {place_text}" if event_count > 0 else "no memory  yet"

full_title = f"🌍 Journey ({display_name}) – {memory_count_str}{timeline_info}"

st.set_page_config(
   page_title=full_title,
   layout="wide",
   initial_sidebar_state=initial_sidebar
)


# ==================== RESPONSIVE CSS BASED ON DETECTED DEVICE ====================
device = st.session_state.device_type

css = """
<style>
    /* Common styles for all devices */
    .main > div { padding-top: 0rem !important; }
    .block-container { 
        padding-top: 2rem !important; 
        padding-left: 1rem !important; 
        padding-right: 1rem !important; 
    }

    /* Map iframe - base */
    iframe {
        width: 100% !important;
        border: none;
        min-height: 500px !important;
    }

    /* Larger touch targets */
    .stButton > button {
        height: 3em !important;
        font-size: 16px !important;
    }
    .stTextInput > div > div > input,
    .stDateInput > div > div,
    .stTextArea > div > div > textarea {
        font-size: 16px !important;
    }

    /* Timeline base */
    .timeline-container {
        margin: 10px 0;
        padding: 10px;
        background: linear-gradient(to bottom, #f0f4f8, #e0e8f0);
        border-radius: 12px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.12);
    }
"""

# ==================== DEVICE-SPECIFIC STYLES ====================
if device == "mobile":
    css += """
    iframe {
        height: 65vh !important;
        min-height: 450px !important;
    }
    h1, h2, h3 { font-size: 1.6rem !important; }
    .timeline-bar { height: 6px; margin: 30px 0 10px 0; }
    .timeline-label { font-size: 11px !important; }
    .timeline-label strong { font-size: 13px !important; }
    section[data-testid="stSidebar"] {
        width: 100% !important;
        min-width: 100% !important;
    }
    """

elif device == "tablet":
    css += """
    iframe {
        height: 75vh !important;
        min-height: 550px !important;
    }
    h1, h2, h3 { font-size: 1.8rem !important; }
    section[data-testid="stSidebar"] {
        width: 350px !important;
    }
    """

else:  # desktop
    css += """
    iframe {
        height: 85vh !important;
        min-height: 600px !important;
    }
    section[data-testid="stSidebar"] {
        min-width: 380px !important;
        width: 380px !important;
    }
    """

# ==================== SHARED TIMELINE STYLING (kept from original) ====================
css += """
    .timeline-bar {
        position: relative;
        height: 8px;
        background: linear-gradient(to right, #a0c4ff, #9ec5fe, #bdb2ff, #ffc6ff);
        border-radius: 4px;
        margin: 40px 0 15px 0;
        box-shadow: 0 2px 6px rgba(0,0,0,0.1);
    }
    .timeline-tick {
        position: absolute;
        top: -20px;
        left: -3px;
        width: 6px;
        height: 45px;
        background: #5d8aa8;
        transform: rotate(35deg);
        transform-origin: bottom center;
        border-radius: 3px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.2);
    }
    .timeline-label-frame {
        position: absolute;
        top: 15px;
        left: -40px;
        width: 80px;
        height: 50px;
        transform: translateX(-50%) rotate(35deg);
        cursor: pointer;
        z-index: 5;
    }
    .timeline-label {
        position: absolute;
        top: 10px;
        left: 50%;
        transform: translateX(-50%);
        font-size: 13px;
        color: #333;
        white-space: nowrap;
        text-align: center;
        background: rgba(255,255,255,0.8);
        padding: 4px 8px;
        border-radius: 6px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.15);
        transition: all 0.3s ease;
    }
    .timeline-label-frame:hover .timeline-label {
        background: rgba(255,255,255,0.95);
        box-shadow: 0 4px 12px rgba(0,0,0,0.25);
        z-index: 1000;
    }
    .timeline-label-frame:hover .timeline-label strong { font-size: 18px; }
    .timeline-label-frame:hover .timeline-label span { font-size: 20px; font-weight: bold; color: #1a1a1a; }
    .timeline-label-frame:hover .timeline-title { display: block; }
    .timeline-label strong { 
        font-family: 'Helvetica', 'Arial', sans-serif; 
        font-weight: 900; 
        font-size: 15px; 
        color: #1a1a1a; 
    }
    .timeline-label span { 
        font-family: 'Georgia', 'Times New Roman', serif; 
        color: #444; 
    }
    .timeline-title {
        display: none;
        font-size: 14px;
        font-weight: bold;
        color: #1a1a1a;
        margin-top: 8px;
        white-space: normal;
        max-width: 200px;
    }
</style>
"""
st.markdown(css, unsafe_allow_html=True)

# ── Edit controls — only shown/enabled when logged in ────────────────────
if is_logged_in():
    #st.markdown("---")
    #st.subheader("Edit Controls")

    # Optional: restrict to specific emails
    allowed = ["your.email@gmail.com", "family@gmail.com"]
    #if st.session_state.auth["user_info"].get("email") not in allowed:
    blocked = ["your.email@gmail.com", "family@gmail.com"]
    if st.session_state.auth["user_info"].get("email") in blocked:
            st.warning("Account is blocked.  No edit permission for this account.")
    # else:
    #     if st.button("➕ Add new memory", type="primary"):
    #         st.session_state["adding_memory"] = True
    #         st.rerun()

    st.sidebar.title(f"🌍 Welcome {st.session_state.name}🌍")
else:
    #st.title("🌍 Welcome Visitors 🌍")
    st.sidebar.title("🌍 Welcome Visitors 🌍")
    #st.sidebar.markdown(f"<a href = \"login-session\">Signed in to create/edit your journeies")
    st.sidebar.markdown('👤 [**Sign in with Google to add/edit Journeies**](#login-section)', unsafe_allow_html=True)
    #if not st.session_state.auth.get("is_logged_in", False):
    #st.markdown('👤 [**Sign in with Google to edit**](#login-section)', unsafe_allow_html=True)

#========================== Login Session ===========================
full_title = f"🌍 Journey ({display_name}) has {event_count} {place_text} {timeline_info}"
# ==================== CENTER ON MARKER CONTROL ====================
if data["events"]:
    sorted_events = sorted(data["events"], key=lambda x: x["date"])
    col_title, col_reset, col_btn, col_num= st.columns([10, 1, 1, 1])
    #col1, col2 = st.columns([3, 1])
    with col_title:
        st.title(full_title)
    with col_reset:
        if st.button("Full View"):
            st.session_state.map_center = [20, 0]
            st.session_state.map_zoom = 4
            st.session_state.force_map_refresh += 1
            st.session_state.reset = True
            st.rerun()

    logger.info(f"DEBUG: event_cout {event_count}")
    if event_count >= 15:
        with col_num:
            marker_id = st.number_input("Go to marker ID", min_value=1, max_value=len(sorted_events), value=1, step=1, label_visibility="collapsed")
        with col_btn:
            if st.button("Marker =>"):
                if 1 <= marker_id <= len(sorted_events):
                    st.session_state.goto_marker = True
                    idx = marker_id - 1
                    event = sorted_events[idx]
                    lat = event["location"]["latitude"]
                    lon = event["location"]["longitude"]
                    st.session_state.map_center = [lat, lon]
                    st.session_state.map_zoom = 12  # Adjust zoom level as needed
                    st.session_state.force_map_refresh += 1
                    # Debug: show current target values
                    #logger.info(f"DEBUG: Current Marker ID idx = {st.session_state.get("idx")}")
                    #logger.info(f"DEBUG: Current Marker ID id  = {event["id"]}")
                    #logger.info(f"DEBUG: Current Marker ID = {event["title"]}")
                    #logger.info(f"DEBUG: Current map_center in session_state = {st.session_state.get("map_center")}")
                    #logger.info(f"DEBUG: Current map_zoom   in session_state = {st.session_state.get("map_zoom")}")
                    ## st.rerun()
                else:
                    st.error("Invalid marker ID")

    # =================== use .fit_bound approach ==== save for future improvements
    # if event_count >= 15:
    #     with col_num:
    #         marker_id = st.number_input(
    #             "Go to marker ID",
    #             min_value=1,
    #             max_value=len(sorted_events),
    #             value=1,
    #             step=1,
    #             label_visibility="collapsed"
    #         )
    #
    #     with col_btn:
    #         if st.button("Marker =>"):
    #             if 1 <= marker_id <= len(sorted_events):
    #                 idx = marker_id - 1
    #                 event = sorted_events[idx]
    #
    #                 try:
    #                     lat = float(event["location"]["latitude"])
    #                     lon = float(event["location"]["longitude"])
    #
    #                     # Small town-scale bounding box around the marker
    #                     # ~0.08° padding ≈ 8–9 km radius — good for town overview
    #                     pad = 0.08
    #                     bounds = [
    #                         [lat - pad, lon - pad],
    #                         [lat + pad, lon + pad]
    #                     ]
    #
    #                     # Apply fit_bounds to the current map
    #                     main_map.fit_bounds(
    #                         bounds,
    #                         padding=(50, 70)  # slightly more bottom space
    #                     )
    #
    #                     # Lock zoom to town scale
    #                     main_map.options["minZoom"] = 11  # city/town overview
    #                     main_map.options["maxZoom"] = 14  # neighborhood level
    #
    #                     # Force redraw
    #                     st.session_state.force_map_refresh += 1
    #
    #                     st.success(
    #                         f"Jumped to marker {marker_id}: {event['title']} ({event['date']})"
    #                     )
    #
    #                     # Optional debug (uncomment if needed)
    #                     # logger.info(f"DEBUG: Jump to marker {marker_id} | lat/lon = {lat}, {lon}")
    #                     # logger.info(f"DEBUG: Applied bounds = {bounds}")
    #
    #                     st.rerun()
    #
    #                 except (KeyError, ValueError, TypeError) as e:
    #                     st.error(f"Cannot jump to marker: invalid location ({e})")
    #
    #             else:
    #                 st.error("Invalid marker ID")

# ==================== TIMELINE BAR ON TOP ====================
if data["events"]:
    sorted_events = sorted(data["events"], key=lambda x: x["date"])
    dates = [datetime.strptime(e["date"], "%Y-%m-%d") for e in sorted_events]

    if dates:
        #min_date = min(dates) - timedelta(days=365 * 2)
        #max_date = max(dates) + timedelta(days=365 * 5)
        #total_span = (max_date - min_date).days or 1

        min_event = min(dates)
        max_event = max(dates)
        span_days = (max_event - min_event).days
        span_days = max(span_days, 1)

        padding_days = max(int(span_days * 0.05), 30)  # 5% or at least 60 days

        min_date = min_event - timedelta(days=padding_days)
        max_date = max_event + timedelta(days=padding_days)
        total_span = (max_date - min_date).days or 1

        st.markdown("<div class='timeline-container'>", unsafe_allow_html=True)

        timeline_html = '<div class="timeline-bar">'

        for idx, (event, dt) in enumerate(zip(sorted_events, dates), start=1):
            position = ((dt - min_date).days / total_span) * 100
            escaped_title = html.escape(event.get('title', 'Untitled'))
            escaped_desc  = html.escape(event.get('description', 'Description:'))
            timeline_html += f'<div class="timeline-tick" style="left: {position}%;"></div>'
            timeline_html += f'''
            <div class="timeline-label-frame" style="left: {position}%;">
                <div class="timeline-label">
                    <strong>{idx}.</strong> <span>{event["date"]}</span>
                    <div class="timeline-title">{escaped_title}</div>
                    <div class="timeline-title">{escaped_desc}</div>
                </div>
            </div>
            '''


        timeline_html += '</div>'
        st.markdown(timeline_html, unsafe_allow_html=True)

        years_span = (max(dates) - min(dates)).days // 365
        #st.caption(f"Events span ~{years_span} years • Hover on label frame to show memory title")

        st.markdown("</div>", unsafe_allow_html=True)
else:
    #st.info("Add memories to see the extended timeline.")
    pass

    #st.write("DEBUG: bf create_map Current map_center in session_state =", st.session_state.get("map_center"))
    #st.write("DEBUG: bf create_map Current map_zoom   in session_state =", st.session_state.get("map_zoom"))
    #st.write("DEBUG: bf create_map force_map_refresh counter =", st.session_state.force_map_refresh)

# Conditionally pass center/zoom only if not default (allows fit_bounds to take effect initially)
center = st.session_state.map_center if st.session_state.map_center != [20, 0] else None
zoom = st.session_state.map_zoom if st.session_state.map_zoom != 2 else None

# ==================== MAP ====================
map_key = f"main_map_{st.session_state.force_map_refresh}"
main_map = create_map()

if not st.session_state.goto_marker:
    coordinates = []
    for event in st.session_state.data.get("events", []):
        try:
            lat = float(event["location"]["latitude"])
            lon = float(event["location"]["longitude"])
            coordinates.append([lat, lon])
        except (KeyError, ValueError, TypeError):
            continue
    # ======================== calculate center and zoom ===============
    if coordinates:
        # coordinates = [[lat1, lon1], [lat2, lon2], ...]
        min_lat = min(lat for lat, lon in coordinates)
        max_lat = max(lat for lat, lon in coordinates)
        min_lon = min(lon for lat, lon in coordinates)
        max_lon = max(lon for lat, lon in coordinates)

        # Optional: add padding in degrees (very effective for long journeys)
        lat_span = max_lat - min_lat
        lon_span = max_lon - min_lon
        pad_lat = max(0.5, lat_span * 0.15)   # 15% extra or at least 0.5°
        pad_lon = max(0.5, lon_span * 0.15)

        bounds = [
            [min_lat - pad_lat, min_lon - pad_lon],
            [max_lat + pad_lat, max_lon + pad_lon]
        ]

        main_map.fit_bounds(bounds, padding=(60, 100))   # pixel padding — bottom heavier for vertical trips

        # Optional safety limits
        main_map.options["minZoom"] = 2
        main_map.options["maxZoom"] = 14

map_data = st_folium(
    main_map,
    key=map_key,
    center=st.session_state.map_center,
    zoom=st.session_state.map_zoom,
    width=None,
    height= 1200,
    use_container_width=True,
    returned_objects=["last_clicked"]
    #returned_objects = ["last_clicked", "center", "zoom"]
)

click = map_data["last_clicked"]


if map_data is not None and map_data.get("last_clicked"):
    new_lat = round(click["lat"], 6)
    new_lon = round(click["lng"], 6)

# Update the live values that the number inputs will read from
    st.session_state.edit_lat = new_lat
    st.session_state.edit_lon = new_lon

full_title = f"🌍 Journey ({display_name}) has {event_count} {place_text} {timeline_info}"


#if st.session_state.auth.get("is_logged_in") and map_data and map_data.get("last_clicked"):
if is_logged_in() and map_data and map_data.get("last_clicked"):
    st.session_state.add_new_memory = True
    pass
    # for display status purpose (not clean code)
else:
    #if map_data and map_data.get("last_clicked") and not is_edit_mode:
    if map_data and map_data.get("last_clicked"):
            st.sidebar.info("🔒 In **View Mode** — map clicks are disabled. Switch to **Edit Mode** to add memories.")

if map_data and map_data.get("center"):
    st.session_state.map_center = [map_data["center"]["lat"], map_data["center"]["lng"]]
    st.session_state.map_zoom = map_data.get("zoom", 2)

# ==================== ADD NEW MEMORY ====================
if st.session_state.editing_event_id and st.session_state.add_new_memory:
    st.session_state.add_new_memory = False  # editing takes priority
# todo
#if st.session_state.auth.get("is_logged_in", False):
#    st.session_state.add_new_memory = True

logger.info(f"add new marker ----------->  {st.session_state.editing_event_id}   {st.session_state.add_new_memory}   {st.session_state.add_new_memory}")
#if st.session_state.app_mode == "Edit Mode" and map_data and map_data.get("last_clicked"):
if not st.session_state.current_journey_locked and st.session_state.add_new_memory and map_data and map_data.get("last_clicked"):
    click = map_data["last_clicked"]
    lat, lon = round(click["lat"], 6), round(click["lng"], 6)
    default_name = f"{lat:.5f}, {lon:.5f}"

    st.sidebar.header("➕ Add New Memory")
    with st.sidebar.form("add_form", clear_on_submit=False):
        title = st.text_input("Title*", "")
        date = st.date_input("Date*", datetime.today(),
                             min_value= MIN_DATE,
                             #min_value=datetime(1930, 1, 1).date(),
                             max_value=MAX_DATE)
        loc_name = st.text_input("Location Name*", default_name)
        description = st.text_area("Description")
        photos = st.file_uploader("Photos", accept_multiple_files=True, type=["jpg", "jpeg", "png", "gif", "heic", "HEIC", "heif", "HEIF"])
        videos = st.file_uploader("Videos", accept_multiple_files=True, type=["mp4", "mov", "webm"])

        col_save, col_cancel = st.columns([1, 1])
        with col_save:
            save_clicked = st.form_submit_button("💾 Save Memory")
        with col_cancel:
            cancel_clicked = st.form_submit_button("❌ Cancel", type="secondary")

        if save_clicked:
            if not title.strip():
                st.error("Title required")
            else:
                photo_paths = []
                for up in photos or []:
                    fname = f"{int(time.time())}_{up.name}"
                    # path = UPLOADS_PHOTOS / fname
                    # path.write_bytes(up.getbuffer())
                    # photo_paths.append(str(path))
                    file_bytes = up.getbuffer()

                    #if os.getenv("K_SERVICE1"):
                    if IS_CLOUD:
                        photo_paths.append(upload_to_gcs(file_bytes, f"photos/{fname}", up.type))
                    else:
                        path = UPLOADS_PHOTOS / fname
                        path.write_bytes(file_bytes)
                        # photo_paths.append(str(path))
                        photo_paths.append(to_relative_path(path))

                video_paths = []
                for up in videos or []:
                    fname = f"{int(time.time())}_{up.name}"
                    # path = UPLOADS_VIDEOS / fname
                    # path.write_bytes(up.getbuffer())
                    # video_paths.append(str(path))
                    file_bytes = up.getbuffer()
                    #if os.getenv("K_SERVICE1"):
                    if IS_CLOUD:
                        video_paths.append(upload_to_gcs(file_bytes, f"videos/{fname}", up.type))
                    else:
                        path = UPLOADS_VIDEOS / fname
                        path.write_bytes(file_bytes)
                        #video_paths.append(str(path))
                        video_paths.append(to_relative_path(path))


                new_id = max((e["id"] for e in st.session_state.data["events"]), default=0) + 1
                new_event = {
                    "id": new_id,
                    "title": title,
                    "date": date.strftime("%Y-%m-%d"),
                    "location": {"name": loc_name, "latitude": lat, "longitude": lon},
                    "description": description,
                    "media": {"photos": photo_paths, "videos": video_paths}
                }
                st.session_state.data["events"].append(new_event)
                # todo JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False), encoding="utf-8")
                save_data_to_storage(st.session_state.data)
                st.session_state.force_map_refresh += 1
                st.success("Memory added!")
                st.rerun()
        # === CANCEL BUTTON — OUTSIDE THE FORM ===
        if cancel_clicked:
        #if st.sidebar.button("❌ Cancel Adding Memory", type="secondary"):
            st.session_state.last_map_clicked = None
            st.session_state.show_add_marker_form = False
            st.session_state.current_journey_locked = False
            st.session_state.add_new_memory = False
            st.session_state.force_map_refresh += 1
            st.success("Adding Memory cancelled!")
            st.rerun()  # Clears the form by removing last_clicked state

st.markdown('<div id="login-section"></div>', unsafe_allow_html=True)
if not is_logged_in():
    with st.container():

        result = oauth2.authorize_button(
            name="Sign in with Google to Create and Edit your journeies.",
            redirect_uri=REDIRECT_URI,
            scope=SCOPE,
            key="google_login_btn",
            extras_params={
                "access_type": "offline",
                "prompt": "consent"
            },
            pkce="S256",
            use_container_width=True,
        )
        st.title("👆 Click **Sign in with Google** above to begin (Scroll up if you don't see it) ✨")
        if result and result.get("token"):
            token = result["token"]
            st.session_state.auth["token"] = result["token"]
            st.session_state.auth["user_info"] = result.get("user_info", {})
            st.session_state.auth["is_logged_in"] = True
            st.session_state.current_journey_locked = False
            if token.get("refresh_token"):
                st.session_state.auth["refresh_token"] = token["refresh_token"]
            st.rerun()
else:
    # st.success(f"Logged in as {st.session_state.auth['user_info'].get('name', 'User')}")
    # Optional: extra edit controls here if you want
    # ----------------------------
    # Logged in area
    # ----------------------------
    token = st.session_state.auth["token"]
    # st.subheader("Token payload (for debug)")
    # st.json(token)

    # If you need the email:
    # Google returns an id_token (JWT) in many cases; you can decode it to read claims.
    # NOTE: For production, you should VERIFY the token signature & audience.
    id_token = token.get("id_token")
    if id_token:
        try:
            import jwt  # PyJWT

            claims = jwt.decode(id_token, options={"verify_signature": False})
            #st.subheader("ID Token claims (decoded, NOT verified)")
            #st.json(claims)
            #st.write("Email:", claims.get("email"))
            #st.write("Name :", claims.get("name"))
            st.session_state.email = claims.get("email")
            st.session_state.name = claims.get("name")

            if "visitor_logged_this_session" not in st.session_state:
                visitor_msg = (
                    f"Visitor access | "
                    f"location: Portland, Oregon, US | "
                    f"device: {st.session_state.get('device_type', 'unknown')} | "
                    f"journey: {st.session_state.selected_json_file}"
                )
                append_to_log(visitor_msg, message_type="visitor_access", throttle=True)
                st.session_state.visitor_logged_this_session = True

                # ── After successful login ───────────────────────────────────────
            if "user_login_logged_this_session" not in st.session_state:
                user_msg = (
                    f"User login | "
                    f"Name: {st.session_state.get('name', 'Unknown')} | "
                    f"Email: {st.session_state.get('email', 'unknown')} | "
                    f"location: Portland, Oregon, US | "
                    f"device: {st.session_state.get('device_type', 'unknown')} | "
                    f"journey: {st.session_state.selected_json_file}"
                )
                append_to_log(user_msg, message_type=f"user_login_{st.session_state.get('email', 'unknown')}",
                              throttle=True)
                st.session_state.user_login_logged_this_session = True


        except Exception as e:
            st.warning(f"Couldn't decode id_token: {e}")
    else:
        st.info("No id_token found in token response (depends on Google response/scopes).")

    # Refresh token
    # if st.button("Refresh token"):
    #     token = oauth2.refresh_token(token)
    #     st.session_state["token"] = token
    #     st.rerun()

    #cols = st.columns([1, 4])
    #with cols[1]:
    if st.button("Sign out", type="secondary", use_container_width=True):
        st.session_state.auth = {"token": None, "user_info": None, "is_logged_in": False}
        st.session_state.current_journey_locked = True
        log_msg = f"User logout | Name: {st.session_state.get('name', 'Unknown')} | Email: {st.session_state.get('email', 'unknown')}"
        append_to_log(log_msg, message_type="user_logout", throttle=False)  # no throttle on logout
        st.rerun()

# ==================== SIDEBAR SUMMARY WITH EDIT AND DELETE BUTTONS ====================
st.sidebar.subheader("✨ Journey Operations")
logger.info(f" locked {st.session_state.current_journey_locked}")
if not st.session_state.current_journey_locked:
    # ==================== CREATE NEW JOURNEY ====================
    #st.sidebar.markdown("---")
    with st.sidebar.expander("➕ Create a Journey", expanded=False):
        st.write("Enter a name for your new journey. It will start empty.")

        new_journey_name = st.text_input(
            "Journey Name*",
            placeholder="e.g., My 2026 Adventures",
            help="Use letters, numbers, spaces, or hyphens. The file will be saved as a .json."
        )

        if new_journey_name:
            # Clean the input to make a safe filename
            clean_name = (
                new_journey_name.strip()
                .lower()
                .replace(" ", "-")
                .replace("_", "-")
                .replace("/", "")
                .replace("\\", "")
            )
            if not clean_name:
                st.error("Please enter a valid name.")
            else:
                new_filename = f"{clean_name}.json"
                new_file_path = BASE_DIR / new_filename

                if new_file_path.exists():
                    st.warning(f"A journey named **{new_filename}** already exists. Choose a different name.")
                else:
                    col_create, col_cancel = st.columns(2)
                    with col_create:
                        if st.button("✅ Create Journey", type="primary", use_container_width=True):
                            try:
                                # Default JSON structure
                                default_data = {
                                    "autobiography": {
                                        "title": new_journey_name,
                                        "author": "Your Name",
                                        "created_date": datetime.now().strftime("%Y-%m-%d"),
                                        "last_updated": datetime.now().strftime("%Y-%m-%d")
                                    },
                                    "events": []
                                }

                                # Write the new JSON file
                                new_file_path.write_text(
                                    json.dumps(default_data, indent=4, ensure_ascii=False),
                                    encoding="utf-8"
                                )

                                # Switch to the new journey
                                st.session_state.selected_json_file = new_filename
                                save_data_to_storage(st.session_state.data)
                                # todo JSON_FILE.write_text(json.dumps(default_data, indent=4, ensure_ascii=False),
                                #                     encoding="utf-8")

                                # Clear cache and reset state
                                st.cache_data.clear()
                                if "data" in st.session_state:
                                    del st.session_state["data"]
                                if "editing_event_id" in st.session_state:
                                    del st.session_state["editing_event_id"]
                                keys_to_reset = ["map_center", "map_zoom", "force_map_refresh"]
                                for k in keys_to_reset:
                                    if k in st.session_state:
                                        del st.session_state[k]

                                st.success(f"✅ Created and switched to: **{new_journey_name}** (0 places)")
                                log_msg = f"Create Journey•| {get_audit_actor_info()}"
                                append_to_log(log_msg, message_type="user_login",
                                              throttle=False)  # no throttle on logout
                                st.rerun()

                            except Exception as e:
                                st.error(f"Failed to create journey: {e}")
                    st.session_state.current_journey_locked = is_journey_locked(new_journey_name)

                    with col_cancel:
                        if st.button("❌ Cancel", type="secondary", use_container_width=True):
                            st.rerun()

    # ==================== RENAME JOURNEY (FIXED + ALWAYS SHOW BUTTON) ====================
    with st.sidebar.expander("✏️ Rename Journey", expanded=False):
        st.write("Change the name of an existing journey. This renames the file and updates the title.")

        available_journeys = get_local_json_files()
        if not available_journeys:
            st.info("No journeys available to rename.")
        else:
            journey_to_rename = st.selectbox(
                "Select journey to rename",
                options=available_journeys,
                index=available_journeys.index(st.session_state.selected_json_file)
                if st.session_state.selected_json_file in available_journeys else 0,
                help="Choose the journey you want to rename",
                key="rename_select"
            )

            blob_or_path = get_json_path(journey_to_rename) if IS_CLOUD else str(BASE_DIR / journey_to_rename)

            try:
                current_data = load_data_from_file(blob_or_path)
                current_title = current_data.get("autobiography", {}).get(
                    "title", journey_to_rename.replace(".json", "")
                )
                event_count = len(current_data.get("events", []))
                st.info(f"**Current:** {current_title} • {event_count} memories • File: `{journey_to_rename}`")
            except Exception as e:
                st.error(f"Could not load journey data: {e}")
                current_data = None
                current_title = journey_to_rename.replace(".json", "")
            # Use a form so the submit button is stable and always visible
            with st.form("rename_form", clear_on_submit=False):
                default_new_nme = f"{current_title} new_name"
                new_journey_name = st.text_input(
                    "New Journey Name*",
                    value=default_new_nme,
                    placeholder="e.g., Europe Adventure 2025",
                    help="This will become the new display title and filename",
                    key="rename_new_title"
                )

                # Prepare validation
                name_ok = bool(new_journey_name and new_journey_name.strip())
                same_as_current = (new_journey_name.strip() == current_title.strip()) if name_ok else True

                clean_name = (
                    new_journey_name.strip()
                    .lower()
                    .replace(" ", "-")
                    .replace("_", "-")
                    .replace("/", "")
                    .replace("\\", "")
                    .replace(".", "")
                ) if name_ok else ""

                clean_ok = bool(clean_name)
                new_filename = f"{clean_name}.json" if clean_ok else ""
                exists_conflict = (new_filename in available_journeys) if new_filename else False

                # Always show the button; disable if not valid
                can_rename = (current_data is not None) and name_ok and (not same_as_current) and clean_ok and (
                    not exists_conflict)

                submitted = st.form_submit_button("💾 Save (Rename Journey)", type="primary", disabled=not can_rename)

            # Show guidance messages (outside form so it doesn’t break button rendering)
            if name_ok and same_as_current:
                st.caption("New name is the same as current — nothing to do.")
            elif name_ok and clean_ok and exists_conflict:
                st.warning(f"A journey named **{new_filename}** already exists. Choose a different name.")
            elif name_ok and not clean_ok:
                st.error("Invalid name – please use letters, numbers, spaces, or hyphens.")

            # Execute rename only after submit
            if submitted and can_rename:
                try:
                    current_data["autobiography"]["title"] = new_journey_name.strip()
                    current_data["autobiography"]["last_updated"] = datetime.now().strftime("%Y-%m-%d")

                    json_text = json.dumps(current_data, indent=4, ensure_ascii=False)

                    if IS_CLOUD:
                        upload_to_gcs(json_text.encode("utf-8"), get_json_path(new_filename), "application/json")
                        bucket.blob(get_json_path(journey_to_rename)).delete()
                        st.success(f"✅ Journey renamed to **{new_journey_name}** in cloud!")
                    else:
                        (BASE_DIR / new_filename).write_text(json_text, encoding="utf-8")
                        (BASE_DIR / journey_to_rename).unlink(missing_ok=True)
                        st.success(f"✅ Journey renamed to **{new_journey_name}** locally!")

                    # Update selected journey only AFTER success
                    if journey_to_rename == st.session_state.selected_json_file:
                        st.session_state.selected_json_file = new_filename

                    st.cache_data.clear()
                    st.session_state.pop("data", None)
                    log_msg = f"Rename Journey• | {get_audit_actor_info()}"
                    append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout
                    st.rerun()

                except Exception as e:
                    st.error(f"Rename failed: {e}")
                    logger.error(f"Rename error: {e}")

# ==================== DOWNLOAD JOURNEY BACKUP (SELECT ANY JOURNEY) ====================
with st.sidebar.expander("📥 Download Journey", expanded=False):
    st.write("Select any journey and download its complete JSON backup for safekeeping or sharing.")

    available_journeys = get_local_json_files()

    if not available_journeys:
        st.info("No journeys available to download.")
    else:
        # Dropdown to select which journey to download
        journey_to_download = st.selectbox(
            "Choose a journey to backup",
            options=available_journeys,
            format_func=lambda x: x.replace(".json", "").replace("_", " ").replace("-", " ").title(),
            help="All journeys are listed, including the current one"
        )

        # Load the selected journey data safely
        try:
            blob_or_path = get_json_path(journey_to_download) if IS_CLOUD else str(BASE_DIR / journey_to_download)
            if IS_CLOUD:
                json_bytes = download_from_gcs(get_json_path(journey_to_download))
            else:
                json_bytes = Path(blob_or_path).read_bytes()

            # Load metadata for nice display
            temp_data = json.loads(json_bytes.decode("utf-8"))
            title = temp_data.get("autobiography", {}).get("title", journey_to_download.replace(".json", ""))
            title_display = " ".join(word.capitalize() for word in title.replace("-", " ").replace("_", " ").split())
            event_count = len(temp_data.get("events", []))

            # Show info
            is_current = journey_to_download == st.session_state.selected_json_file
            current_label = " (current)" if is_current else ""
            st.markdown(f"**{title_display}{current_label}**")
            st.caption(f"{event_count} memor{'y' if event_count == 1 else 'ies'} • File: `{journey_to_download}`")

            # Generate timestamped filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            base_name = journey_to_download.replace(".json", "")
            backup_filename = f"{base_name}_backup_{timestamp}.json"

            # Download button
            st.download_button(
                label="📥 Download Backup Now",
                data=json_bytes,
                file_name=backup_filename,
                mime="application/json",
                use_container_width=True,
                key=f"download_backup_{journey_to_download}"
            )

            #if st.download_button(): # TODO
            #    log_msg = f"Download Journey• event=new_042 • title=\"Birthday 2026\" | {get_audit_actor_info()}"
            #    append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout

        except Exception as e:
            st.error("Could not load journey data for download.")
            logger.error(f"Failed to prepare download for {journey_to_download}: {e}")

# ==================== UPLOAD & RESTORE JSON (only when current journey is unlocked) ====================
with st.sidebar.expander("📤 Upload Journey", expanded=False):
    if st.session_state.current_journey_locked:
        st.info("🔒 Restore is disabled — the **current** journey is locked (view-only).")
        st.caption("Switch to an unlocked journey to use this feature.")
    else:
        st.write("Restore a previously backed-up `.json` file. This will **replace** an existing journey or create it if missing.")

        uploaded_file = st.file_uploader(
            "Select a backup JSON file to restore",
            type=["json"],
            key="json_restore_uploader"
        )

        if uploaded_file is not None:
            try:
                uploaded_bytes = uploaded_file.read()
                uploaded_data = json.loads(uploaded_bytes.decode("utf-8"))

                if not all(key in uploaded_data for key in ["autobiography", "events"]):
                    st.error("Invalid backup: missing 'autobiography' or 'events' section.")
                elif not isinstance(uploaded_data["events"], list):
                    st.error("Invalid backup: 'events' must be a list.")
                else:
                    # ── Determine target filename ───────────────────────────────
                    restore_filename = uploaded_file.name.strip()
                    if not restore_filename.lower().endswith(".json"):
                        restore_filename += ".json"

                    title = uploaded_data["autobiography"].get("title", restore_filename.replace(".json", ""))
                    event_count = len(uploaded_data["events"])

                    st.success(f"Valid backup: **{uploaded_file.name}** — {title} ({event_count} memories)")

                    # ── Check if target journey already exists and is locked ────
                    target_locked = is_journey_locked(restore_filename)

                    if target_locked:
                        st.error(
                            f"**Cannot restore:** The journey `{restore_filename}` is **locked** (view-only).\n\n"
                            "Restore would overwrite a read-only journey — this is not allowed.\n"
                            "• Unlock it first (remove the `.json_lock` file), or\n"
                            "• Rename your backup file and try again."
                        )
                    else:
                        # Exists but unlocked, or doesn't exist → safe to proceed
                        exists = restore_filename in get_local_json_files()
                        if exists:
                            st.warning(
                                f"⚠️ This will **replace** the existing journey `{restore_filename}` "
                                f"({event_count} memories will be overwritten)."
                            )
                        else:
                            st.info(f"New journey `{restore_filename}` will be created.")

                        col1, col2 = st.columns(2)
                        with col1:
                            if st.button("✅ Yes, Restore Now", type="primary", use_container_width=True):
                                try:
                                    log_msg = f"Upload Journey• | {get_audit_actor_info()}"
                                    append_to_log(log_msg, message_type="user_login",
                                                  throttle=False)  # no throttle on logout
                                    blob_name = get_json_path(restore_filename)

                                    if IS_CLOUD:
                                        upload_to_gcs(uploaded_bytes, blob_name, "application/json")
                                        st.success(f"✅ Restored **{title}** to cloud storage!")
                                    else:
                                        (BASE_DIR / restore_filename).write_bytes(uploaded_bytes)
                                        st.success(f"✅ Restored **{title}** locally!")

                                    # Switch to the restored journey
                                    st.session_state.selected_json_file = restore_filename
                                    st.session_state.current_journey_locked = is_journey_locked(restore_filename)

                                    # Full reload
                                    st.cache_data.clear()
                                    if "data" in st.session_state:
                                        del st.session_state["data"]
                                    st.session_state.force_map_refresh += 1

                                    st.rerun()

                                except Exception as e:
                                    st.error(f"Restore failed: {e}")
                                    logger.error(f"Restore error: {e}")

                        with col2:
                            if st.button("❌ Cancel", type="secondary", use_container_width=True, key="restore_cancel_button"):
                                st.info("Restore cancelled.")

            except json.JSONDecodeError:
                st.error("Invalid JSON file — could not parse.")
            except Exception as e:
                st.error(f"Error reading file: {e}")

def log_kml_download():
    log_msg = f"Download Package (JSON + Media)• | {get_audit_actor_info()}"
    append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout
    st.session_state.kml_just_downloaded = True

if "kml_just_downloaded" not in st.session_state:
    st.session_state.kml_just_downloaded = False

# ==================== DOWNLOAD JOURNEY AS KML (SELECT ANY JOURNEY) ====================
with st.sidebar.expander("🌍 Export to Google Map/Earth", expanded=False):
    #st.write("Select any journey and download it as a KML file for Google My Maps or Google Earth.")
    #available_journeys = get_local_json_files()
    logger.info(f"About to export — passing {len(temp_data['events'])} events")
    # kml_bytes = export_to_kml_bytes(temp_data["events"])
    if not available_journeys:
        st.info("No journeys available to download.")
    else:
        # Dropdown to select which journey to export as KML
        journey_to_kml = st.selectbox(
            "Choose a journey to export as KML",
            options=available_journeys,
            format_func=lambda x: x.replace(".json", "").replace("_", " ").replace("-", " ").title(),
            help="All journeys are listed, including the current one",
            key="kml_select_journey"  # unique key so it doesn't conflict with others
        )

        if journey_to_kml:
            kml_bytes = export_to_kml_bytes(temp_data["events"])
            # Load the selected journey data safely
            try:
                blob_or_path = get_json_path(journey_to_kml) if IS_CLOUD else str(BASE_DIR / journey_to_kml)
                temp_data = load_data_from_file(blob_or_path)
                logger.info(f"Loaded data for KML — keys: {list(temp_data.keys())}")
                logger.info(f"Events count in loaded data: {len(temp_data.get('events', []))}")
                # Optional: print first event if exists
                if temp_data.get("events"):
                    logger.info(f"First event sample: {temp_data['events'][0]}")
                title = temp_data.get("autobiography", {}).get("title", journey_to_kml.replace(".json", ""))
                title_display = " ".join(
                    word.capitalize() for word in title.replace("-", " ").replace("_", " ").split())
                event_count = len(temp_data.get("events", []))

                is_current = journey_to_kml == st.session_state.selected_json_file
                current_label = " (current)" if is_current else ""
                st.markdown(f"**{title_display}{current_label}**")
                st.caption(f"{event_count} memor{'y' if event_count == 1 else 'ies'} will be exported from `{journey_to_kml}`")
                # st.caption(f"Will export **{event_count}** location{'s' if event_count != 1 else ''}")

                if event_count == 0:
                    st.info("This journey has no memories yet — KML will be empty.")
                else:
                    #log_msg = f"Export to Google Map/Earch• event=new_042 • title=\"Birthday 2026\" | {get_audit_actor_info()}"
                    #append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout
                    # Generate filename based on **selected** journey
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
                    base_name = journey_to_kml.replace(".json", "")
                    kml_filename = f"{base_name}_journey_{timestamp}.kml"
                    logger.info(f"klm_filename {kml_filename}")
                    # st.download_button(
                    #     label="⬇️ Download KML Now",
                    #     #data=f,
                    #     data=kml_bytes,
                    #     file_name=kml_filename,  # ← now uses selected journey name
                    #     mime="application/vnd.google-earth.kml+xml",
                    #     use_container_width=True,
                    #     key=f"download_kml_{journey_to_kml}_{timestamp}"  # unique per selection + time
                    # )
                    # Create KML file (using your existing function)
                    export_to_kml(temp_data["events"], kml_filename)

                    # Read the file for download
                    with open(kml_filename, "rb") as f:
                        file_contenet = f.read()
                        st.download_button(
                            label="⬇️ Download KML Now",
                            data=file_contenet,
                            # data=kml_bytes,
                            file_name=kml_filename,  # ← now uses selected journey name
                            mime="application/vnd.google-earth.kml+xml",
                            use_container_width=True,
                            key=f"download_kml_{journey_to_kml}_{timestamp}",  # unique per selection + time
                            on_click=log_kml_download
                        )
                        if st.session_state.kml_just_downloaded: # TODO
                            log_msg = f"Export to KML | {get_audit_actor_info()}"
                            append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout
                    st.markdown("""
                    **How to open:**
                    1. Go to https://www.google.com/mymaps
                    2. Create a new map
                    3. Click **Import** → choose the downloaded .kml file
                    4. See your journey with points and connecting path!
                    """)

            except Exception as e:
                st.error(f"Could not load or export {journey_to_kml}: {str(e)}")
                logger.error(f"KML export error for {journey_to_kml}: {e}")

# ==================== DOWNLOAD JSON's MEDIA FILES ) ====================

with st.sidebar.expander("📦 Download Package (JSON + Media)", expanded=False):
    st.write("Download a single ZIP containing the selected journey JSON **plus all referenced photos/videos**.")

    available_journeys = get_local_json_files()
    if not available_journeys:
        st.info("No journeys available.")
    else:
        journey_pkg = st.selectbox(
            "Choose a journey to package",
            options=available_journeys,
            format_func=lambda x: x.replace(".json", "").replace("_", " ").replace("-", " ").title(),
            key="media_download_select"
        )

        if journey_pkg:
            try:
                zip_bytes = zip_journey_package(journey_pkg)

                ts = datetime.now().strftime("%Y%m%d_%H%M")
                base = journey_pkg.replace(".json", "")
                zip_name = f"{base}_package_{ts}.zip"

                st.download_button(
                    "⬇️ Download Package ZIP",
                    data=zip_bytes,
                    file_name=zip_name,
                    mime="application/zip",
                    use_container_width=True,
                    key=f"download_media_{journey_pkg}_{ts}"
                )
                #if st.download_button(): # TODO
                #    log_msg = f"Download Package (JSON + Media)• event=new_042 • title=\"Birthday 2026\" | {get_audit_actor_info()}"
                #    append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout

                st.caption("Includes: journeys/<json>, media/photos/*, media/videos/*, manifest.json")
            except Exception as e:
                st.error(f"Failed to build package: {e}")

with st.sidebar.expander("📦 Upload Package (JSON + Media)", expanded=False):
    if st.session_state.current_journey_locked:
        st.info("🔒 Restore is disabled — the **current** journey is locked (view-only).")
        st.caption("Switch to an unlocked journey to use this feature.")
    else:
        st.write("Upload a package ZIP to restore the journey JSON and **re-upload all media**.")

        # init guard state
        st.session_state.setdefault("pkg_last_fingerprint", None)
        st.session_state.setdefault("pkg_restored_ok", False)

        pkg_up = st.file_uploader(
            "Select a journey package ZIP",
            type=["zip"],
            key="pkg_restore_uploader"
        )

        if pkg_up is not None:
            fp = (pkg_up.name, getattr(pkg_up, "size", None))

            col1, col2 = st.columns([1, 1])
            do_restore = col1.button("✅ Restore package", use_container_width=True)
            col2.button("🧹 Reset uploader", use_container_width=True, on_click=lambda: st.session_state.pop("pkg_restore_uploader", None))

            if do_restore:
                if st.session_state.pkg_last_fingerprint == fp and st.session_state.pkg_restored_ok:
                    st.info("This package has already been restored in this session.")
                else:
                    try:
                        log_msg = f"Upload Package (JSON + Media)• | {get_audit_actor_info()}"
                        append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout
                        zip_bytes = pkg_up.read()
                        restored_name, restored_data = restore_journey_package(zip_bytes)

                        title = restored_data.get("autobiography", {}).get("title", restored_name.replace(".json", ""))
                        count = len(restored_data.get("events", []))

                        st.success(f"✅ Restored: **{title}** ({count} memories) → `{restored_name}`")

                        # mark processed BEFORE rerun
                        st.session_state.pkg_last_fingerprint = fp
                        st.session_state.pkg_restored_ok = True

                        # switch to restored journey
                        st.session_state.selected_json_file = restored_name
                        st.session_state.current_journey_locked = is_journey_locked(restored_name)

                        st.cache_data.clear()
                        st.session_state.pop("data", None)

                        # Important: only one of these is needed; keep force_map_refresh if your map uses it
                        st.session_state.force_map_refresh += 1

                        st.rerun()
                    except Exception as e:
                        st.session_state.pkg_restored_ok = False
                        st.error(f"Restore failed: {e}")

# ==================== DELETE JOURNEY FILE (GCS + Local Compatible) ====================
if not st.session_state.current_journey_locked:
    with st.sidebar.expander("🗑️ Delete Journey", expanded=False):
        st.warning("⚠️ This will **permanently delete** a journey file and all its photos/videos.")

        # Get current list of journeys (from GCS or local)
        available_journeys = get_local_json_files()
        available_for_deletion = [
            f for f in available_journeys
            if f != st.session_state.selected_json_file
        ]

        if not available_for_deletion:
            st.info("No other journey files available to delete.")
        elif is_journey_locked(st.session_state.selected_json_file):
            st.info("The Journey is locked and can't be deleted.")
        else:
            file_to_delete = st.selectbox(
                "Select a journey to delete",
                options=available_for_deletion,
                help="Only inactive journeys can be deleted"
            )

            # Load preview data
            blob_or_path = get_json_path(file_to_delete) if IS_CLOUD else str(BASE_DIR / file_to_delete)
            try:
                preview_data = load_data_from_file(blob_or_path)
                event_count = len(preview_data.get("events", []))
                title = preview_data.get("autobiography", {}).get("title", file_to_delete.replace(".json", ""))
                st.write(f"**{title}** • {event_count} memories • File: `{file_to_delete}`")
            except:
                st.write(f"File: `{file_to_delete}` (preview unavailable)")

            col_confirm, col_cancel = st.columns(2)

            with col_confirm:
                if st.button("🗑️ Delete Permanently", type="primary", use_container_width=True):
                    try:
                        # 1. Delete all media files (photos + videos)
                        for event in preview_data.get("events", []):
                            for media_type in ["photos", "videos"]:
                                for media_url in event.get("media", {}).get(media_type, []):
                                    try:
                                        if media_url.startswith("gs://"):
                                            # GCS path
                                            parts = media_url[5:].split("/", 1)
                                            bucket_name = parts[0]
                                            blob_path = parts[1]
                                            storage.Client().bucket(bucket_name).blob(blob_path).delete()
                                        else:
                                            # Local path
                                            Path(media_url).unlink(missing_ok=True)
                                    except Exception as e:
                                        logger.warning(f"Failed to delete media {media_url}: {e}")

                        # 2. Delete the journey JSON itself
                        if IS_CLOUD:
                            blob_name = get_json_path(file_to_delete)
                            bucket.blob(blob_name).delete()
                            st.success(f"✅ Journey **{file_to_delete}** deleted permanently from cloud.")
                        else:
                            (BASE_DIR / file_to_delete).unlink()
                            st.success(f"✅ Journey **{file_to_delete}** deleted permanently.")

                        # Refresh journey list
                        log_msg = f"Delete Journey• | {get_audit_actor_info()}"
                        append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout
                        st.rerun()

                    except Exception as e:
                        st.error(f"Failed to delete: {e}")
                        logger.error(f"Delete journey failed: {e}")

            with col_cancel:
                st.button("Cancel", type="secondary", use_container_width=True)

with st.sidebar.expander("🔍 Search Journey ", expanded=False):
    tab_normal, tab_regex = st.tabs(["Normal Search", "Regex (advanced)"])

    with tab_normal:
        normal_search = st.text_input("Keywords (comma/space separated)", key="normal_search")
        if st.button("Search (normal)", key="btn_normal"):
            st.session_state.search_mode = "normal"
            st.session_state.search_value = normal_search
            log_msg = f"Search Journey• | {normal_search} | {get_audit_actor_info()}"
            append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout
            st.rerun()

    with tab_regex:
        regex_pattern = st.text_input("Regular expression", placeholder="Paris|birthday|202[0-5]", key="regex_pattern")
        if st.button("Search (regex)", key="btn_regex"):
            st.session_state.search_mode = "regex"
            st.session_state.search_value = regex_pattern
            log_msg = f"Search Journey• | {regex_pattern} | {get_audit_actor_info()}"
            append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout
            st.rerun()

# st.sidebar.subheader(f"🗺️ Current Journey ({st.session_state.selected_json_file}) has {len(st.session_state.data['events'])} places")
event_count = len(st.session_state.data.get("events", []))
place_text = "memory" if event_count == 1 else "memories"

locked = st.session_state.get("current_journey_locked", False)

lock_emoji = "🔒" if st.session_state.current_journey_locked else "✏️"

locked = st.session_state.current_journey_locked

# ==================== JOURNEY LOCK / UNLOCK STATUS & CONTROLS ====================
if is_logged_in():

    locked = st.session_state.get("current_journey_locked", False)
    if locked:
        st.session_state.add_new_memory = False
        st.sidebar.subheader(f"🗺️ Selected Journey")
        st.sidebar.markdown(f"{st.session_state.selected_json_file} {timeline_info}")
        #st.sidebar.markdown(f"{st.session_state.selected_json_file} has {event_count} {place_text}")
        #st.sidebar.warning("🔒 **Locked** (view-only mode)")
       # st.sidebar.caption("No editing, adding or deleting is allowed in this journey.")

        if st.sidebar.button("🔓 Edit this journey", type="primary", use_container_width=True):
            try:
                if IS_CLOUD:
                    lock_blob_name = f"{JOURNEYS_FOLDER}/{st.session_state.selected_json_file}_lock"
                    bucket.blob(lock_blob_name).delete()
                    logger.info(f"Deleted lock blob: {lock_blob_name}")
                else:
                    lock_path = BASE_DIR / f"{st.session_state.selected_json_file}_lock"
                    if lock_path.exists():
                        lock_path.unlink()
                        logger.info(f"Deleted local lock file: {lock_path}")

                st.session_state.current_journey_locked = False
                st.success("✅ Journey is now **unlocked** and editable again!")
                st.rerun()

            except Exception as e:
                st.error(f"Failed to unlock journey: {e}")
                logger.error(f"Unlock failed: {e}")

    else:
        st.session_state.add_new_memory = True
        st.sidebar.subheader(f"🗺️ Selected Journey (Edit Mode)")
        st.sidebar.markdown(f"{st.session_state.selected_json_file} {timeline_info}")
        #st.sidebar.markdown(f"{st.session_state.selected_json_file} has {event_count} {place_text}")
        #st.sidebar.success("✏️ **Editable**")
        #st.sidebar.caption("You can add, edit and delete memories in this journey.")

        if st.sidebar.button("🔒 Lock this journey", type="secondary", use_container_width=True):
            log_msg = f"Lock Journey• | {get_audit_actor_info()}"
            append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout
            try:
                lock_content = datetime.now().isoformat().encode("utf-8")  # optional timestamp

                if IS_CLOUD:
                    lock_blob_name = f"{JOURNEYS_FOLDER}/{st.session_state.selected_json_file}_lock"
                    bucket.blob(lock_blob_name).upload_from_string(lock_content, content_type="text/plain")
                    logger.info(f"Created lock blob: {lock_blob_name}")
                else:
                    lock_path = BASE_DIR / f"{st.session_state.selected_json_file}_lock"
                    lock_path.write_bytes(lock_content)
                    logger.info(f"Created local lock file: {lock_path}")

                st.session_state.current_journey_locked = True
                st.success("🔒 Journey is now **locked** (view-only)")
                st.rerun()

            except Exception as e:
                st.error(f"Failed to lock journey: {e}")
                logger.error(f"Lock failed: {e}")

else:
    # Not logged in → show minimal / read-only info
    st.sidebar.subheader(f"🗺️ Journey: {st.session_state.selected_json_file} {timeline_info}")
    #st.sidebar.markdown(f"️🗺️ Journey {st.session_state.selected_json_file} has {event_count} {place_text}")
    #st.sidebar.caption("Sign in to edit this journey")

######################## buggy EDITING ######################

sorted_events = sorted(st.session_state.data["events"], key=lambda x: x["date"])

for idx, event in enumerate(sorted_events, start=1):
    expander_key = f"memory_expander_{event['id']}"
    is_editing_this = (st.session_state.get("editing_event_id") == event["id"])

    with st.sidebar.expander(
        f"🔹 {idx}. {event['date']} — {event['title']}",
        expanded=is_editing_this or st.session_state.get(f"force_open_{event['id']}", False)
    ):
        st.caption(f"📍 {event['location']['name']}")

        # Small preview of existing media (optional but nice)
        if event["media"].get("photos") or event["media"].get("videos"):
            cols = st.columns(3)
            for i, p in enumerate(event["media"].get("photos", [])[:3]):
                with cols[i % 3]:
                    try:
                        if isinstance(p, str) and p.startswith("gs://"):
                            b = media_bytes_anywhere(p)  # uses gcs_bytes_from_gs_uri()
                            if b:
                                st.image(b, width="stretch")
                            else:
                                st.caption(f"🖼️ Preview unavailable ({os.path.basename(p)})")
                        else:
                            # local relative path
                            lp = resolve_local_path(p)
                            if lp.exists():
                                st.image(lp.read_bytes(), width="stretch")
                            else:
                                st.caption(f"🖼️ Missing ({os.path.basename(p)})")
                    except Exception:
                        st.caption(f"🖼️ Preview unavailable ({os.path.basename(p)})")
            # ---- VIDEO PREVIEW (SIDEBAR) ----
            videos = event.get("media", {}).get("videos", [])
            if videos:
                st.markdown("**🎬 Videos**")
                for v in videos[:2]:  # limit for performance
                    try:
                        if isinstance(v, str) and v.startswith("gs://"):
                            vb = media_bytes_anywhere(v)
                            if vb:
                                st.video(vb)
                            else:
                                st.caption(f"🎬 Preview unavailable ({os.path.basename(v)})")
                        else:
                            lp = resolve_local_path(v)
                            if lp.exists():
                                st.video(lp.read_bytes())
                            else:
                                st.caption(f"🎬 Missing ({os.path.basename(v)})")
                    except Exception:
                        st.caption(f"🎬 Preview unavailable ({os.path.basename(v)})")
            else:
                st.caption("No videos attached.")

            # for i, p in enumerate(event["media"].get("photos", [])[:3]):
            #     with cols[i % 3]:
            #         try:
            #             st.image(p, width="stretch")
            #         except Exception as e:
            #             st.caption(f"🖼️ Preview unavailable ({os.path.basename(p)})")
            #             # Optional: log for debugging
            #             # logger.warning(f"Missing media: {p} → {str(e)}")
            #         #st.image(p,width="stretch")

        # Edit / Delete buttons — always visible when not editing
        if not st.session_state.current_journey_locked and not is_editing_this:
            col_edit, col_delete = st.columns([3, 1])
            with col_edit:
                if st.button("✏️ Edit", key=f"edit_{event['id']}"):
                    st.session_state.editing_event_id = event["id"]
                    st.rerun()
            with col_delete:
                if st.button("🗑️ Delete", key=f"delete_{event['id']}"):
                    st.session_state.confirm_delete_id = event["id"]
                    log_msg = f"Delete Memory• | {get_audit_actor_info()}"
                    append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout
                    st.rerun()

        # ── Edit form appears RIGHT HERE — under the buttons ──
        if is_editing_this and not st.session_state.current_journey_locked:
            st.markdown("---")
            st.subheader("Edit this memory")
            if map_data and map_data.get("last_clicked"):
                click = map_data["last_clicked"]
                lat, lon = click["lat"], click["lng"]
                # lat, lon = round(click["lat"], 6), round(click["lng"], 6)
                st.session_state.default_name = f"{st.session_state.latitude:.5f}, {st.session_state.longitude:.5f}"
                logger.info(f" 1 EDITY lat, lon **{lat}, {lon}**")
                logger.info(f" 2 EDITY lat, lon **{event["location"]["latitude"]}")
                logger.info(f" 3 EDITY lat, lon **{event["location"]["longitude"]}")
                st.session_state.latitude = lat
                st.session_state.longitude = lon

            with st.form(key=f"edit_form_{event['id']}", clear_on_submit=False):
                col_lat, col_lon = st.columns(2)

                if st.session_state.edit_lat is None:
                    lat_value = float(event["location"]["latitude"])
                else:
                    lat_value = float(st.session_state.edit_lat)

                if st.session_state.edit_lon is None:
                    lon_value = float(event["location"]["longitude"])
                else:
                    lon_value = float(st.session_state.edit_lon)

                logger.info(f"pass Edit marker {lat_value} {st.session_state.edit_lat}")
                logger.info(f"pass Edit marker {lon_value} {st.session_state.edit_lon}")
                with col_lat:
                    current_lat_key = f"lat_{event['id']}"
                    if current_lat_key in st.session_state:
                        if abs(st.session_state[current_lat_key] - lat_value) > 1e-6:
                            st.session_state[current_lat_key] = lat_value
                            # st.rerun()   # often helps — try with & without
                    new_lat = st.number_input(
                        "Latitude",
                        #value=lat_value,
                        #value=float(event["location"]["latitude"]),
                        value=lat_value,
                        format="%.6f", step=0.000001,
                        key=f"lat_{event['id']}"
                    )

                   # new_lat = st.number_input("Latitude", key=f"lat_{event['id']}", format="%.6f", step=0.000001)

                with col_lon:
                    current_lon_key = f"lon_{event['id']}"
                    if current_lon_key in st.session_state:
                        if abs(st.session_state[current_lon_key] - lon_value) > 1e-6:
                            st.session_state[current_lon_key] = lon_value
                            # st.rerun()   # often helps — try with & without
                    new_lon = st.number_input(
                        "Longitude",
                        #value=lon_value,
                        value=lon_value,
                        #value=float(event["location"]["longitude"]),
                        format="%.6f", step=0.000001,
                        key=f"lon_{event['id']}"
                    )
                    # new_lon = st.number_input("Longitude", key=f"lon_{event['id']}", format="%.6f", step=0.000001)

                new_title = st.text_input("Title", event["title"], key=f"title_{event['id']}")
                new_date = st.date_input(
                    "Date",
                    datetime.strptime(event["date"], "%Y-%m-%d").date(),
                    min_value=MIN_DATE,
                    max_value=MAX_DATE,
                    key=f"date_{event['id']}"
                )
                new_loc_name = st.text_input("Location Name", event["location"]["name"],
                                             key=f"locname_{event['id']}")
                new_desc = st.text_area("Description", event.get("description", ""),
                                        key=f"desc_{event['id']}")

                new_photos = st.file_uploader(
                    "Add Photos...",
                    accept_multiple_files=True,
                    type=["jpg", "jpeg", "png", "gif"],
                    key=f"photos_{event['id']}"
                )
                new_videos = st.file_uploader(
                    "Add Videos...",
                    accept_multiple_files=True,
                    type=["mp4", "mov"],
                    key=f"videos_{event['id']}"
                )

                col_save, col_cancel = st.columns(2)
                with col_save:
                    save_clicked = st.form_submit_button("💾 Save Changes", type="primary")
                with col_cancel:
                    cancel_clicked = st.form_submit_button("❌ Cancel", type="secondary")

                if save_clicked:
                    # Update fields
                    event["location"]["latitude"]  = new_lat
                    event["location"]["longitude"] = new_lon
                    event["title"]                 = new_title
                    event["date"]                  = new_date.strftime("%Y-%m-%d")
                    event["location"]["name"]      = new_loc_name
                    event["description"]           = new_desc

                    # Handle new uploads (same as add new memory)
                    if new_photos:
                        for up in new_photos:
                            fname = f"{int(time.time())}_{up.name}"
                            file_bytes = up.getvalue()

                            if IS_CLOUD:
                                gcs_path = upload_to_gcs(file_bytes, f"photos/{fname}", up.type)
                                event["media"].setdefault("photos", []).append(gcs_path)
                            else:
                                path = UPLOADS_PHOTOS / fname
                                path.write_bytes(file_bytes)
                                event["media"].setdefault("photos", []).append(str(path))

                    if new_videos:
                        for up in new_videos:
                            fname = f"{int(time.time())}_{up.name}"
                            file_bytes = up.getvalue()

                            if IS_CLOUD:
                                gcs_path = upload_to_gcs(file_bytes, f"videos/{fname}", up.type)
                                event["media"].setdefault("videos", []).append(gcs_path)
                            else:
                                path = UPLOADS_VIDEOS / fname
                                path.write_bytes(file_bytes)
                                event["media"].setdefault("videos", []).append(str(path))

                    save_data_to_storage(st.session_state.data)
                    st.session_state.force_map_refresh += 1
                    st.session_state.editing_event_id = None
                    st.session_state.pop("edit_lat" , None)
                    st.session_state.pop("edit_lon" , None)
                    st.success("Memory updated!")
                    log_msg = f"Update Memory• | {get_audit_actor_info()}"
                    append_to_log(log_msg, message_type="user_login", throttle=False)  # no throttle on logout
                    st.rerun()

                if cancel_clicked:
                    st.session_state.editing_event_id = None
                    st.rerun()

if "confirm_delete_id" in st.session_state:
    delete_event = next((e for e in st.session_state.data["events"] if e["id"] == st.session_state.confirm_delete_id),
                        None)
    if delete_event:
        for idx, event in enumerate(sorted_events, start=1):
            if event["id"] == st.session_state.confirm_delete_id:
                with st.sidebar.expander(f"{idx}. {event['date']} — {event['title']} (Confirm Delete)", expanded=True):
                    st.warning("⚠️ Are you sure you want to permanently delete this memory?")
                    st.write(f"**{event['title']}** • {event['date']} • {event['location']['name']}")

                    col_yes, col_no = st.columns(2)
                    with col_yes:
                        if st.button("Yes, delete permanently", type="primary", key=f"confirm_yes_{event['id']}"):
                            # for p in event["media"].get("photos", []) + event["media"].get("videos", []):
                            #     if os.path.exists(p):
                            #         os.remove(p)
                            # Delete media files (GCS or local)
                            for p in event["media"].get("photos", []) + event["media"].get("videos", []):
                                try:
                                    if p.startswith("gs://"):
                                        parts = p[5:].split("/", 1)
                                        bucket_name = parts[0]
                                        blob_path = parts[1] if len(parts) > 1 else ""
                                        storage.Client().bucket(bucket_name).blob(blob_path).delete()
                                    else:
                                        path = Path(p)
                                        if path.exists():
                                            path.unlink()
                                except Exception:
                                    pass  # Best-effort deletion


                            st.session_state.data["events"] = [e for e in st.session_state.data["events"] if
                                                               e["id"] != event["id"]]
                            # todo JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False),
                            #                     encoding="utf-8")
                            save_data_to_storage(st.session_state.data)
                            st.session_state.force_map_refresh += 1
                            if "confirm_delete_id" in st.session_state:
                                del st.session_state.confirm_delete_id
                            st.success("Memory deleted")
                            st.rerun()
                    with col_no:
                        if st.button("No, keep it", key=f"confirm_no_{event['id']}"):
                            if "confirm_delete_id" in st.session_state:
                                del st.session_state.confirm_delete_id
                            st.rerun()
                break

# Optional: last modified
#if JSON_FILE.exists():
#    mtime = datetime.fromtimestamp(JSON_FILE.stat().st_mtime)
#    st.sidebar.caption(f"Last saved: {mtime.strftime('%Y-%m-%d %H:%M')}")


# ==================== MY JOURNEYS (ROBUST PREVIEW) ====================
st.sidebar.subheader("📍 My Journeys")

local_json_files = get_local_json_files()

if not local_json_files:
    st.sidebar.info("No journeys found. Create one by adding memories!")
else:
    for json_name in sorted(local_json_files):
        is_current = json_name == st.session_state.selected_json_file

        # Try to load preview data safely
        try:
            blob_or_path = get_json_path(json_name) if IS_CLOUD else str(BASE_DIR / json_name)
            temp_data = load_data_from_file(blob_or_path)  # This auto-creates default if missing
            event_count = len(temp_data.get("events", []))
            title = temp_data.get("autobiography", {}).get("title", json_name.replace(".json", ""))
            title = " ".join(word.capitalize() for word in title.replace("-", " ").replace("_", " ").split())
            count_text = f"{event_count} place{'s' if event_count != 1 else ''}"
            has_error = False
        except Exception as e:
            logger.warning(f"Failed to preview {json_name}: {e}")
            event_count = 0
            title = json_name.replace(".json", "").replace("_", " ").replace("-", " ")
            title = " ".join(word.capitalize() for word in title.split())
            count_text = "0 places (load error)"
            has_error = True
        title = json_name
        # Button styling
        if is_current:
            button_label = f"**→ {title}** • {count_text}"
            if has_error:
                button_label += " ⚠️"
            disabled = True
        else:
            button_label = f"{title} • {count_text}"
            if has_error:
                button_label += " ⚠️"
            disabled = False

        if st.sidebar.button(
            button_label,
            key=f"journey_switch_{json_name}",
            disabled=disabled,
            use_container_width=True
        ):
            if not is_current:
                st.session_state.selected_json_file = json_name
                st.cache_data.clear()
                if "data" in st.session_state:
                    del st.session_state["data"]
                st.session_state.force_map_refresh += 1
                st.rerun()

st.markdown("---")
st.caption('[Privacy Statement](https://github.com/ywang0701/geo-temporial-journal/blob/main/privacy.txt)', unsafe_allow_html=True)