import streamlit as st
from streamlit_folium import st_folium
import folium
from folium.plugins import MarkerCluster
import json
import os
import sys
from datetime import datetime
import time
import base64
import logging
from pathlib import Path
import html

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

JSON_FILE = BASE_DIR / "life_events.json"

# ==================== INITIAL DATA SETUP ====================
if not JSON_FILE.exists():
    initial = {
        "autobiography": {
            "title": "My Life Journey",
            "author": "Your Name",
            "created_date": datetime.now().strftime("%Y-%m-%d"),
            "last_updated": datetime.now().strftime("%Y-%m-%d")
        },
        "events": []
    }
    JSON_FILE.write_text(json.dumps(initial, indent=4), encoding="utf-8")

# ==================== CACHED DATA LOADING ====================
@st.cache_data(show_spinner=False)
def load_data_from_file(_file_path: Path):
    try:
        data = json.loads(_file_path.read_text(encoding="utf-8"))
        data["events"] = sorted(data["events"], key=lambda x: x["date"])
        return data
    except Exception as e:
        logger.error(f"Failed to load data: {e}")
        return {
            "autobiography": {
                "title": "My Life Journey",
                "author": "Your Name",
                "created_date": datetime.now().strftime("%Y-%m-%d"),
                "last_updated": datetime.now().strftime("%Y-%m-%d")
            },
            "events": []
        }

if "data" not in st.session_state:
    st.session_state.data = load_data_from_file(JSON_FILE)

data = st.session_state.data

# ==================== SESSION STATE INITIALIZATION ====================
if "editing_event_id" not in st.session_state:
    st.session_state.editing_event_id = None
if "last_clicked_coords" not in st.session_state:
    st.session_state.last_clicked_coords = None
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
    if y < 1990: return "purple"
    elif y < 2000: return "blue"
    elif y < 2010: return "green"
    elif y < 2020: return "orange"
    else: return "red"

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

# ==================== STATIC MAP WITH DIFFERENT FONTS FOR NUMBER & DATE ====================
def create_map(edit_mode=False):
    center = st.session_state.map_center
    zoom = st.session_state.map_zoom
    m = folium.Map(location=center, zoom_start=zoom, tiles="OpenStreetMap")
    cluster = MarkerCluster().add_to(m)

    # Sort events chronologically
    sorted_events = sorted(st.session_state.data["events"], key=lambda x: x["date"])

    for idx, e in enumerate(sorted_events, start=1):
        # Original colored circle marker
        folium.Marker(
            [e["location"]["latitude"], e["location"]["longitude"]],
            popup=folium.Popup(build_popup_html(e), max_width=450),
            tooltip=f"{idx}. {e['title']} ({e['date']})",
            icon=folium.Icon(color=get_color_by_year(e["date"]), icon="circle", prefix="fa"),
            draggable=edit_mode
        ).add_to(cluster)

        # Label with different fonts: bold sans-serif for number, serif for date
        label_html = f"""
        <div style="
            font-size: 14pt;
            color: #333333;
            background: rgba(255, 255, 255, 0.75);
            padding: 6px 12px;
            border-radius: 8px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.15);
            white-space: nowrap;
            border: 1px solid rgba(0,0,0,0.1);
            display: inline-block;
        ">
            <span style="
                font-family: 'Helvetica', 'Arial', sans-serif;
                font-weight: 900;
                font-size: 16pt;
                margin-right: 6px;
            ">{idx}.</span>
            <span style="
                font-family: 'Georgia', 'Times New Roman', serif;
                font-weight: normal;
                font-size: 14pt;
            ">{e['date']}</span>
        </div>
        """

        folium.Marker(
            [e["location"]["latitude"], e["location"]["longitude"]],
            icon=folium.DivIcon(
                html=label_html,
                icon_size=(None, None),
                icon_anchor=(-10, 22)  # Fine-tuned position
            )
        ).add_to(m)

    return m

# ==================== CSS ====================
st.markdown("""
<style>
    .main > div { padding-top: 0rem !important; }
    .block-container { padding-top: 1rem !important; }
    iframe { height: 80vh !important; width: 100% !important; border: none; }
    section[data-testid="stSidebar"] { min-width: 400px !important; width: 400px !important; }
    [data-testid="collapsedControl"] { display: none !important; }
</style>
""", unsafe_allow_html=True)

# ==================== PAGE CONFIG ====================
st.set_page_config(page_title="My Life Journey", layout="wide")
st.title("🌍 My Life Journey – Static Map with Chronological Timeline")
st.markdown("### Number in bold sans-serif • Date in elegant serif • Soft transparent background • Historical feel preserved")

# ==================== EDIT MODE ====================
col_edit, _ = st.columns([1, 5])
with col_edit:
    edit_mode = st.checkbox("✏️ Edit Mode", value=False)
    if edit_mode:
        st.info("Click marker to edit • Drag to move")

# ==================== MAP RENDERING ====================
map_key = f"main_map_{st.session_state.force_map_refresh}"
main_map = create_map(edit_mode=edit_mode)

map_data = st_folium(
    main_map,
    key=map_key,
    width=None,
    height=800,
    returned_objects=["last_clicked", "last_object_clicked", "center", "zoom"]
)

# Preserve view
if map_data and map_data.get("center"):
    st.session_state.map_center = [map_data["center"]["lat"], map_data["center"]["lng"]]
    st.session_state.map_zoom = map_data.get("zoom", 2)

# ==================== MAP INTERACTIONS ====================
if edit_mode and map_data and map_data.get("last_object_clicked"):
    clicked = map_data["last_object_clicked"]
    lat, lon = round(clicked["lat"], 6), round(clicked["lng"], 6)
    st.session_state.last_clicked_coords = (lat, lon)

    best_event = None
    best_dist = float('inf')
    for e in st.session_state.data["events"]:
        dist = abs(e["location"]["latitude"] - lat) + abs(e["location"]["longitude"] - lon)
        if dist < best_dist:
            best_dist = dist
            best_event = e
    if best_event and best_dist < 0.5:
        st.session_state.editing_event_id = best_event["id"]

# Add new memory
if map_data and map_data.get("last_clicked") and not map_data.get("last_object_clicked"):
    click = map_data["last_clicked"]
    lat, lon = round(click["lat"], 6), round(click["lng"], 6)
    default_name = f"{lat:.5f}, {lon:.5f}"

    st.sidebar.header("➕ Add New Memory")
    with st.sidebar.form("add_form"):
        title = st.text_input("Title*", "")
        date = st.date_input("Date*", datetime.today(),
                             min_value=datetime(1930, 1, 1).date(),
                             max_value=datetime.today().date())
        loc_name = st.text_input("Location Name*", default_name)
        description = st.text_area("Description")
        photos = st.file_uploader("Photos", accept_multiple_files=True, type=["jpg", "jpeg", "png", "gif"])
        videos = st.file_uploader("Videos", accept_multiple_files=True, type=["mp4", "mov", "webm"])

        if st.form_submit_button("💾 Save Memory"):
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

# ==================== EDIT / DELETE ====================
if st.session_state.editing_event_id:
    event = next((e for e in st.session_state.data["events"] if e["id"] == st.session_state.editing_event_id), None)
    if event:
        st.sidebar.header(f"✏️ Editing: {event['title']}")
        cur_lat = st.session_state.last_clicked_coords[0] if st.session_state.last_clicked_coords else event["location"]["latitude"]
        cur_lon = st.session_state.last_clicked_coords[1] if st.session_state.last_clicked_coords else event["location"]["longitude"]
        st.sidebar.markdown(f"**Lat:** {cur_lat:.6f} | **Lon:** {cur_lon:.6f}")

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
                                JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False), encoding="utf-8")
                                st.rerun()
            else:
                st.sidebar.info(f"No {label.lower()}")

        with st.sidebar.form("edit_form"):
            new_title = st.text_input("Title", event["title"])
            new_date = st.date_input("Date", datetime.strptime(event["date"], "%Y-%m-%d").date())
            new_loc = st.text_input("Location Name", event["location"]["name"])
            new_desc = st.text_area("Description", event.get("description", ""))
            add_photos = st.file_uploader("Add Photos", accept_multiple_files=True, type=["jpg", "jpeg", "png", "gif"],
                                          key=f"add_ph_{event['id']}")
            add_videos = st.file_uploader("Add Videos", accept_multiple_files=True, type=["mp4", "mov", "webm"],
                                          key=f"add_vid_{event['id']}")

            if st.form_submit_button("💾 Save Changes", type="primary"):
                location_changed = st.session_state.last_clicked_coords is not None
                if location_changed:
                    event["location"]["latitude"], event["location"]["longitude"] = st.session_state.last_clicked_coords

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
                if location_changed:
                    st.session_state.force_map_refresh += 1
                st.session_state.editing_event_id = None
                st.session_state.last_clicked_coords = None
                st.success("Changes saved!")
                st.rerun()

        if st.sidebar.button("Cancel Editing"):
            st.session_state.editing_event_id = None
            st.session_state.last_clicked_coords = None
            st.rerun()

        st.sidebar.markdown("---")
        if st.sidebar.button("🗑️ Delete Memory Permanently", type="secondary"):
            if st.sidebar.checkbox("Confirm permanent deletion", key=f"del_confirm_{event['id']}"):
                for p in event["media"].get("photos", []) + event["media"].get("videos", []):
                    if os.path.exists(p):
                        os.remove(p)
                st.session_state.data["events"] = [e for e in st.session_state.data["events"] if e["id"] != event["id"]]
                JSON_FILE.write_text(json.dumps(st.session_state.data, indent=4, ensure_ascii=False), encoding="utf-8")
                st.session_state.force_map_refresh += 1
                st.session_state.editing_event_id = None
                st.success("Memory deleted")
                st.rerun()

# ==================== SIDEBAR: CHRONOLOGICAL TIMELINE LIST ====================
st.sidebar.markdown("---")
st.sidebar.write(f"**{len(st.session_state.data['events'])} memories**")

sorted_events = sorted(st.session_state.data["events"], key=lambda x: x["date"])
for idx, event in enumerate(sorted_events, start=1):
    with st.sidebar.expander(f"{idx}. {event['date']} — {event['title']}"):
        st.caption(f"📍 {event['location']['name']}")
        for p in event["media"].get("photos", [])[:3]:
            if os.path.exists(p):
                st.image(p, width=200)
        for v in event["media"].get("videos", [])[:1]:
            if os.path.exists(v):
                st.video(v)

st.sidebar.markdown("---")
if st.sidebar.button("💾 Download Backup"):
    with open(JSON_FILE, "rb") as f:
        st.sidebar.download_button("⬇️ Backup JSON", f, "my_life_backup.json", "application/json")

st.caption("Final touch: Number in bold modern sans-serif (Helvetica/Arial) • Date in classic serif (Georgia) • Visual hierarchy improved • Performance unchanged")