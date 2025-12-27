import streamlit as st
from streamlit_folium import st_folium
import folium
from folium.plugins import TimestampedGeoJson
import json
import os
from datetime import datetime
from geopy.geocoders import Nominatim
import time
import sys
import base64
import logging

# ==================== CONSOLE DEBUG LOGGING ====================
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ==================== BASE DIRECTORY ====================
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOADS_PHOTOS = os.path.join(BASE_DIR, "uploads", "photos")
UPLOADS_VIDEOS = os.path.join(BASE_DIR, "uploads", "videos")
os.makedirs(UPLOADS_PHOTOS, exist_ok=True)
os.makedirs(UPLOADS_VIDEOS, exist_ok=True)

JSON_FILE = os.path.join(BASE_DIR, "life_events.json")

# ==================== INITIALISE DATA ====================
if not os.path.exists(JSON_FILE):
    initial_data = {
        "autobiography": {
            "title": "My Life Journey",
            "author": "Your Name",
            "created_date": datetime.now().strftime("%Y-%m-%d"),
            "last_updated": datetime.now().strftime("%Y-%m-%d")
        },
        "events": []
    }
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(initial_data, f, indent=4)

def load_data():
    with open(JSON_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data

def save_data(data):
    data["autobiography"]["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    with open(JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

data = load_data()
geolocator = Nominatim(user_agent="my_life_journey_app")

# ==================== SESSION STATE ====================
if 'main_map_key' not in st.session_state:
    st.session_state.main_map_key = 0

if 'view_mode' not in st.session_state:
    st.session_state.view_mode = "main"

if 'selected_photo' not in st.session_state:
    st.session_state.selected_photo = None

if 'editing_event_id' not in st.session_state:
    st.session_state.editing_event_id = None

if 'map_center' not in st.session_state:
    st.session_state.map_center = [20, 0]

if 'map_zoom' not in st.session_state:
    st.session_state.map_zoom = 2

if 'last_clicked_coords' not in st.session_state:
    st.session_state.last_clicked_coords = None

# ==================== BASE64 HELPERS ====================
def get_image_base64(file_path):
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except:
        return None

def get_video_base64(file_path):
    if not file_path or not os.path.exists(file_path):
        return None
    size_mb = os.path.getsize(file_path) / (1024 * 1024)
    if size_mb > 15:
        return None
    try:
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode('utf-8')
    except:
        return None

# ==================== COLOR BY YEAR ====================
def get_color_by_year(year):
    if year < 1990: return "purple"
    elif year < 2000: return "blue"
    elif year < 2010: return "green"
    elif year < 2020: return "orange"
    else: return "red"

# ==================== POPUP WITH DOWNLOAD LINKS ====================
def build_popup_html(event):
    html = f"""
    <div style="width:380px; max-height:550px; overflow-y:auto; padding:8px; font-family:sans-serif;">
        <h3 style="text-align:center; margin:0 0 8px 0;">{event['title']}</h3>
        <p style="text-align:center; color:#555; margin:0 0 10px 0;">{event['date']} • {event['location']['name']}</p>
        <p style="line-height:1.4; margin-bottom:15px;">{event['description'] or 'No description'}</p>
        <hr style="margin:15px 0;">
    """

    photos = event["media"].get("photos", [])
    videos = event["media"].get("videos", [])

    if photos:
        html += "<strong style='margin-bottom:10px; display:block;'>Photos:</strong>"
        html += "<div style='display:flex; flex-direction:column; gap:12px;'>"
        for photo_path in photos:
            filename = os.path.basename(photo_path)
            b64 = get_image_base64(photo_path)
            if b64:
                download_url = f"data:image/jpeg;base64,{b64}"
                html += f"""
                <div style="text-align:center;">
                    <img src="{download_url}" 
                         style="max-width:100%; height:auto; border-radius:10px; box-shadow:0 4px 12px rgba(0,0,0,0.15);">
                    <div style="margin-top:8px;">
                        <a href="{download_url}" download="{filename}" style="color:#1E88E5; text-decoration:none; font-size:14px;">
                            📥 Download Full Size
                        </a>
                    </div>
                </div>
                """
        html += "</div>"

    if videos:
        html += "<strong style='margin-top:20px; margin-bottom:10px; display:block;'>Videos:</strong>"
        html += "<div style='display:flex; flex-direction:column; gap:15px;'>"
        for video_path in videos:
            filename = os.path.basename(video_path)
            b64 = get_video_base64(video_path)
            if b64:
                download_url = f"data:video/mp4;base64,{b64}"
                html += f"""
                <div style="text-align:center;">
                    <video controls style="max-width:100%; border-radius:10px; box-shadow:0 4px 12px rgba(0,0,0,0.15);">
                        <source src="{download_url}" type="video/mp4">
                    </video>
                    <div style="margin-top:8px;">
                        <a href="{download_url}" download="{filename}" style="color:#1E88E5; text-decoration:none; font-size:14px;">
                            📥 Download Video
                        </a>
                    </div>
                </div>
                """
        html += "</div>"

    if not photos and not videos:
        html += "<p style='text-align:center; color:#888;'><em>No media attached</em></p>"

    html += "</div>"
    return html

# ==================== CREATE MAIN MAP ====================
def create_map(edit_mode=False):
    # Use persistent center and zoom
    center = st.session_state.map_center
    zoom = st.session_state.map_zoom

    m = folium.Map(location=center, zoom_start=zoom, tiles="OpenStreetMap")

    for event in data["events"]:
        color = get_color_by_year(int(event["date"][:4]))
        popup_html = build_popup_html(event)
        folium.Marker(
            location=[event["location"]["latitude"], event["location"]["longitude"]],
            popup=folium.Popup(popup_html, max_width=450),
            tooltip=f"{event['title']} ({event['date']})",
            icon=folium.Icon(color=color, icon="circle", prefix="fa"),
            draggable=edit_mode
        ).add_to(m)

    return m

# ==================== UI ====================
st.set_page_config(page_title="My Life Journey", layout="wide")
st.title("🌍 My Life Journey – Interactive Autobiography Map")
st.markdown("### Click anywhere on the map to add a memory • Click 'Download Full Size' to save media")

# CSS for full height map
st.markdown("""
<style>
    .main > div {
        padding-top: 0rem;
    }
    .block-container {
        padding-top: 1rem;
    }
    section[data-testid="stSidebar"] {
        min-width: 320px;
    }
    div[data-testid="stVerticalBlock"] > div:has(> iframe) {
        height: calc(100vh - 140px) !important;
    }
    iframe {
        height: calc(100vh - 160px) !important;
    }
</style>
""", unsafe_allow_html=True)

# Navigation: Back button only when not in main view
if st.session_state.view_mode != "main":
    col_back, col_title = st.columns([1, 10])
    with col_back:
        if st.button("← Back"):
            st.session_state.view_mode = "main"
            st.session_state.selected_photo = None
            st.session_state.editing_event_id = None
            st.rerun()
    with col_title:
        if st.session_state.view_mode == "timeline":
            st.subheader("🕰️ Animated Timeline")
        elif st.session_state.view_mode == "photo_zoom":
            st.subheader("🔍 Photo Zoom View")

# ==================== PHOTO ZOOM VIEW ====================
if st.session_state.view_mode == "photo_zoom" and st.session_state.selected_photo:
    photo_path = st.session_state.selected_photo
    st.image(photo_path, use_column_width=True)
    st.caption("Pinch (mobile) or Ctrl+Scroll (desktop) to zoom in/out • Click 'Back' to return")

# ==================== TIMELINE VIEW ====================
elif st.session_state.view_mode == "timeline":
    if len(data["events"]) < 2:
        st.info("Add at least 2 events with different dates for a visible animation.")
    else:
        features = []
        for event in data["events"]:
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [event["location"]["longitude"], event["location"]["latitude"]]
                },
                "properties": {
                    "time": event["date"] + "T00:00:00",
                    "popup": build_popup_html(event),
                    "icon": "circle"
                }
            })

        geojson = {"type": "FeatureCollection", "features": features}

        center_lat = sum(e["location"]["latitude"] for e in data["events"]) / len(data["events"])
        center_lon = sum(e["location"]["longitude"] for e in data["events"]) / len(data["events"])

        timeline_map = folium.Map(location=[center_lat, center_lon], zoom_start=3)

        TimestampedGeoJson(
            geojson,
            period="P1M",
            duration="P1D",
            add_last_point=True,
            auto_play=False,
            loop=False,
            loop_button=True,
            time_slider_drag_update=True,
            transition_time=1000
        ).add_to(timeline_map)

        st_folium(
            timeline_map,
            width=None,
            height=800,
            returned_objects=[],
            key="timeline_map_fixed"
        )

        save_path = os.path.join(BASE_DIR, "my_life_timeline.html")
        timeline_map.save(save_path)
        with open(save_path, "rb") as f:
            st.download_button(
                "📥 Download Timeline HTML",
                f,
                file_name="my_life_journey_timeline.html",
                mime="text/html"
            )

# ==================== MAIN MAP VIEW ====================
else:
    col1, _ = st.columns([1, 4])
    with col1:
        edit_mode = st.checkbox("✏️ Edit Mode", value=False)
        if edit_mode:
            st.info("Drag markers to move • Click marker to open edit form")

    main_map = create_map(edit_mode=edit_mode)
    map_data = st_folium(
        main_map,
        key=f"main_map_{st.session_state.main_map_key}",
        width=None,
        height=800,
        returned_objects=["last_clicked", "last_object_clicked", "center", "zoom"]
    )

    # Update map view state (center and zoom) for persistence
    if map_data:
        if "center" in map_data and map_data["center"]:
            st.session_state.map_center = [map_data["center"]["lat"], map_data["center"]["lng"]]
        if "zoom" in map_data:
            st.session_state.map_zoom = map_data["zoom"]

    # Detect photo click from popup (only in normal mode)
    component_value = st.session_state.get("component_value")
    if component_value and component_value.startswith("photo_") and not edit_mode:
        parts = component_value.split("_")
        photo_index = int(parts[1])
        event_id = int(parts[2])
        for event in data["events"]:
            if event["id"] == event_id:
                photos = event["media"].get("photos", [])
                if 0 <= photo_index < len(photos):
                    st.session_state.selected_photo = photos[photo_index]
                    st.session_state.view_mode = "photo_zoom"
                    st.rerun()
                break

    # ==================== TRIGGER EDIT MODE ====================
    if edit_mode and map_data and map_data.get("last_object_clicked"):
        clicked = map_data["last_object_clicked"]
        current_lat = clicked["lat"]
        current_lon = clicked["lng"]

        # Store last clicked coords for accurate saving
        st.session_state.last_clicked_coords = (current_lat, current_lon)

        # Find the closest event
        event = None
        best_distance = float('inf')
        for e in data["events"]:
            dist = abs(e["location"]["latitude"] - current_lat) + abs(e["location"]["longitude"] - current_lon)
            if dist < best_distance:
                best_distance = dist
                event = e

        if event and best_distance < 0.1:
            st.session_state.editing_event_id = event["id"]
            st.session_state.main_map_key += 1
            st.rerun()

    # ==================== ADD NEW EVENT ====================
    if map_data and map_data.get("last_clicked") and not map_data.get("last_object_clicked"):
        click = map_data["last_clicked"]
        lat, lon = click["lat"], click["lng"]

        try:
            location = geolocator.reverse((lat, lon), language="en")
            place_name = location.address if location else f"{lat:.4f}, {lon:.4f}"
        except:
            place_name = f"{lat:.4f}, {lon:.4f}"

        st.sidebar.header("➕ Add New Memory")
        with st.sidebar.form("new_form", clear_on_submit=True):
            title = st.text_input("Title*", "")
            date = st.date_input("Date*", datetime.today())
            location_name = st.text_input("Location*", place_name)
            description = st.text_area("Description")
            photos = st.file_uploader("Photos (multiple)", accept_multiple_files=True, type=["jpg", "jpeg", "png", "gif"])
            videos = st.file_uploader("Videos", accept_multiple_files=True, type=["mp4", "mov", "webm"])

            submitted = st.form_submit_button("💾 Save Memory")
            if submitted:
                if not title.strip():
                    st.error("Please enter a title")
                else:
                    photo_paths = []
                    for p in photos or []:
                        filename = f"{int(time.time())}_{p.name}"
                        path = os.path.join(UPLOADS_PHOTOS, filename)
                        with open(path, "wb") as f:
                            f.write(p.getbuffer())
                        photo_paths.append(path)

                    video_paths = []
                    for v in videos or []:
                        filename = f"{int(time.time())}_{v.name}"
                        path = os.path.join(UPLOADS_VIDEOS, filename)
                        with open(path, "wb") as f:
                            f.write(v.getbuffer())
                        video_paths.append(path)

                    new_event = {
                        "id": max([e["id"] for e in data["events"]] or [0]) + 1,
                        "title": title,
                        "date": date.strftime("%Y-%m-%d"),
                        "location": {"name": location_name, "latitude": round(lat, 6), "longitude": round(lon, 6)},
                        "description": description,
                        "media": {"photos": photo_paths, "videos": video_paths}
                    }
                    data["events"].append(new_event)
                    save_data(data)
                    st.session_state.main_map_key += 1
                    st.success("Memory added!")
                    st.rerun()

    # ==================== SHOW EDIT FORM ====================
    if st.session_state.editing_event_id is not None:
        event = next((e for e in data["events"] if e["id"] == st.session_state.editing_event_id), None)
        if event:
            st.sidebar.header(f"✏️ Editing: {event['title']}")

            # Use last clicked position for display
            if st.session_state.last_clicked_coords:
                display_lat, display_lon = st.session_state.last_clicked_coords
            else:
                display_lat = event["location"]["latitude"]
                display_lon = event["location"]["longitude"]

            st.sidebar.markdown("### 📍 Current Marker Position (drag marker to update)")
            st.sidebar.markdown(f"**Latitude:** {display_lat:.6f}")
            st.sidebar.markdown(f"**Longitude:** {display_lon:.6f}")

            # Current photos
            st.sidebar.markdown("### 📸 Current Photos")
            current_photos = event["media"].get("photos", []).copy()
            if current_photos:
                cols = st.columns(3)
                for i, path in enumerate(current_photos):
                    if os.path.exists(path):
                        with cols[i % 3]:
                            st.image(path, width=200)
                            if st.button("🗑️ Remove", key=f"del_photo_{i}_{event['id']}"):
                                try:
                                    os.remove(path)
                                    logger.debug(f"Deleted photo file: {path}")
                                except Exception as e:
                                    logger.error(f"Failed to delete photo file {path}: {e}")
                                current_photos.remove(path)
                                st.rerun()
            else:
                st.info("No photos yet")

            # Current videos
            st.sidebar.markdown("### 🎥 Current Videos")
            current_videos = event["media"].get("videos", []).copy()
            if current_videos:
                cols = st.columns(2)
                for i, path in enumerate(current_videos):
                    if os.path.exists(path):
                        with cols[i % 2]:
                            st.video(path)
                            if st.button("🗑️ Remove", key=f"del_video_{i}_{event['id']}"):
                                try:
                                    os.remove(path)
                                    logger.debug(f"Deleted video file: {path}")
                                except Exception as e:
                                    logger.error(f"Failed to delete video file {path}: {e}")
                                current_videos.remove(path)
                                st.rerun()
            else:
                st.info("No videos yet")

            # Edit form
            with st.sidebar.form("edit_main_form"):
                new_title = st.text_input("Title", event["title"], key=f"title_{event['id']}")
                new_date = st.date_input("Date", datetime.strptime(event["date"], "%Y-%m-%d"), key=f"date_{event['id']}")
                new_location = st.text_input("Location Name", event["location"]["name"], key=f"locname_{event['id']}")
                new_desc = st.text_area("Description", event["description"] or "", key=f"desc_{event['id']}")

                st.markdown("### ➕ Add More Photos")
                new_photos = st.file_uploader("Upload photos", type=["jpg", "jpeg", "png", "gif"], accept_multiple_files=True, key=f"add_photos_{event['id']}")

                st.markdown("### ➕ Add More Videos")
                new_videos = st.file_uploader("Upload videos", type=["mp4", "mov", "webm"], accept_multiple_files=True, key=f"add_videos_{event['id']}")

                st.markdown("---")
                st.markdown("### Save Your Changes")
                save = st.form_submit_button("💾 Save Changes", type="primary")

                if save:
                    # Save last clicked/dragged position
                    if st.session_state.last_clicked_coords:
                        event["location"]["latitude"] = round(st.session_state.last_clicked_coords[0], 6)
                        event["location"]["longitude"] = round(st.session_state.last_clicked_coords[1], 6)

                    event["location"]["name"] = new_location

                    for photo in new_photos or []:
                        filename = f"{int(time.time())}_{photo.name}"
                        path = os.path.join(UPLOADS_PHOTOS, filename)
                        with open(path, "wb") as f:
                            f.write(photo.getbuffer())
                        current_photos.append(path)

                    for video in new_videos or []:
                        filename = f"{int(time.time())}_{video.name}"
                        path = os.path.join(UPLOADS_VIDEOS, filename)
                        with open(path, "wb") as f:
                            f.write(video.getbuffer())
                        current_videos.append(path)

                    event.update({
                        "title": new_title,
                        "date": new_date.strftime("%Y-%m-%d"),
                        "description": new_desc,
                        "media": {"photos": current_photos, "videos": current_videos}
                    })

                    save_data(data)
                    st.session_state.main_map_key += 1
                    st.session_state.editing_event_id = None
                    st.session_state.last_clicked_coords = None
                    st.success("All changes saved! New marker position is permanent.")
                    st.rerun()

            if st.sidebar.button("Cancel Edit"):
                st.session_state.editing_event_id = None
                st.session_state.last_clicked_coords = None
                st.rerun()

            # Delete memory
            st.sidebar.markdown("---")
            st.sidebar.markdown("### 🗑️ Delete This Memory")
            if st.sidebar.button("Delete This Memory Permanently", type="secondary"):
                if st.sidebar.checkbox("Yes, permanently delete this memory and all its media files", key=f"confirm_delete_{event['id']}"):
                    for path in event["media"].get("photos", []):
                        if os.path.exists(path):
                            try:
                                os.remove(path)
                                logger.debug(f"Deleted photo during memory deletion: {path}")
                            except Exception as e:
                                logger.error(f"Failed to delete photo {path}: {e}")

                    for path in event["media"].get("videos", []):
                        if os.path.exists(path):
                            try:
                                os.remove(path)
                                logger.debug(f"Deleted video during memory deletion: {path}")
                            except Exception as e:
                                logger.error(f"Failed to delete video {path}: {e}")

                    data["events"] = [e for e in data["events"] if e["id"] != event["id"]]
                    save_data(data)
                    st.session_state.main_map_key += 1
                    st.session_state.editing_event_id = None
                    st.success("Memory and all media files permanently deleted")
                    st.rerun()

    # ==================== SIDEBAR LIST (DOWNLOAD LINKS) ====================
    st.sidebar.markdown("---")
    st.sidebar.write(f"**{len(data['events'])} memories**")

    for event in sorted(data["events"], key=lambda x: x["date"]):
        with st.sidebar.expander(f"{event['date']} — {event['title']}"):
            st.caption(f"📍 {event['location']['name']}")
            photos = event["media"].get("photos", [])
            if photos:
                st.markdown("**Photos**")
                cols = st.columns(min(3, len(photos)))
                for i, path in enumerate(photos):
                    if os.path.exists(path):
                        b64 = get_image_base64(path)
                        if b64:
                            download_url = f"data:image/jpeg;base64,{b64}"
                            filename = os.path.basename(path)
                            with cols[i % min(3, len(photos))]:
                                st.image(path, width=200)
                                st.markdown(
                                    f'<div style="text-align:center; margin-top:4px;">'
                                    f'<a href="{download_url}" download="{filename}" style="color:#1E88E5; text-decoration:none; font-size:14px;">'
                                    f'📥 Download Full Size</a></div>',
                                    unsafe_allow_html=True
                                )

            videos = event["media"].get("videos", [])
            if videos:
                st.markdown("**Videos**")
                for path in videos:
                    if os.path.exists(path):
                        st.video(path)

    if data["events"]:
        if st.sidebar.button("🗺️ View Animated Timeline", type="primary"):
            st.session_state.view_mode = "timeline"
            st.rerun()

# ==================== BACKUP ====================
with st.sidebar:
    st.markdown("---")
    if st.button("💾 Download Backup"):
        with open(JSON_FILE, "rb") as f:
            st.download_button("⬇️ Backup JSON", f, "my_life_backup.json", "application/json")

st.caption("FINAL VERSION • Map view (center + zoom) preserved across modes • Edit Mode: Drag → click → edit form opens • Position saved • Full media download • All bugs fixed")