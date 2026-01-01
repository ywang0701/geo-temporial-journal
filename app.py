import streamlit as st
from streamlit_folium import st_folium
import streamlit.components.v1 as components
import folium
from folium.plugins import MarkerCluster
from folium.plugins import AntPath, MarkerCluster  # Add AntPath here
import json
import os
import sys
from datetime import datetime, timedelta
import time
import base64
import logging
from pathlib import Path
import html
import argparse

# ==================== LOGGING & PATHS ====================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

UPLOADS_PHOTOS = BASE_DIR / "uploads" / "photos"
UPLOADS_VIDEOS = BASE_DIR / "uploads" / "videos"
UPLOADS_PHOTOS.mkdir(parents=True, exist_ok=True)
UPLOADS_VIDEOS.mkdir(parents=True, exist_ok=True)

import streamlit as st
import streamlit.components.v1 as components  # ← Correct import for current Streamlit

# ==================== DEVICE DETECTION ====================
if "device_type" not in st.session_state:
    detect_js = """
    <script>
        function detectDevice() {
            const width = window.innerWidth;
            const hasTouch = 'ontouchstart' in window || navigator.maxTouchPoints > 0;
            const ua = navigator.userAgent.toLowerCase();
            const isMobileUA = /android|webos|iphone|ipad|ipod|blackberry|iemobile|opera mini/i.test(ua);

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

# For testing only (remove later)
# st.caption(f"Detected: **{st.session_state.device_type.upper()}**")

# Then set the initial sidebar based on device
initial_sidebar = "collapsed" if st.session_state.device_type == "mobile" else "expanded"

# ==================== JSON FILE PATH WITH ARGUMENT SUPPORT ====================
parser = argparse.ArgumentParser(description="My Life Journey App")
parser.add_argument(
    "--file",
    type=str,
    default="life_events.json",
    help="Path to the life events JSON file (default: life_events.json)"
)
args = parser.parse_args()

JSON_FILE = (BASE_DIR / args.file).resolve()

st.sidebar.caption(f"📄 Using data file: `{JSON_FILE.name}`")

# JSON_FILE = BASE_DIR / "life_events.json"

# ==================== DYNAMIC TITLE BASED ON JSON FILENAME ====================
# Get filename without extension and path
json_filename = JSON_FILE.stem  # e.g., "life_events", "my_family_memories", "john_2025"

# Clean up common patterns for nicer display
display_name = json_filename.replace("_", " ").replace("-", " ")
# Capitalize each word
display_name = " ".join(word.capitalize() for word in display_name.split())

# Fallback if somehow empty
if not display_name.strip():
    display_name = "My Journey"



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
        JSON_FILE.write_text(json.dumps(default_data, indent=4, ensure_ascii=False), encoding="utf-8")


ensure_valid_json()


# ==================== CACHED & SAFE DATA LOADING ====================
@st.cache_data(show_spinner=False)
def load_data_from_file(_file_path: Path):
    try:
        text = _file_path.read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError("File is empty")
        data = json.loads(text)
        data["events"] = sorted(data["events"], key=lambda x: x.get("date", "0000-00-00"))
        return data
    except Exception as e:
        logger.warning(f"Corrupted data file detected: {e}. Resetting to default.")
        st.warning("Data file was corrupted or empty. It has been reset to default.")
        default_data = {
            "autobiography": {
                "title": "My Life Journey",
                "author": "Your Name",
                "created_date": datetime.now().strftime("%Y-%m-%d"),
                "last_updated": datetime.now().strftime("%Y-%m-%d")
            },
            "events": []
        }
        _file_path.write_text(json.dumps(default_data, indent=4, ensure_ascii=False), encoding="utf-8")
        return default_data


if "data" not in st.session_state:
    st.session_state.data = load_data_from_file(JSON_FILE)

data = st.session_state.data

# Calculate timeline year range (only if there are events)
timeline_info = ""
if data["events"]:
    sorted_events = sorted(data["events"], key=lambda x: x["date"])
    dates = [datetime.strptime(e["date"], "%Y-%m-%d") for e in sorted_events]
    if dates:
        start_year = min(dates).year
        end_year = max(dates).year
        timeline_info = f" ({start_year} – {end_year})"

# Final dynamic title
#full_title = f"🌍 {display_name} - Map{timeline_info}"
full_title = f"🌍 Life Events - Map {timeline_info}  - test version"
# ==================== SESSION STATE INITIALIZATION ====================
if "editing_event_id" not in st.session_state:
    st.session_state.editing_event_id = None
if "map_center" not in st.session_state:
    st.session_state.map_center = [20, 0]
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = 2
if "force_map_refresh" not in st.session_state:
    st.session_state.force_map_refresh = 0


# ==================== HELPERS ====================
def get_image_base64(p):
    path = Path(p)
    if not path.exists():
        return None
    return base64.b64encode(path.read_bytes()).decode('utf-8')


def get_video_base64(p):
    path = Path(p)
    if not path.exists() or path.stat().st_size > 15 * 1024 * 1024:
        return None
    return base64.b64encode(path.read_bytes()).decode('utf-8')


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
    loc = html.escape(event['location']['name'])

    popup = f"""
    <div style="width:380px;max-height:550px;overflow-y:auto;padding:8px;font-family:sans-serif;">
        <h3 style="text-align:center;margin:0 0 8px 0;">{title}</h3>
        <p style="text-align:center;color:#555;margin:0 0 10px 0;">{event['date']} • {loc}</p>
        <p style="line-height:1.4;margin-bottom:15px;">{desc}</p>
        <hr style="margin:15px 0;">
    """

    photos = event["media"].get("photos", [])
    videos = event["media"].get("videos", [])

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

    if not photos and not videos:
        popup += "<p style='text-align:center;color:#888;'><em>No media</em></p>"

    popup += "</div>"
    return popup


# ==================== MAP CREATION WITH CURVED JOURNEY LINES ====================
def create_map():
    events = st.session_state.data["events"]
    if not events:
        m = folium.Map(location=[20, 0], zoom_start=2, tiles="OpenStreetMap")
        return m

    sorted_events = sorted(events, key=lambda x: x["date"])
    coords = [[e["location"]["latitude"], e["location"]["longitude"]] for e in sorted_events]

    m = folium.Map(tiles="OpenStreetMap")
    cluster = MarkerCluster().add_to(m)

    # Add numbered markers
    for idx, e in enumerate(sorted_events, start=1):
        folium.Marker(
            [e["location"]["latitude"], e["location"]["longitude"]],
            popup=folium.Popup(build_popup_html(e), max_width=450),
            tooltip=f"{idx}. {e['title']} ({e['date']})",
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

        # Optional: Add a subtle static curved base line (great circle feel)
        folium.PolyLine(
            locations=coords,
            weight=3,
            color="#4A90E2",
            opacity=0.4,
            smooth_factor=50           # Very high for natural Earth curve
        ).add_to(m)

    m.fit_bounds(coords, padding=(80, 80))
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

#st.title("🌍 My Life Journey – Map with Colored Timeline")

st.title(full_title)

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

            timeline_html += f'<div class="timeline-tick" style="left: {position}%;"></div>'
            timeline_html += f'''
            <div class="timeline-label-frame" style="left: {position}%;">
                <div class="timeline-label">
                    <strong>{idx}.</strong> <span>{event["date"]}</span>
                    <div class="timeline-title">{escaped_title}</div>
                </div>
            </div>
            '''

        timeline_html += '</div>'
        st.markdown(timeline_html, unsafe_allow_html=True)

        years_span = (max(dates) - min(dates)).days // 365
        st.caption(f"Events span ~{years_span} years • Hover on label frame to show memory title")

        st.markdown("</div>", unsafe_allow_html=True)
else:
    st.info("Add memories to see the extended timeline.")

# ==================== MAP ====================
map_key = f"main_map_{st.session_state.force_map_refresh}"
main_map = create_map()

map_data = st_folium(
    main_map,
    key=map_key,
    width=None,
    height=1200,
    use_container_width=True,
    returned_objects=["last_clicked"]
    #returned_objects = ["last_clicked", "center", "zoom"]
)
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
if st.session_state.app_mode == "Edit Mode" and map_data and map_data.get("last_clicked"):
    click = map_data["last_clicked"]
    lat, lon = round(click["lat"], 6), round(click["lng"], 6)
    default_name = f"{lat:.5f}, {lon:.5f}"

    st.sidebar.header("➕ Add New Memory")
    with st.sidebar.form("add_form", clear_on_submit=False):
        title = st.text_input("Title*", "")
        date = st.date_input("Date*", datetime.today(),
                             min_value=datetime(1930, 1, 1).date(),
                             max_value=None)
        loc_name = st.text_input("Location Name*", default_name)
        description = st.text_area("Description")
        photos = st.file_uploader("Photos", accept_multiple_files=True, type=["jpg", "jpeg", "png", "gif"])
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
                    path = UPLOADS_PHOTOS / fname
                    path.write_bytes(up.getbuffer())
                    photo_paths.append(str(path))
                video_paths = []
                for up in videos or []:
                    fname = f"{int(time.time())}_{up.name}"
                    path = UPLOADS_VIDEOS / fname
                    path.write_bytes(up.getbuffer())
                    video_paths.append(str(path))

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
                JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False), encoding="utf-8")
                st.session_state.force_map_refresh += 1
                st.success("Memory added!")
                st.rerun()

        if cancel_clicked:
            st.rerun()

# ==================== EDITING EXISTING EVENT ====================
if st.session_state.editing_event_id:
    event = next((e for e in st.session_state.data["events"] if e["id"] == st.session_state.editing_event_id), None)
    if event:
        st.sidebar.header(f"✏️ Editing: {event['title']}")

        cur_lat = event["location"]["latitude"]
        cur_lon = event["location"]["longitude"]
        st.sidebar.markdown(f"**Current:** Lat {cur_lat:.6f} | Lon {cur_lon:.6f}")

        new_lat = st.sidebar.number_input("Latitude", value=cur_lat, step=0.000001, format="%.6f")
        new_lon = st.sidebar.number_input("Longitude", value=cur_lon, step=0.000001, format="%.6f")

        for mtype, label in [("photos", "Photos"), ("videos", "Videos")]:
            st.sidebar.markdown(f"### Current {label}")
            paths = event["media"].get(mtype, []).copy()
            if paths:
                cols = st.sidebar.columns(3 if mtype == "photos" else 2)
                for i, p in enumerate(paths):
                    if os.path.exists(p):
                        with cols[i % len(cols)]:
                            if mtype == "photos":
                                st.image(p, width=150)
                            else:
                                st.video(p)
                            if st.button("Remove", key=f"del_{mtype}_{i}_{event['id']}"):
                                os.remove(p)
                                event["media"][mtype].remove(p)
                                JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False),
                                                     encoding="utf-8")
                                st.rerun()
            else:
                st.sidebar.info(f"No {label.lower()}")

        with st.sidebar.form("edit_form"):
            new_title = st.text_input("Title", event["title"])
            new_date = st.date_input("Date", datetime.strptime(event["date"], "%Y-%m-%d").date(),
                                     min_value=datetime(1920, 1, 1).date(),
                                     max_value=None)
            new_loc = st.text_input("Location Name", event["location"]["name"])
            new_desc = st.text_area("Description", event.get("description", ""))
            add_photos = st.file_uploader("Add Photos", accept_multiple_files=True, type=["jpg", "jpeg", "png", "gif"],
                                          key=f"add_ph_{event['id']}")
            add_videos = st.file_uploader("Add Videos", accept_multiple_files=True, type=["mp4", "mov", "webm"],
                                          key=f"add_vid_{event['id']}")

            if st.form_submit_button("💾 Save Changes", type="primary"):
                event["location"]["latitude"] = new_lat
                event["location"]["longitude"] = new_lon
                event["title"] = new_title
                event["date"] = new_date.strftime("%Y-%m-%d")
                event["location"]["name"] = new_loc
                event["description"] = new_desc

                for up in add_photos or []:
                    fname = f"{int(time.time())}_{up.name}"
                    path = UPLOADS_PHOTOS / fname
                    path.write_bytes(up.getbuffer())
                    event["media"]["photos"].append(str(path))
                for up in add_videos or []:
                    fname = f"{int(time.time())}_{up.name}"
                    path = UPLOADS_VIDEOS / fname
                    path.write_bytes(up.getbuffer())
                    event["media"]["videos"].append(str(path))

                JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False), encoding="utf-8")
                st.session_state.force_map_refresh += 1
                st.session_state.editing_event_id = None
                st.success("Changes saved!")
                st.rerun()

        if st.sidebar.button("Cancel Editing"):
            st.session_state.editing_event_id = None
            st.rerun()

# ==================== SIDEBAR SUMMARY WITH EDIT AND DELETE BUTTONS ====================
st.sidebar.markdown("---")
st.sidebar.write(f"**{len(st.session_state.data['events'])} memories**")

sorted_events = sorted(st.session_state.data["events"], key=lambda x: x["date"])
for idx, event in enumerate(sorted_events, start=1):
    with st.sidebar.expander(f"{idx}. {event['date']} — {event['title']}", expanded=False):
        st.caption(f"📍 {event['location']['name']}")
        for p in event["media"].get("photos", [])[:3]:
            if os.path.exists(p):
                st.image(p, width=200)
        for v in event["media"].get("videos", [])[:1]:
            if os.path.exists(v):
                st.video(v)

        # Edit and Delete buttons side by side
        col_edit, col_delete = st.columns([2, 1])
        with col_edit:
            if st.button("✏️ Edit", key=f"edit_sidebar_{event['id']}"):
                st.session_state.editing_event_id = event["id"]
                st.rerun()
        with col_delete:
            if st.button("🗑️ Delete", key=f"delete_sidebar_{event['id']}"):
                st.session_state.confirm_delete_id = event["id"]
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
                            for p in event["media"].get("photos", []) + event["media"].get("videos", []):
                                if os.path.exists(p):
                                    os.remove(p)
                            st.session_state.data["events"] = [e for e in st.session_state.data["events"] if
                                                               e["id"] != event["id"]]
                            JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False),
                                                 encoding="utf-8")
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

st.sidebar.markdown("---")
# Initialize mode in session state if not exists
if "app_mode" not in st.session_state:
    st.session_state.app_mode = "View Mode"  # Default

col_mode, col_download = st.sidebar.columns([1, 1])

with col_mode:
    mode = st.radio(
        "Mode:",
        options=["View Mode", "Edit Mode"],
        index=0 if st.session_state.app_mode == "View Mode" else 1,
        horizontal=False,
        label_visibility="collapsed",
        key="mode_radio"  # Important: give it a key so Streamlit tracks it
    )

    # Update session state when user changes mode
    if mode != st.session_state.app_mode:
        st.session_state.app_mode = mode
        st.rerun()  # TODO Optional: force immediate refresh for snappy feel

with col_download:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("💾 Backup Memories"):
        with open(JSON_FILE, "rb") as f:
            st.download_button(
                "⬇️ Backup JSON",
                f,
                file_name=f"{JSON_FILE.stem}_backup_{datetime.now().strftime('%Y%m%d')}.json",
                mime="application/json"
            )

# ==================== UPLOAD & RESTORE JSON (FIXED - USES CORRECT LOAD FUNCTION) ====================
st.sidebar.markdown("---")
with st.sidebar.expander("📤 Upload & Restore Journey", expanded=False):
    st.write("Restore a previously backed-up `.json` file. This will **replace** the current journey's data.")

    uploaded_file = st.file_uploader(
        "Select a backup JSON file to restore",
        type=["json"],
        key="json_restore_uploader"
    )

    if uploaded_file is not None:
        try:
            # Read and validate uploaded JSON
            uploaded_bytes = uploaded_file.read()
            uploaded_data = json.loads(uploaded_bytes.decode("utf-8"))

            if not all(key in uploaded_data for key in ["autobiography", "events"]):
                st.error("Invalid backup: missing 'autobiography' or 'events' section.")
            elif not isinstance(uploaded_data["events"], list):
                st.error("Invalid backup: 'events' must be a list.")
            else:
                title = uploaded_data["autobiography"].get("title", "Untitled Journey")
                event_count = len(uploaded_data["events"])
                st.success(f"Valid backup: **{title}** ({event_count} memories)")

                st.warning(f"⚠️ This will **replace all data** in the current journey:\n\n**{JSON_FILE.name}**")

                col1, col2 = st.columns(2)
                with col1:
                    if st.button("✅ Yes, Restore Now", type="primary", use_container_width=True):
                        # 1. Overwrite the current JSON file
                        JSON_FILE.write_bytes(uploaded_bytes)

                        # 2. Update current journey path
                        st.session_state.current_json_path = JSON_FILE

                        # 3. Clear cache and reload data properly
                        load_data_from_file.clear()  # This clears the @st.cache_data
                        st.session_state.data = load_data_from_file(JSON_FILE)

                        # 4. Force full refresh
                        st.session_state.force_map_refresh = st.session_state.get("force_map_refresh", 0) + 1
                        if "map_refresh_key" in st.session_state:
                            st.session_state.map_refresh_key += 1

                        # 5. Reset map view
                        for key in ["map_center", "map_zoom"]:
                            if key in st.session_state:
                                del st.session_state[key]

                        # 6. Clear editing state
                        if "editing_event_id" in st.session_state:
                            del st.session_state.editing_event_id

                        st.success(f"✅ Journey restored successfully!\n\nNow viewing: **{title}**")
                        st.rerun()

                with col2:
                    if st.button("❌ Cancel", type="secondary", use_container_width=True):
                        st.info("Restore cancelled.")

            # ==================== TEMPORARY REFRESH BANNER AFTER RESTORE ====================
            if st.session_state.get("refresh_banner", False):
                start_time = st.session_state.get("banner_start_time", time.time())
                elapsed = time.time() - start_time

                if elapsed < 5:  # Show for 5 seconds
                    st.markdown(
                        """
                        <div style="
                            position: fixed;
                            top: 100px;
                            left: 50%;
                            transform: translateX(-50%);
                            background: #ff4b4b;
                            color: white;
                            padding: 16px 32px;
                            border-radius: 12px;
                            font-size: 18px;
                            font-weight: bold;
                            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
                            z-index: 10000;
                            text-align: center;
                        ">
                            🔄 Please refresh the webpage to see the updated journey
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                    # Force rerun to update timer
                    time.sleep(0.1)
                    st.rerun()
                else:
                    # Time's up → remove banner
                    st.session_state.refresh_banner = False
                    if "banner_start_time" in st.session_state:
                        del st.session_state.banner_start_time
                    st.rerun()
        except json.JSONDecodeError:
            st.error("Invalid JSON file — could not parse.")
        except Exception as e:
            st.error(f"Error: {e}")

st.caption("Delete button now placed next to Edit in the memory list • Safe confirmation required")