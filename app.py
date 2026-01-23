#import select
# COPY .streamlit/ ./.streamlit/ Dockerfile
import streamlit as st
from streamlit_folium import st_folium
import streamlit.components.v1 as components
import folium
from folium.plugins import MarkerCluster
from folium.plugins import AntPath, MarkerCluster  # Add AntPath here
import json
#import os
import sys
from datetime import datetime, timedelta, date
import time
import base64
import logging
from pathlib import Path
import html
import argparse
# === NEW IMPORTS FOR GOOGLE CLOUD STORAGE ===
import os
from google.cloud import storage
from google.oauth2 import service_account
import simplekml
import re
import toml

DEFAULT_ACTIVE_JSON="YourFirstJourney.json"
#ALLOWED_EDIT_EMAILS = ["your.email@gmail.com", "family.member@gmail.com"]

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
#logger.info(f"Detected IS_CLOUD = {os.getenv('DEPLOY_ENV') == 'cloud'}")

if "selected_json_file" not in st.session_state:
    st.session_state.selected_json_file = DEFAULT_ACTIVE_JSON

if "reset_map" not in st.session_state:
    st.session_state.reset_map = True

#if getattr(sys, 'frozen', False):
#    BASE_DIR = Path(sys.executable).parent
#else:
#    BASE_DIR = Path(__file__).resolve().parent

#EBASE_DIR = Path("/adata/JJ")
# BASE_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(os.getcwd()).resolve()

# === DEFINE FOLDERS (CRITICAL - you were missing this!) ===
BUCKET_NAME = "journey-journal"  # Your GCS bucket name
JOURNEYS_FOLDER = "journeys"      # Folder for JSON files
PHOTOS_FOLDER = "photos"
VIDEOS_FOLDER = "videos"
MIN_DATE = date(1800, 1, 1)              # ← you can lower to 1850 or 1800 if needed
MAX_DATE = date(2026,12,30)


st.session_state.latitude = 1.11
st.session_state.longitude = 1.11
# === DETECT IF RUNNING ON STREAMLIT CLOUD ===
# IS_CLOUD = os.getenv("DEPLOY_ENV") == "cloud"   # Set key: DEPLOY_ENV, value: cloud
IS_CLOUD = False # HF is local  CLOUD is GCP
try:
    repo_root = Path(__file__).resolve().parents[1]
    config_path = repo_root / ".streamlit" / "config.toml"

    config = toml.load(config_path)
    xsrf = config.get("server", {}).get("enableXsrfProtection", "not set")
    st.sidebar.info(f"XSRF protection status: {xsrf}")

except Exception as e:
    st.sidebar.warning(f"Could not read config.toml: {e}")

if IS_CLOUD:
    st.sidebar.success("✅ Running on Streamlit Cloud (GCS enabled)")

    # Load credentials from secrets (must be under [gcs] or [connections.gcs])
    credentials = service_account.Credentials.from_service_account_info(st.secrets["gcs"])

    # Create client with explicit credentials and project
    storage_client = storage.Client(
        credentials=credentials,
        project=st.secrets["gcs"]["project_id"]  # or ["connections.gcs"]
    )
    bucket = storage_client.bucket(BUCKET_NAME)
    # Your existing upload_to_gcs, download_from_gcs, etc. functions stay the same
else:
    st.sidebar.info("🖥️ Running locally (using filesystem)")
    # Your local fallback code (UPLOADS_PHOTOS, etc.)

def upload_to_gcs(file_bytes, destination_blob_name, content_type='application/octet-stream'):
    blob = bucket.blob(destination_blob_name)
    blob.upload_from_string(file_bytes, content_type=content_type)
    return f"gs://{BUCKET_NAME}/{destination_blob_name}"

def download_from_gcs(blob_name):
    blob = bucket.blob(blob_name)
    return blob.download_as_bytes()


def list_journey_blobs():
    return [blob.name for blob in bucket.list_blobs(prefix=f"{JOURNEYS_FOLDER}/") if blob.name.endswith(".json")]


def get_json_path(json_name):
    return f"{JOURNEYS_FOLDER}/{json_name}"

def to_relative_path(path: Path) -> str:
    """Convert absolute Path to path relative to CWD (for JSON storage)"""
    return path.relative_to(BASE_DIR).as_posix()

if IS_CLOUD:
    pass
else:
    # Local development fallback
    UPLOADS_PHOTOS = BASE_DIR / "uploads" / "photos"
    UPLOADS_VIDEOS = BASE_DIR / "uploads" / "videos"
    UPLOADS_PHOTOS.mkdir(parents=True, exist_ok=True)
    UPLOADS_VIDEOS.mkdir(parents=True, exist_ok=True)

#DEFAULT_ACTIVE_JSON="life_events.json"

if "edit_lat" not in st.session_state:
    st.session_state.edit_lat = None
if "edit_lon" not in st.session_state:
    st.session_state.edit_lon = None

if "default_location" not in st.session_state:
    st.session_state.default_name= None

if "selected_event_id" not in st.session_state:
    st.session_state.selected_event_id = None

if "current_journey_locked" not in st.session_state:
    st.session_state.current_journey_locked = False

if "add_new_memory" not in st.session_state:
    st.session_state.add_new_memory = False

if "search_text" not in st.session_state:
    st.session_state.search_text = ""

import streamlit as st
import streamlit.components.v1 as components  # ← Correct import for current Streamlit

# ──────────────────────────────────────────────────────────────
#          TEMP BYPASS – Google login is broken right now
# ──────────────────────────────────────────────────────────────

# Force login for everyone (temporary dev workaround)
if True:  # ← change to False when you fix real auth
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
    # Original real authentication code (commented out for now)
    if not st.user.is_logged_in:
        st.set_page_config(page_title="Please Sign In", layout="wide")
        st.title("🌍 Journey Journal")
        st.markdown("Sign in to continue")
        if st.button("Sign in with Google", type="primary"):
            st.login("google")
        st.stop()

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
parser = argparse.ArgumentParser(description="My Life Journey App")
parser.add_argument(
    "--file",
    type=str,
    default=DEFAULT_ACTIVE_JSON,
    help=f"Path to the life events JSON file (default: {DEFAULT_ACTIVE_JSON})"
)
args = parser.parse_args()

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


def is_journey_locked(json_filename):
    if IS_CLOUD:
        lock_blob = bucket.blob(f"{JOURNEYS_FOLDER}/{json_filename}_lock")
        return lock_blob.exists()
    else:
        lock_path = BASE_DIR / f"{json_filename}_lock"
        return lock_path.exists()


import simplekml
from datetime import datetime

def export_to_kml(events, output_filename="my_journey_with_timeline.kml"):
    """
    Creates KML with:
    - Placemarks for each memory (with title, date, description)
    - One continuous LineString for the journey path
    - TimeStamp on each placemark → enables timeline animation in Google Earth
    """
    kml = simplekml.Kml(name="My Journey with Timeline", open=1)

    # Sort events by date (safety)
    sorted_events = sorted(events, key=lambda e: e.get("date", "0000-00-00"))

    path_coords = []

    # Nice style for the path (always visible)
    journey_line = kml.newlinestring(name="Journey Path")
    journey_line.style.linestyle.color = simplekml.Color.teal  # or simplekml.Color.hex("50E3C2")
    journey_line.style.linestyle.width = 5
    journey_line.altitudemode = simplekml.AltitudeMode.clamptoground

    for idx, event in enumerate(sorted_events, 1):
        try:
            lat = float(event["location"]["latitude"])
            lon = float(event["location"]["longitude"])
            coord = (lon, lat)  # KML: longitude first!
            path_coords.append(coord)

            title = event.get("title", f"Memory #{idx}")
            date_str = event.get("date", None)
            desc = event.get("description", "No description")

            popup_html = f"""
            <h3 style="margin:0 0 8px 0;">{title}</h3>
            <p style="color:#555; margin:4px 0;"><b>Date:</b> {date_str or 'Unknown'}</p>
            <p style="margin:8px 0 12px 0;">{desc}</p>
            """

            photos = len(event["media"].get("photos", []))
            videos = len(event["media"].get("videos", []))
            if photos or videos:
                popup_html += f'<p style="color:#666;font-style:italic;">{photos} photo(s) • {videos} video(s)</p>'

            # Create placemark
            pnt = kml.newpoint(
                name=f"{idx}. {date_str or 'Unknown'} – {title}",
                description=popup_html,
                coords=[coord]
            )

            # Icon
            pnt.style.iconstyle.icon.href = "http://maps.google.com/mapfiles/kml/paddle/red-circle.png"
            pnt.style.iconstyle.scale = 1.1

            # ── KEY PART: Add TimeStamp for timeline animation ──
            if date_str:
                try:
                    # Parse your date (YYYY-MM-DD) and set a default time (noon UTC)
                    dt = datetime.strptime(date_str, "%Y-%m-%d")
                    # Format as ISO 8601: yyyy-mm-ddThh:mm:ssZ (Z = UTC)
                    iso_time = dt.replace(hour=12, minute=0, second=0).isoformat() + "Z"
                    pnt.timestamp.when = iso_time
                except ValueError:
                    # If date parsing fails, skip timestamp (still usable)
                    pass

        except (KeyError, ValueError, TypeError):
            continue

    # Add connecting path (always visible)
    if len(path_coords) >= 2:
        journey_line.coords = path_coords

    kml.save(output_filename)
    return output_filename

# st.sidebar.caption(f"📄 Using data file: `{JSON_FILE.name}`") # todo
#if "selected_json_file" not in st.session_state:
#    st.session_state.selected_json_file = DEFAULT_ACTIVE_JSON

JSON_BLOB_NAME = get_json_path(st.session_state.selected_json_file) if IS_CLOUD else str(BASE_DIR / st.session_state.selected_json_file)
JSON_FILE = BASE_DIR / st.session_state.selected_json_file
# st.sidebar.caption(f"📄 Using data file: `{st.session_state.selected_json_file}`")

# ==================== SCAN FOR JSON FILES ====================

def get_sorted_events_with_index():
    events = st.session_state.data.get("events", [])
    sorted_events = sorted(events, key=lambda x: x.get("date", "0000-00-00"))
    return list(enumerate(sorted_events, start=1))  # (1-based index, event)

def get_local_json_files():
    """Scan the current directory for .json files (excluding hidden and system files)"""
    json_files = []
    for item in BASE_DIR.iterdir():
        if item.is_file() and item.suffix.lower() == ".json" and not item.name.startswith("."):
            json_files.append(item.name)
    return sorted(json_files)

def save_data_to_storage(data):
    json_text = json.dumps(data, indent=4, ensure_ascii=False)
    if IS_CLOUD:
        logger.info(f" Save to cloud {JSON_BLOB_NAME}")
        upload_to_gcs(json_text.encode("utf-8"), JSON_BLOB_NAME, "application/json")
    else:
        logger.info(f" Save to local {JSON_FILE}")
        Path(JSON_FILE).write_text(json_text, encoding="utf-8")

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

def is_journey_locked(json_filename):
    if not st.user.is_logged_in:
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


# Right after st.session_state.selected_json_file = json_name
st.session_state.current_journey_locked = is_journey_locked(st.session_state.selected_json_file)


local_json_files = available_journeys

# ==================== DYNAMIC TITLE BASED ON JSON FILENAME ====================
# Get filename without extension and path
json_filename = st.session_state.selected_json_file # e.g., "life_events", "my_family_memories", "john_2025"

# Clean up common patterns for nicer display
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


# ==================== ROBUST DATA INITIALIZATION ====================
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


if "data" not in st.session_state:
    #st.session_state.data = load_data_from_file(JSON_FILE)
    st.session_state.data = load_data_from_file(JSON_BLOB_NAME)

# List journeys
def get_local_json_files():
    #if os.getenv("K_SERVICE1"):
    if IS_CLOUD:
        blobs = list_journey_blobs()
        return [os.path.basename(b) for b in blobs]
    else:
        return sorted([f.name for f in BASE_DIR.iterdir() if f.is_file() and f.suffix == ".json" and not f.name.startswith(".")])

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

# Updated title: includes filename and count
event_count = len(st.session_state.data.get("events", []))
place_text = "memory" if event_count == 1 else "memories"
memory_count_str = f"{event_count} {place_text}" if event_count > 0 else "no memory  yet"

#full_title = f"🌍 Journey ({display_name}) has {event_count} {place_text} {timeline_info}"
full_title = f"🌍 Journey ({display_name}) – {memory_count_str}{timeline_info}"

st.set_page_config(
   page_title=full_title,
   layout="wide",
   initial_sidebar_state=initial_sidebar
)

# ==================== SESSION STATE INITIALIZATION ====================
if "editing_event_id" not in st.session_state:
    st.session_state.editing_event_id = None
if "map_center" not in st.session_state:
    st.session_state.map_center = [20, 0]
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 2
if "force_map_refresh" not in st.session_state:
    st.session_state.force_map_refresh = 0


def get_media_bytes(media_path):
    """Fetch bytes from GCS (gs://...) or local path"""
    if media_path.startswith("gs://"):
        parts = media_path[5:].split("/", 1)
        bucket_name = parts[0]
        blob_path = parts[1] if len(parts) > 1 else ""
        blob = storage.Client().bucket(bucket_name).blob(blob_path)
        return blob.download_as_bytes()
    else:
        path = Path(media_path)
        if not path.exists():
            return None
        return path.read_bytes()

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


# ==================== POPUP ====================
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
    # # === PHOTOS ===
    # if photos:
    #     popup += "<strong>Photos:</strong><div style='display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:8px;'>"
    #     for p in photos:
    #         # Convert gs://journey-journal/... → public HTTPS URL
    #         if p.startswith("gs://"):
    #             public_url = p.replace("gs://journey-journal/", "https://storage.googleapis.com/journey-journal/")
    #         else:
    #             public_url = p  # local fallback (only works locally)
    #
    #         fn = os.path.basename(p)
    #         popup += f"""
    #         <div style="text-align:center;">
    #             <img src="{public_url}"
    #                  style="width:100px;height:100px;object-fit:cover;border-radius:8px;cursor:pointer;"
    #                  onclick="this.style.width='100%';this.style.height='auto';this.onclick=null;"
    #                  loading="lazy">
    #             <br><small><a href="{public_url}" download="{fn}" target="_blank">📥 Download</a></small>
    #         </div>
    #         """
    #     popup += "</div>"
    #
    # # === VIDEOS ===
    # if videos:
    #     popup += "<strong style='margin-top:15px;display:block;'>Videos:</strong><div style='display:flex;flex-direction:column;gap:12px;margin-top:8px;'>"
    #     for v in videos:
    #         if v.startswith("gs://"):
    #             public_url = v.replace("gs://journey-journal/", "https://storage.googleapis.com/journey-journal/")
    #         else:
    #             public_url = v
    #
    #         fn = os.path.basename(v)
    #         popup += f"""
    #         <div style="text-align:center;">
    #             <video controls style="max-width:100%;border-radius:8px;" preload="metadata">
    #                 <source src="{public_url}" type="video/mp4">
    #                 Your browser does not support the video tag.
    #             </video>
    #             <br><small><a href="{public_url}" download="{fn}" target="_blank">📥 Download</a></small>
    #         </div>
    #         """
    #     popup += "</div>"

    # === FALLBACK MESSAGES ===
    if not photos and not videos:
        popup += "<p style='text-align:center;color:#888;'><em>No media</em></p>"
    else:
        popup += f"<p style='text-align:center;color:#666;margin-top:12px;'><em>{len(photos)} photo(s) • {len(videos)} video(s)</em></p>"

    popup += "</div>"
    return popup

# ==================== MAP CREATION WITH CURVED JOURNEY LINES ====================
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
st.set_page_config(
    page_title=f"{display_name} - Map {timeline_info}",
    layout="wide",
    initial_sidebar_state=initial_sidebar   # ← Use the variable here
)

#========================== Login Session ===========================

# ──────────────────────────────────────────────────────────────
#          Authentication Guard – runs before anything else
# ──────────────────────────────────────────────────────────────

# Optional: you can force re-login every time (good for testing)
# But usually you want session persistence via cookie → comment out
# if "force_reauth" not in st.session_state:
#     st.session_state.force_reauth = False

if not st.user.is_logged_in:
    st.sidebar.title("Hello Visitor")
    st.sidebar.markdown("Sign in to view or edit your personal journeys.")
    if st.sidebar.button("Sign in with Google", type="primary"):
        st.login("google")

    # ## Google debug
    # st.write("--- DEBUG LOGIN STATUS ---")
    # st.write("st.user.is_logged_in       =", st.user.is_logged_in)
    # st.write("st.user (full object)      =", dict(st.user) if st.user else "None")
    # st.write("st.experimental_user       =", st.experimental_user)
    # st.write("Session state has user?    =", "user" in st.session_state)
    if st.user.is_logged_in:
        st.success(f"Logged in as {st.user.name} ({st.user.email})")
    else:
        st.info("Not logged in yet")
    st.set_page_config(page_title="Journey Journal – Sign in", layout="wide")

    #st.title("🌍 Journey Journal")
    st.title("🌍 Welcome Visitors 🌍")
    #st.markdown("Sign in to view or edit your personal journeys.")

# ──────────────────────────────────────────────────────────────
#          User is now logged in → show app + user info
# ──────────────────────────────────────────────────────────────
if st.user.is_logged_in:
# Optional: show who is logged in (very useful)
    st.sidebar.title(f"Hello 🙍 {st.user.name}")
    #st.sidebar.markdown(f"**👤 Signed in as**  {st.user.email or st.user.name or 'Authenticated user'}")
    #st.sidebar.caption(f"Logged in • {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    st.sidebar.markdown(f"** Signed in as**  {st.user.email or st.user.name or 'Authenticated user'}")

# if st.sidebar.button("🔒 Lock this journey", type="secondary", use_container_width=True):

    if st.sidebar.button("🙋️ Sign out", type="secondary", use_container_width=True):
        st.logout()
        # st.user.is_logged_in = False
        st.rerun()

    # ── Optional: store user info in session state if you need it later ──
    if "user_info" not in st.session_state:
        st.session_state.user_info = {
            "email": st.user.email,
            "name": st.user.name,
            "id": st.user.sub,           # subject = unique user ID
            "provider": st.user.iss,     # issuer
            "last_login": datetime.now().isoformat()
        }

    # ──────────────────────────────────────────────────────────────
    #          Your NORMAL application code starts here
    # ──────────────────────────────────────────────────────────────

    #st.title(f"Welcome, {st.user.name or 'Traveler'}! 🌏")

# ... paste ALL your existing journey map, sidebar, editing logic, etc. here ...

# # Example: restrict editing to specific users (optional)
#
#     if st.user.email in ALLOWED_EDIT_EMAILS:
#         st.success("You have **edit permissions**.")
#         # show edit buttons, add memory form, etc.
#     else:
#         st.info("You are in **view-only mode**.")
#         # hide editing UI or show read-only version

#========================== Login Session ===========================
#st.title("🌍 My Life Journey – Map with Colored Timeline")

full_title = f"🌍 Journey ({display_name}) has {event_count} {place_text} {timeline_info}"



# ==================== CENTER ON MARKER CONTROL ====================
if data["events"]:
    sorted_events = sorted(data["events"], key=lambda x: x["date"])
    col_title, col_reset, col_btn, col_num= st.columns([10, 1, 1, 1])
    #col1, col2 = st.columns([3, 1])
    with col_title:
        st.title(full_title)
    with col_reset:
        if st.button("Reset to Full View"):
            st.session_state.map_center = [20, 0]
            st.session_state.map_zoom = 2
            st.session_state.force_map_refresh += 1
            st.session_state.reset = True
            st.rerun()

    logger.info(f"DEBUG: event_cout {event_count}")
    if event_count >= 15:
        with col_num:
            marker_id = st.number_input("Go to marker ID", min_value=1, max_value=len(sorted_events), value=1, step=1, label_visibility="collapsed")
        with col_btn:
            if st.button("Visit => Marker"):
                if 1 <= marker_id <= len(sorted_events):
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

#st.title(full_title)

# ==================== TIMELINE BAR ON TOP ====================
if data["events"]:
    sorted_events = sorted(data["events"], key=lambda x: x["date"])
    dates = [datetime.strptime(e["date"], "%Y-%m-%d") for e in sorted_events]

    if dates:
        min_date = min(dates) - timedelta(days=365 * 2)
        max_date = max(dates) + timedelta(days=365 * 5)
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

#st.session_state.map_center = [20, 0]
#st.session_state.map_zoom = 12
#st.write("DEBUG: af create_map Current map_center in session_state =", st.session_state.get("map_center"))
#st.write("DEBUG: af create_map Current map_zoom   in session_state =", st.session_state.get("map_zoom"))
#st.write("DEBUG: af create_map force_map_refresh counter =", st.session_state.force_map_refresh)

# ==================== MAP ====================
map_key = f"main_map_{st.session_state.force_map_refresh}"
main_map = create_map()

map_data = st_folium(
    main_map,
    key=map_key,
    center=st.session_state.map_center,
    zoom=st.session_state.map_zoom,
    width=None,
    height=1200,
    use_container_width=True,
    returned_objects=["last_clicked"]
    #returned_objects = ["last_clicked", "center", "zoom"]
)

click = map_data["last_clicked"]

if "lat_edit" not in st.session_state:
    st.session_state.edit_lat = None

if "lon_edit" not in st.session_state:
    st.session_state.edit_lon = None

if map_data is not None and map_data.get("last_clicked"):
    new_lat = round(click["lat"], 6)
    new_lon = round(click["lng"], 6)

# Update the live values that the number inputs will read from
    st.session_state.edit_lat = new_lat
    st.session_state.edit_lon = new_lon


full_title = f"🌍 Journey ({display_name}) has {event_count} {place_text} {timeline_info}"


# Now check click + mode
if "app_mode" not in st.session_state:
    st.session_state.app_mode = "View Mode"  # Default

if st.session_state.app_mode and map_data and map_data.get("last_clicked"):
    pass
    # for display status purpose (not clean code)
else:
    if map_data and map_data.get("last_clicked") and not is_edit_mode:
        st.sidebar.info("🔒 In **View Mode** — map clicks are disabled. Switch to **Edit Mode** to add memories.")

if map_data and map_data.get("center"):
    st.session_state.map_center = [map_data["center"]["lat"], map_data["center"]["lng"]]
    st.session_state.map_zoom = map_data.get("zoom", 2)

# ==================== ADD NEW MEMORY ====================
# Safety: only allow one mode at a time
if st.session_state.editing_event_id and st.session_state.add_new_memory:
    st.session_state.add_new_memory = False  # editing takes priority

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
        #with col_cancel:
        #    cancel_clicked = st.form_submit_button("❌ Cancel", type="secondary")

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

                # photo_paths = []
                # for up in photos or []:
                #     if up is not None:  # Safety check
                #         fname = f"{int(time.time())}_{up.name}"
                #         try:
                #             file_bytes = up.getvalue()  # ← Use .getvalue(), not .getbuffer()
                #             if not file_bytes:  # Extra safety
                #                 st.warning(f"Empty file skipped: {up.name}")
                #                 continue
                #             gcs_url = upload_to_gcs(file_bytes, f"photos/{fname}", up.type)
                #             photo_paths.append(gcs_url)
                #         except Exception as e:
                #             st.error(f"Failed to upload photo {up.name}: {e}")
                #
                # video_paths = []
                # for up in videos or []:
                #     if up is not None:
                #         fname = f"{int(time.time())}_{up.name}"
                #         try:
                #             file_bytes = up.getvalue()
                #             if not file_bytes:
                #                 st.warning(f"Empty file skipped: {up.name}")
                #                 continue
                #             gcs_url = upload_to_gcs(file_bytes, f"videos/{fname}", up.type)
                #             video_paths.append(gcs_url)
                #         except Exception as e:
                #             st.error(f"Failed to upload video {up.name}: {e}")

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
        if st.sidebar.button("❌ Cancel Adding Memory", type="secondary"):
            st.session_state.current_journey_locked = False
            st.session_state.add_new_memory = False
            st.success("Adding Memory cancelled!")
            st.rerun()  # Clears the form by removing last_clicked state
        #if cancel_clicked:
        #    st.rerun()
#else:
#    if map_data and map_data.get("last_clicked"):
#        st.sidebar.info("🔒 This journey is locked — cannot add new memories")

# # ==================== EDITING EXISTING EVENT ====================
# if not st.session_state.current_journey_locked and st.session_state.editing_event_id:
#     event = next((e for e in st.session_state.data["events"] if e["id"] == st.session_state.editing_event_id), None)
#     if event:
#         if map_data and map_data.get("last_clicked"):
#             click = map_data["last_clicked"]
#             lat, lon = click["lat"], click["lng"]
#             # lat, lon = round(click["lat"], 6), round(click["lng"], 6)
#             st.session_state.default_name = f"{st.session_state.latitude:.5f}, {st.session_state.longitude:.5f}"
#             #st.markdown(f" 1 EDITY lat, lon **{lat}, {lon}**")
#             #st.markdown(f" 2 EDITY lat, lon **{event["location"]["latitude"]}")
#             #st.markdown(f" 3 EDITY lat, lon **{event["location"]["longitude"]}")
#             st.session_state.latitude = lat
#             st.session_state.longitude = lon
#
#
#         st.sidebar.header(f"✏️ Editing: {event['title']}")
#
#         cur_lat = event["location"]["latitude"]
#         cur_lon = event["location"]["longitude"]
#         if st.session_state.latitude == 1.11:
#             st.session_state.latitude = cur_lat
#         if st.session_state.longitude== 1.11:
#             st.session_state.longitude = cur_lon
#         st.session_state.default_name = f"{st.session_state.latitude:.5f}, {st.session_state.longitude:.5f}"
#         #st.sidebar.markdown(f"**Current:** Lat {cur_lat:.6f} | Lon {cur_lon:.6f}")
#         st.sidebar.markdown(f"**Current:** Lat {st.session_state.latitude:.6f} | Lon {st.session_state.longitude:.6f}")
#
#         #new_lat = st.sidebar.number_input("Latitude", value=cur_lat, step=0.000001, format="%.6f")
#         #new_lon = st.sidebar.number_input("Longitude", value=cur_lon, step=0.000001, format="%.6f")
#
#         new_lat = st.sidebar.number_input("Latitude", value=st.session_state.latitude, step=0.000001, format="%.6f")
#         new_lon = st.sidebar.number_input("Longitude", value=st.session_state.longitude, step=0.000001, format="%.6f")
#
#         for mtype, label in [("photos", "Photos"), ("videos", "Videos")]:
#             st.sidebar.markdown(f"### Current {label}")
#             paths = event["media"].get(mtype, []).copy()
#             if paths:
#                 cols = st.sidebar.columns(3 if mtype == "photos" else 2)
#                 for i, p in enumerate(paths):
#                     if os.path.exists(p):
#                         with cols[i % len(cols)]:
#                             if mtype == "photos":
#                                 st.image(p, width=150)
#                             else:
#                                 st.video(p)
#                             if st.button("Remove", key=f"del_{mtype}_{i}_{event['id']}"):
#                                 os.remove(p)
#                                 event["media"][mtype].remove(p)
#                                 # todo JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False),
#                                 #                     encoding="utf-8")
#                                 save_data_to_storage(st.session_state.data)
#                                 st.rerun()
#             else:
#                 st.sidebar.info(f"No {label.lower()}")
#
#         with st.sidebar.form("edit_form"):
#             if map_data and map_data.get("last_clicked"):
#                 click = map_data["last_clicked"]
#                 lat, lon = click["lat"], click["lng"]
#                 #lat, lon = round(click["lat"], 6), round(click["lng"], 6)
#                 #default_name = f"{st.session_state.latitude:.5f}, {st.session_state.longitude:.5f}"
#                 st.session_state.default_name = f"{st.session_state.latitude:.5f}, {st.session_state.longitude:.5f}"
#                 #st.markdown(f" 1 EDITY lat, lon **{lat}, {lon}**")
#                 #st.markdown(f" 2 EDITY lat, lon **{event["location"]["latitude"]}")
#                 #st.markdown(f" 3 EDITY lat, lon **{event["location"]["longitude"]}")
#                 st.session_state.latitude = lat
#                 st.session_state.longitude = lon
#                 pass
#             new_title = st.text_input("Title", event["title"])
#             new_date = st.date_input("Date", datetime.strptime(event["date"], "%Y-%m-%d").date(),
#                                      #min_value=datetime(1920, 1, 1).date(),
#                                      min_value=MIN_DATE,
#                                      max_value=MAX_DATE)
#             #new_loc = st.text_input("Location Name", event["location"]["name"]) #TODO
#             new_loc = st.text_input("Location Name", st.session_state.default_name)
#             new_desc = st.text_area("Description", event.get("description", ""))
#             add_photos = st.file_uploader("Add Photos", accept_multiple_files=True, type=["jpg", "jpeg", "png", "gif","heic","HEIC","heif","HEIF"],
#                                           key=f"add_ph_{event['id']}")
#             add_videos = st.file_uploader("Add Videos", accept_multiple_files=True, type=["mp4", "mov", "webm"],
#                                           key=f"add_vid_{event['id']}")
#
#             if st.form_submit_button("💾 Save Changes", type="primary"):
#                 event["location"]["latitude"] = new_lat
#                 event["location"]["longitude"] = new_lon
#                 event["title"] = new_title
#                 event["date"] = new_date.strftime("%Y-%m-%d")
#                 event["location"]["name"] = new_loc
#                 event["description"] = new_desc
#
#                 # --- Upload new photos (FIXED) ---
#                 for up in add_photos or []:
#                     if up is not None:
#                         fname = f"{int(time.time())}_{up.name}"
#                         try:
#                             file_bytes = up.getvalue()
#                             if not file_bytes:
#                                 continue
#                             if IS_CLOUD:
#                                 url = upload_to_gcs(file_bytes, f"photos/{fname}", up.type)
#                             else:
#                                 local_path = UPLOADS_PHOTOS / fname
#                                 local_path.write_bytes(file_bytes)
#                                 url = str(local_path)
#                             event["media"].setdefault("photos", []).append(url)
#                         except Exception as e:
#                             st.error(f"Failed to upload photo {up.name}: {e}")
#
#                 # --- Upload new videos (FIXED) ---
#                 for up in add_videos or []:
#                     if up is not None:
#                         fname = f"{int(time.time())}_{up.name}"
#                         try:
#                             file_bytes = up.getvalue()
#                             if not file_bytes:
#                                 continue
#                             if IS_CLOUD:
#                                 url = upload_to_gcs(file_bytes, f"videos/{fname}", up.type)
#                             else:
#                                 local_path = UPLOADS_VIDEOS / fname
#                                 local_path.write_bytes(file_bytes)
#                                 url = str(local_path)
#                             event["media"].setdefault("videos", []).append(url)
#                         except Exception as e:
#                             st.error(f"Failed to upload video {up.name}: {e}")
#
#                 # todo JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False), encoding="utf-8")
#                 save_data_to_storage(st.session_state.data)
#                 st.session_state.force_map_refresh += 1
#                 st.session_state.editing_event_id = None
#                 st.success("Changes saved!")
#                 st.rerun()
#
#             if st.sidebar.button("Cancel Editing"):
#                 st.session_state.editing_event_id = None
#                 st.rerun()
# else:
#     if st.session_state.editing_event_id:
#         st.warning("Cannot edit — journey is locked")
#         st.session_state.editing_event_id = None
#         st.rerun()


# ==================== SIDEBAR SUMMARY WITH EDIT AND DELETE BUTTONS ====================
st.sidebar.subheader("✨ Journey Operations")
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
                                st.rerun()

                            except Exception as e:
                                st.error(f"Failed to create journey: {e}")
                    st.session_state.current_journey_locked = is_journey_locked(new_journey_name)

                    with col_cancel:
                        if st.button("❌ Cancel", type="secondary", use_container_width=True):
                            st.rerun()

    # ==================== RENAME JOURNEY (FIXED ORDER + SAFE) ====================
    with st.sidebar.expander("✏️ Rename Journey", expanded=False):
        st.write("Change the name of an existing journey. This renames the file and updates the title.")

        available_journeys = get_local_json_files()

        if not available_journeys:
            st.info("No journeys available to rename.")
        else:
            # Select journey to rename
            journey_to_rename = st.selectbox(
                "Select journey to rename",
                options=available_journeys,
                index=available_journeys.index(st.session_state.selected_json_file)
                if st.session_state.selected_json_file in available_journeys else 0,
                help="Choose the journey you want to rename"
            )

            # === LOAD AND PREVIEW THE SELECTED JOURNEY FIRST ===
            blob_or_path = get_json_path(journey_to_rename) if IS_CLOUD else str(BASE_DIR / journey_to_rename)
            try:
                current_data = load_data_from_file(blob_or_path)
                current_title = current_data.get("autobiography", {}).get("title", journey_to_rename.replace(".json", ""))
                event_count = len(current_data.get("events", []))

                # Format nice display name
                current_display = journey_to_rename.replace(".json", "").replace("_", " ").replace("-", " ")
                current_display = " ".join(word.capitalize() for word in current_display.split())

                st.info(f"**Current:** {current_title} • {event_count} memory{'s' if event_count != 1 else ''} • File: `{journey_to_rename}`")
            except Exception as e:
                st.error(f"Could not load journey data: {e}")
                current_display = journey_to_rename.replace(".json", "")
                current_title = current_display
                current_data = None

            # === NOW USE current_display SAFELY ===
            new_journey_name = st.text_input(
                "New Journey Name*",
                value=current_title,  # Pre-fill with actual title, not filename
                placeholder="e.g., Europe Adventure 2025",
                help="This will become the new display title and filename"
            )

            if new_journey_name and new_journey_name.strip():
                if new_journey_name.strip() == current_title:
                    st.info("New name is the same as current — nothing to do.")
                else:
                    # Clean for safe filename
                    clean_name = (
                        new_journey_name.strip()
                        .lower()
                        .replace(" ", "-")
                        .replace("_", "-")
                        .replace("/", "")
                        .replace("\\", "")
                        .replace(".", "")
                    )
                    if not clean_name:
                        st.error("Invalid name – please use letters, numbers, spaces, or hyphens.")
                    else:
                        new_filename = f"{clean_name}.json"
                        new_blob_name = get_json_path(new_filename) if IS_CLOUD else str(BASE_DIR / new_filename)

                        # Check if new filename already exists
                        if new_filename in available_journeys:
                            st.warning(f"A journey named **{new_filename}** already exists. Choose a different name.")
                        else:
                            col_rename, col_cancel = st.columns(2)
                            with col_rename:
                                if st.button("✏️ Rename Journey", type="primary", use_container_width=True):
                                    if current_data is None:
                                        st.error("Cannot rename: failed to load current journey data.")
                                    else:
                                        try:
                                            # Update title in data
                                            current_data["autobiography"]["title"] = new_journey_name.strip()
                                            current_data["autobiography"]["last_updated"] = datetime.now().strftime("%Y-%m-%d")

                                            json_text = json.dumps(current_data, indent=4, ensure_ascii=False)

                                            # Save to new location
                                            if IS_CLOUD:
                                                upload_to_gcs(json_text.encode("utf-8"), get_json_path(new_filename), "application/json")
                                                # Delete old blob
                                                bucket.blob(get_json_path(journey_to_rename)).delete()
                                                st.success(f"✅ Journey renamed to **{new_journey_name}** in cloud!")
                                            else:
                                                (BASE_DIR / new_filename).write_text(json_text, encoding="utf-8")
                                                (BASE_DIR / journey_to_rename).unlink(missing_ok=True)
                                                st.success(f"✅ Journey renamed to **{new_journey_name}** locally!")

                                            # If renaming the currently active journey, update session
                                            if journey_to_rename == st.session_state.selected_json_file:
                                                st.session_state.selected_json_file = new_filename
                                                st.cache_data.clear()
                                                if "data" in st.session_state:
                                                    del st.session_state["data"]

                                            st.rerun()

                                        except Exception as e:
                                            st.error(f"Rename failed: {e}")
                                            logger.error(f"Rename error: {e}")
                            st.session_state.selected_json_file = new_filename

                            with col_cancel:
                                st.button("❌ Cancel", type="secondary", use_container_width=True)
            else:
                st.warning("Please enter a new journey name.")

# ==================== DOWNLOAD JOURNEY BACKUP (SELECT ANY JOURNEY) ====================
with st.sidebar.expander("📥 Backup Journey", expanded=False):
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

        except Exception as e:
            st.error("Could not load journey data for download.")
            logger.error(f"Failed to prepare download for {journey_to_download}: {e}")

# ==================== DOWNLOAD JOURNEY AS KML (SELECT ANY JOURNEY) ====================
with st.sidebar.expander("🌍 Export to Google Map/Earth", expanded=False):
    st.write("Select any journey and download it as a KML file for Google My Maps or Google Earth.")

    available_journeys = get_local_json_files()

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
            # Load the selected journey data safely
            try:
                blob_or_path = get_json_path(journey_to_kml) if IS_CLOUD else str(BASE_DIR / journey_to_kml)
                temp_data = load_data_from_file(blob_or_path)

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
                    # Generate filename based on **selected** journey
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
                    base_name = journey_to_kml.replace(".json", "")
                    kml_filename = f"{base_name}_journey_{timestamp}.kml"

                    # Create KML file (using your existing function)
                    export_to_kml(temp_data["events"], kml_filename)

                    # Read the file for download
                    with open(kml_filename, "rb") as f:
                        st.download_button(
                            label="⬇️ Download KML Now",
                            data=f,
                            file_name=kml_filename,  # ← now uses selected journey name
                            mime="application/vnd.google-earth.kml+xml",
                            use_container_width=True,
                            key=f"download_kml_{journey_to_kml}_{timestamp}"  # unique per selection + time
                        )

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
            st.rerun()

    with tab_regex:
        regex_pattern = st.text_input("Regular expression", placeholder="Paris|birthday|202[0-5]", key="regex_pattern")
        if st.button("Search (regex)", key="btn_regex"):
            st.session_state.search_mode = "regex"
            st.session_state.search_value = regex_pattern
            st.rerun()


# st.sidebar.subheader(f"🗺️ Current Journey ({st.session_state.selected_json_file}) has {len(st.session_state.data['events'])} places")
event_count = len(st.session_state.data.get("events", []))
place_text = "memory" if event_count == 1 else "memories"

locked = st.session_state.get("current_journey_locked", False)

lock_emoji = "🔒" if st.session_state.current_journey_locked else "✏️"

#st.sidebar.subheader(f"🗺️ Selected Journey ({st.session_state.selected_json_file}) has {event_count} {place_text}")

# Near the top of sidebar — after showing current journey name & count
locked = st.session_state.current_journey_locked

# ==================== JOURNEY LOCK / UNLOCK STATUS & CONTROLS ====================
#st.sidebar.markdown("### Journey Status")

if st.user.is_logged_in:

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
    st.sidebar.caption("Sign in to edit this journey")

# sorted_events = sorted(st.session_state.data["events"], key=lambda x: x["date"])
#
# for idx, event in enumerate(sorted_events, start=1):
#     # Unique expander key - very important
#     expander_key = f"memory_expander_{event['id']}"
#
#     # Control whether this expander should be open
#     is_editing_this = (st.session_state.get("editing_event_id") == event["id"])
#
#     with st.sidebar.expander(
#             f"🔹 {idx}. {event['date']} — {event['title']}",
#             expanded=is_editing_this or st.session_state.get(f"force_open_{event['id']}", False)
#     ):
#         st.caption(f"📍 {event['location']['name']}")
#
#         # Preview media (optional)
#         cols = st.columns(3)
#         for i, p in enumerate(event["media"].get("photos", [])[:3]):
#             with cols[i % 3]:
#                 st.image(p, use_column_width=True)
#
#         # Edit / Delete buttons only when not locked
#         if not st.session_state.current_journey_locked and not is_editing_this:
#             col1, col2 = st.columns([3, 1])
#             with col1:
#                 if st.button("✏️ Edit", key=f"edit_{event['id']}"):
#                     st.session_state.editing_event_id = event["id"]
#                     st.session_state[f"force_open_{event['id']}"] = True
#                     st.rerun()
#
#             with col2:
#                 if st.button("🗑️ Delete", key=f"delete_{event['id']}"):
#                     st.session_state.confirm_delete_id = event["id"]
#                     st.rerun()
#
#         # ── This is the critical part ──
#         # The form MUST be inside this expander block
#         if is_editing_this:
#             st.subheader("Edit this memory")
#
#             # Your editing controls here
#             col_lat, col_lon = st.columns(2)
#             with col_lat:
#                 new_lat = st.number_input("Latitude", value=event["location"]["latitude"],
#                                           format="%.6f", step=0.000001, key=f"lat_{event['id']}")
#             with col_lon:
#                 new_lon = st.number_input("Longitude", value=event["location"]["longitude"],
#                                           format="%.6f", step=0.000001, key=f"lon_{event['id']}")
#
#             new_title = st.text_input("Title", event["title"], key=f"title_{event['id']}")
#             new_date = st.date_input("Date", datetime.strptime(event["date"], "%Y-%m-%d").date(),
#                                      min_value=MIN_DATE, max_value=MAX_DATE, key=f"date_{event['id']}")
#             new_loc_name = st.text_input("Location Name", event["location"]["name"],
#                                          key=f"locname_{event['id']}")
#             new_desc = st.text_area("Description", event.get("description", ""),
#                                     key=f"desc_{event['id']}")
#
#             # Media uploaders...
#             st.file_uploader("Add Photos...", accept_multiple_files=True,
#                              type=["jpg", "jpeg", "png", "gif"], key=f"photos_{event['id']}")
#             st.file_uploader("Add Videos...", accept_multiple_files=True,
#                              type=["mp4", "mov"], key=f"videos_{event['id']}")
#
#             col_save, col_cancel = st.columns(2)
#             with col_save:
#                 if st.button("💾 Save Changes", type="primary", key=f"save_{event['id']}"):
#                     # Update event data...
#                     event["location"]["latitude"] = new_lat
#                     event["location"]["longitude"] = new_lon
#                     event["title"] = new_title
#                     event["date"] = new_date.strftime("%Y-%m-%d")
#                     event["location"]["name"] = new_loc_name
#                     event["description"] = new_desc
#
#                     # Handle file uploads...
#
#                     save_data_to_storage(st.session_state.data)
#                     st.session_state.editing_event_id = None
#                     # Optional: keep expander open after save
#                     # st.session_state[f"force_open_{event['id']}"] = True
#                     st.success("Saved!")
#                     st.rerun()
#
#             with col_cancel:
#                 if st.button("✖ Cancel", key=f"cancel_{event['id']}"):
#                     st.session_state.editing_event_id = None
#                     st.rerun()
#

################# MANUAL EDITING


# # ==================== EDITING EXISTING EVENT ====================
# if st.session_state.editing_event_id:
#     logger.info(f" pass EDITING  EXISTING EVENT")
#     event = next((e for e in st.session_state.data["events"] if e["id"] == st.session_state.editing_event_id), None)
#     if event:
#         if map_data and map_data.get("last_clicked"):
#             click = map_data["last_clicked"]
#             lat, lon = click["lat"], click["lng"]
#             st.session_state.latitude = lat
#             st.session_state.longitude = lon
#             # lat, lon = round(click["lat"], 6), round(click["lng"], 6)
#             st.session_state.default_name = f"{st.session_state.latitude:.5f}, {st.session_state.longitude:.5f}"
#             #st.markdown(f" 1 EDITY lat, lon **{lat}, {lon}**")
#             #st.markdown(f" 2 EDITY lat, lon **{event["location"]["latitude"]}")
#             #st.markdown(f" 3 EDITY lat, lon **{event["location"]["longitude"]}")
#
#         st.sidebar.header(f"✏️ Editing: {event['title']}")
#
#         cur_lat = event["location"]["latitude"]
#         cur_lon = event["location"]["longitude"]
#
#         if st.session_state.latitude == 1.11:
#             st.session_state.latitude = cur_lat
#         if st.session_state.longitude == 1.11:
#             st.session_state.longitude = cur_lon
#
#         st.sidebar.markdown(f"**Current:** Lat {cur_lat:.6f} | Lon {cur_lon:.6f}")
#         #st.sidebar.markdown(f"**Current:** Lat {st.session_state.latitude:.6f} | Lon {st.session_state.longitude:.6f}")
#
#         #new_lat = st.sidebar.number_input("Latitude", value=cur_lat, step=0.000001, format="%.6f")
#         #new_lon = st.sidebar.number_input("Longitude", value=cur_lon, step=0.000001, format="%.6f")
#
#         new_lat = st.sidebar.number_input("Latitude", value=st.session_state.latitude, step=0.000001, format="%.6f")
#         new_lon = st.sidebar.number_input("Longitude", value=st.session_state.longitude, step=0.000001, format="%.6f")
#
#         for mtype, label in [("photos", "Photos"), ("videos", "Videos")]:
#             st.sidebar.markdown(f"### Current {label}")
#             paths = event["media"].get(mtype, []).copy()
#             if paths:
#                 cols = st.sidebar.columns(3 if mtype == "photos" else 2)
#                 for i, p in enumerate(paths):
#                     if os.path.exists(p):
#                         with cols[i % len(cols)]:
#                             if mtype == "photos":
#                                 st.image(p, width=150)
#                             else:
#                                 st.video(p)
#                             if st.button("Remove", key=f"del_{mtype}_{i}_{event['id']}"):
#                                 os.remove(p)
#                                 event["media"][mtype].remove(p)
#                                 # todo JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False),
#                                 #                     encoding="utf-8")
#                                 save_data_to_storage(st.session_state.data)
#                                 st.rerun()
#             else:
#                 st.sidebar.info(f"No {label.lower()}")
#
#         with st.sidebar.form("edit_form"):
#             if map_data and map_data.get("last_clicked"):
#                 click = map_data["last_clicked"]
#                 lat, lon = click["lat"], click["lng"]
#                 #lat, lon = round(click["lat"], 6), round(click["lng"], 6)
#                 #default_name = f"{st.session_state.latitude:.5f}, {st.session_state.longitude:.5f}"
#                 st.session_state.default_name = f"{st.session_state.latitude:.5f}, {st.session_state.longitude:.5f}"
#                 #st.markdown(f" 1 EDITY lat, lon **{lat}, {lon}**")
#                 #st.markdown(f" 2 EDITY lat, lon **{event["location"]["latitude"]}")
#                 #st.markdown(f" 3 EDITY lat, lon **{event["location"]["longitude"]}")
#                 st.session_state.latitude = lat
#                 st.session_state.longitude = lon
#                 pass
#             new_title = st.text_input("Title", event["title"])
#             new_date = st.date_input("Date", datetime.strptime(event["date"], "%Y-%m-%d").date(),
#                                      min_value=datetime(1920, 1, 1).date(),
#                                      max_value=None)
#             #new_loc = st.text_input("Location Name", event["location"]["name"]) #TODO
#             new_loc = st.text_input("Location Name", st.session_state.default_name)
#             new_desc = st.text_area("Description", event.get("description", ""))
#             add_photos = st.file_uploader("Add Photos", accept_multiple_files=True, type=["jpg", "jpeg", "png", "gif","heic","HEIC","heif","HEIF"],
#                                           key=f"add_ph_{event['id']}")
#             add_videos = st.file_uploader("Add Videos", accept_multiple_files=True, type=["mp4", "mov", "webm"],
#                                           key=f"add_vid_{event['id']}")
#
#             if st.form_submit_button("💾 Save Changes", type="primary"):
#                 event["location"]["latitude"] = new_lat
#                 event["location"]["longitude"] = new_lon
#                 event["title"] = new_title
#                 event["date"] = new_date.strftime("%Y-%m-%d")
#                 event["location"]["name"] = new_loc
#                 event["description"] = new_desc
#
#                 # --- Upload new photos (FIXED) ---
#                 for up in add_photos or []:
#                     if up is not None:
#                         fname = f"{int(time.time())}_{up.name}"
#                         try:
#                             file_bytes = up.getvalue()
#                             if not file_bytes:
#                                 continue
#                             if IS_CLOUD:
#                                 url = upload_to_gcs(file_bytes, f"photos/{fname}", up.type)
#                             else:
#                                 local_path = UPLOADS_PHOTOS / fname
#                                 local_path.write_bytes(file_bytes)
#                                 # url = str(local_path)
#                                 url = to_relative_path(local_path)
#                             event["media"].setdefault("photos", []).append(url)
#                         except Exception as e:
#                             st.error(f"Failed to upload photo {up.name}: {e}")
#
#                 # --- Upload new videos (FIXED) ---
#                 for up in add_videos or []:
#                     if up is not None:
#                         fname = f"{int(time.time())}_{up.name}"
#                         try:
#                             file_bytes = up.getvalue()
#                             if not file_bytes:
#                                 continue
#                             if IS_CLOUD:
#                                 url = upload_to_gcs(file_bytes, f"videos/{fname}", up.type)
#                             else:
#                                 local_path = UPLOADS_VIDEOS / fname
#                                 local_path.write_bytes(file_bytes)
#                                 #url = str(local_path)
#                                 url = to_relative_path(local_path)
#                             event["media"].setdefault("videos", []).append(url)
#                         except Exception as e:
#                             st.error(f"Failed to upload video {up.name}: {e}")
#
#                 # todo JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False), encoding="utf-8")
#                 save_data_to_storage(st.session_state.data)
#                 st.session_state.force_map_refresh += 1
#                 st.session_state.editing_event_id = None
#                 st.success("Changes saved!")
#                 st.rerun()
#
#             if st.sidebar.button("Cancel Editing"):
#                 st.session_state.editing_event_id = None
#                 st.rerun()
#
#

















######################## buggy EDITING ######################
if "edit_lat" not in st.session_state:
    st.session_state.edit_lat = None
if "edit_lon" not in st.session_state:
    st.session_state.edit_lon = None

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
                        st.image(p, width="stretch")
                    except Exception as e:
                        st.caption(f"🖼️ Preview unavailable ({os.path.basename(p)})")
                        # Optional: log for debugging
                        # logger.warning(f"Missing media: {p} → {str(e)}")
                    #st.image(p,width="stretch")

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
                    st.rerun()

        # ── Edit form appears RIGHT HERE — under the buttons ──
        if is_editing_this and not st.session_state.current_journey_locked:
            st.markdown("---")
            st.subheader("Edit this memory")


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

                logger.info(f"pass Edit marker {lat_value}   {st.session_state.edit_lat}")
                logger.info(f"pass Edit marker {lon_value} {st.session_state.edit_lon}")

                with col_lat:
                    new_lat = st.number_input(
                        "Latitude",
                        #value=lat_value,
                        value=float(event["location"]["latitude"]),
                        format="%.6f", step=0.000001,
                        key=f"lat_{event['id']}"
                    )
                with col_lon:
                    new_lon = st.number_input(
                        "Longitude",
                        #value=lon_value,
                        value=float(event["location"]["longitude"]),
                        format="%.6f", step=0.000001,
                        key=f"lon_{event['id']}"
                    )

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
                    st.rerun()

                if cancel_clicked:
                    st.session_state.editing_event_id = None
                    st.rerun()


# Confirmation dialog for deletion
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


## ==================== AVAILABLE JOURNEY FILES AS CLICKABLE BUTTONS ====================
# SAFETY CHECK: Ensure selected_json_file always exists in session state
if "selected_json_file" not in st.session_state:
    st.session_state.selected_json_file = DEFAULT_ACTIVE_JSON

# Optional: Support --file argument to pre-select a different journey on launch
#if args.file and (BASE_DIR / args.file).exists():
#    st.session_state.selected_json_file = args.file

# Refresh the list of available JSON files
# local_json_files = get_local_json_files()

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

