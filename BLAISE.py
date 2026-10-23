import os
import sys
import json
import sqlite3
import re
import threading
import time
from difflib import SequenceMatcher
from typing import List, Dict, Optional, Tuple

# UI framework imports with safe fallback layout
try:
    import customtkinter as ctk
    ctk.set_appearance_mode("Dark")
    ctk.set_default_color_theme("blue")
    CTK_AVAILABLE = True
except ImportError:
    import tkinter as ctk
    CTK_AVAILABLE = False

# Primary network stream audio engine (VLC) with Pygame fallback
try:
    import vlc
    VLC_AVAILABLE = True
except ImportError:
    VLC_AVAILABLE = False

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False

# Stream extractor (yt-dlp)
try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False


def extract_youtube_id(url: str) -> Optional[str]:
    """Extracts standard 11-character YouTube video ID from various URL formats."""
    match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) if match else None


# ============================================================================
# 1. LOCAL PERSISTENT CACHE & DATABASE
# ============================================================================
class blaiseDatabase:
    """Handles persistent caching for resolved tracks and user manual overrides."""
    def __init__(self, db_path: str = "blaise_cache.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS track_matches (
                    spotify_id TEXT PRIMARY KEY,
                    yt_video_id TEXT NOT NULL,
                    yt_url TEXT NOT NULL,
                    is_manual_override INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def get_match(self, spotify_id: str) -> Optional[Tuple[str, str, bool]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT yt_video_id, yt_url, is_manual_override FROM track_matches WHERE spotify_id = ?", (spotify_id,))
            row = cursor.fetchone()
            if row:
                return row[0], row[1], bool(row[2])
        return None

    def save_match(self, spotify_id: str, yt_video_id: str, yt_url: str, is_manual: bool = False):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO track_matches (spotify_id, yt_video_id, yt_url, is_manual_override)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(spotify_id) DO UPDATE SET
                    yt_video_id=excluded.yt_video_id,
                    yt_url=excluded.yt_url,
                    is_manual_override=excluded.is_manual_override,
                    updated_at=CURRENT_TIMESTAMP
            """, (spotify_id, yt_video_id, yt_url, int(is_manual)))
            conn.commit()


# ============================================================================
# 2. SPOTIFY DISCOVERY ENGINE
# ============================================================================
class SpotifyDiscoveryEngine:
    """Provides Spotify track metadata and mock seed catalogs."""
    def get_top_tracks(self) -> List[Dict]:
        return [
            {"id": "sp_01", "title": "Blinding Lights", "artist": "The Weeknd", "album": "After Hours", "duration": "3:20"},
            {"id": "sp_02", "title": "Starboy", "artist": "The Weeknd ft. Daft Punk", "album": "Starboy", "duration": "3:50"},
            {"id": "sp_03", "title": "As It Was", "artist": "Harry Styles", "album": "Harry's House", "duration": "2:47"},
            {"id": "sp_04", "title": "Levitating", "artist": "Dua Lipa", "album": "Future Nostalgia", "duration": "3:23"},
            {"id": "sp_05", "title": "Stay", "artist": "The Kid LAROI, Justin Bieber", "album": "F*CK LOVE 3", "duration": "2:21"}
        ]

    def search_tracks(self, query: str) -> List[Dict]:
        if not query.strip():
            return []
        catalog = self.get_top_tracks()
        matches = [t for t in catalog if query.lower() in t["title"].lower() or query.lower() in t["artist"].lower()]
        if not matches:
            matches = [{"id": f"sp_dyn_{abs(hash(query))}", "title": query.title(), "artist": "Various Artists", "album": "Single", "duration": "3:30"}]
        return matches


# ============================================================================
# 3. YOUTUBE MUSIC AUDIO RESOLVER
# ============================================================================
class YTMusicAudioResolver:
    """Matches Spotify metadata to YouTube streams with caching and overrides."""
    def __init__(self, db: blaiseDatabase):
        self.db = db

    def resolve_stream_url(self, track: Dict) -> Tuple[Optional[str], str]:
        spotify_id = track.get("id")
        cached = self.db.get_match(spotify_id)
        
        if cached:
            _, yt_url, _ = cached
            stream_url = self._extract_direct_stream(yt_url)
            if stream_url:
                return stream_url, yt_url

        search_query = f"{track['artist']} - {track['title']} official audio"
        yt_url = self._search_youtube_first_result(search_query) or f"https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        yt_video_id = extract_youtube_id(yt_url) or spotify_id

        self.db.save_match(spotify_id, yt_video_id=yt_video_id, yt_url=yt_url, is_manual=False)
        stream_url = self._extract_direct_stream(yt_url)
        return stream_url, yt_url

    def override_match(self, spotify_id: str, custom_yt_url: str) -> Optional[str]:
        video_id = extract_youtube_id(custom_yt_url)
        if not video_id:
            return None
        stream_url = self._extract_direct_stream(custom_yt_url)
        if stream_url:
            self.db.save_match(spotify_id, yt_video_id=video_id, yt_url=custom_yt_url, is_manual=True)
            return stream_url
        return None

    def _search_youtube_first_result(self, query: str) -> Optional[str]:
        if not YTDLP_AVAILABLE:
            return None
        ydl_opts = {'format': 'bestaudio/best', 'quiet': True, 'default_search': 'ytsearch1'}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(query, download=False)
                if 'entries' in info and len(info['entries']) > 0:
                    return info['entries'][0].get('webpage_url')
        except Exception as e:
            print(f"[Resolver Error] {e}")
        return None

    def _extract_direct_stream(self, yt_url: str) -> Optional[str]:
        if not YTDLP_AVAILABLE:
            return yt_url
        ydl_opts = {'format': 'bestaudio/best', 'quiet': True}
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(yt_url, download=False)
                return info.get('url')
        except Exception:
            return yt_url


# ============================================================================
# 4. UNIVERSAL AUDIO PLAYBACK ENGINE (VLC / PYGAME)
# ============================================================================
class PlaybackEngine:
    """Playback engine capable of streaming direct HTTP network audio URLs."""
    def __init__(self):
        self.queue: List[Dict] = []
        self.current_index: int = -1
        self.is_playing: bool = False
        self.volume: float = 0.8
        self.current_track: Optional[Dict] = None

        self.vlc_instance = vlc.Instance() if VLC_AVAILABLE else None
        self.vlc_player = self.vlc_instance.media_player_new() if self.vlc_instance else None

        if PYGAME_AVAILABLE and not VLC_AVAILABLE:
            pygame.mixer.init()

    def load_queue(self, tracks: List[Dict], start_index: int = 0):
        self.queue = tracks
        self.current_index = start_index

    def play_current(self, stream_url: Optional[str] = None):
        if 0 <= self.current_index < len(self.queue):
            self.current_track = self.queue[self.current_index]
            self.is_playing = True

            if VLC_AVAILABLE and stream_url:
                media = self.vlc_instance.media_new(stream_url)
                self.vlc_player.set_media(media)
                self.vlc_player.audio_set_volume(int(self.volume * 100))
                self.vlc_player.play()
            elif PYGAME_AVAILABLE and stream_url and os.path.exists(stream_url):
                pygame.mixer.music.load(stream_url)
                pygame.mixer.music.set_volume(self.volume)
                pygame.mixer.music.play()

    def toggle_play_pause(self) -> bool:
        self.is_playing = not self.is_playing
        if VLC_AVAILABLE and self.vlc_player:
            if self.is_playing:
                self.vlc_player.play()
            else:
                self.vlc_player.pause()
        elif PYGAME_AVAILABLE:
            if self.is_playing:
                pygame.mixer.music.unpause()
            else:
                pygame.mixer.music.pause()
        return self.is_playing

    def next_track(self) -> Optional[Dict]:
        if self.current_index + 1 < len(self.queue):
            self.current_index += 1
            return self.queue[self.current_index]
        return None

    def previous_track(self) -> Optional[Dict]:
        if self.current_index - 1 >= 0:
            self.current_index -= 1
            return self.queue[self.current_index]
        return None

    def set_volume(self, val: float):
        self.volume = max(0.0, min(1.0, val))
        if VLC_AVAILABLE and self.vlc_player:
            self.vlc_player.audio_set_volume(int(self.volume * 100))
        elif PYGAME_AVAILABLE:
            pygame.mixer.music.set_volume(self.volume)


# ============================================================================
# 5. UNIFIED USER INTERFACE
# ============================================================================
class blaiseGUI:
    """Desktop UI with dynamic support for CustomTkinter and Tkinter."""
    def __init__(self):
        data_dir = os.environ.get("BLAISE_DATA_DIR")
        if data_dir:
            os.makedirs(data_dir, exist_ok=True)
        db_path = os.path.join(data_dir, "blaise_cache.db") if data_dir else "blaise_cache.db"
        self.db = blaiseDatabase(db_path)
        self.spotify = SpotifyDiscoveryEngine()
        self.resolver = YTMusicAudioResolver(self.db)
        self.player = PlaybackEngine()

        if CTK_AVAILABLE:
            self.root = ctk.CTk()
            self.root.title("blaise - Music Discovery & Streaming")
            self.root.geometry("1000x680")
            self._build_ctk_ui()
            self._show_home()
        else:
            self.root = ctk.Tk()
            self.root.title("blaise - Standard UI")
            self.root.geometry("600x400")
            self._build_standard_ui()

    def _build_ctk_ui(self):
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        self.sidebar = ctk.CTkFrame(self.root, width=200, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew", rowspan=2)

        logo_label = ctk.CTkLabel(self.sidebar, text="🎵 blaise", font=ctk.CTkFont(size=22, weight="bold"))
        logo_label.pack(padx=20, pady=(20, 30))

        ctk.CTkButton(self.sidebar, text="Home", command=self._show_home).pack(padx=20, pady=10, fill="x")
        ctk.CTkButton(self.sidebar, text="Search", command=self._show_search).pack(padx=20, pady=10, fill="x")
        ctk.CTkButton(self.sidebar, text="Fix Match Override", command=self._show_override_dialog).pack(padx=20, pady=10, fill="x")

        self.main_content = ctk.CTkFrame(self.root, corner_radius=10)
        self.main_content.grid(row=0, column=1, sticky="nsew", padx=15, pady=15)
        self.main_content.grid_columnconfigure(0, weight=1)

        self.track_scroll = ctk.CTkScrollableFrame(self.main_content, title="Personalized Feed")
        self.track_scroll.pack(fill="both", expand=True, padx=10, pady=10)

        self.player_bar = ctk.CTkFrame(self.root, height=80, corner_radius=0)
        self.player_bar.grid(row=1, column=1, sticky="ew")

        self.lbl_now_playing = ctk.CTkLabel(self.player_bar, text="No track playing", font=ctk.CTkFont(size=14, weight="bold"))
        self.lbl_now_playing.pack(side="left", padx=20)

        self.btn_prev = ctk.CTkButton(self.player_bar, text="⏮", width=40, command=self._on_prev)
        self.btn_prev.pack(side="left", padx=5)

        self.btn_play = ctk.CTkButton(self.player_bar, text="▶", width=50, command=self._on_play_pause)
        self.btn_play.pack(side="left", padx=5)

        self.btn_next = ctk.CTkButton(self.player_bar, text="⏭", width=40, command=self._on_next)
        self.btn_next.pack(side="left", padx=5)

        self.slider_vol = ctk.CTkSlider(self.player_bar, from_=0, to=1, command=self._on_volume_change)
        self.slider_vol.set(0.8)
        self.slider_vol.pack(side="right", padx=20)

    def _build_standard_ui(self):
        label = ctk.Label(self.root, text="CustomTkinter not installed.\nPlease install requirements:\npip install customtkinter python-vlc yt-dlp", font=("Arial", 12))
        label.pack(expand=True, padx=20, pady=20)

    def _show_home(self):
        if not CTK_AVAILABLE:
            return
        for widget in self.track_scroll.winfo_children():
            widget.destroy()

        tracks = self.spotify.get_top_tracks()
        self.player.load_queue(tracks)

        for idx, track in enumerate(tracks):
            row_frame = ctk.CTkFrame(self.track_scroll)
            row_frame.pack(fill="x", pady=4, padx=5)

            lbl = ctk.CTkLabel(row_frame, text=f"{track['title']} - {track['artist']}", anchor="w")
            lbl.pack(side="left", padx=10, expand=True, fill="x")

            btn_play = ctk.CTkButton(row_frame, text="Play", width=60, command=lambda i=idx: self._play_track_at_index(i))
            btn_play.pack(side="right", padx=10)

    def _show_search(self):
        if not CTK_AVAILABLE:
            return
        for widget in self.track_scroll.winfo_children():
            widget.destroy()

        search_bar = ctk.CTkEntry(self.track_scroll, placeholder_text="Search tracks...")
        search_bar.pack(fill="x", padx=10, pady=10)

        def execute_search():
            q = search_bar.get()
            results = self.spotify.search_tracks(q)
            self.player.load_queue(results)
            
            for idx, track in enumerate(results):
                rf = ctk.CTkFrame(self.track_scroll)
                rf.pack(fill="x", pady=4, padx=5)
                ctk.CTkLabel(rf, text=f"{track['title']} - {track['artist']}", anchor="w").pack(side="left", padx=10)
                ctk.CTkButton(rf, text="Play", width=60, command=lambda i=idx: self._play_track_at_index(i)).pack(side="right", padx=10)

        ctk.CTkButton(self.track_scroll, text="Search", command=execute_search).pack(pady=5)

    def _play_track_at_index(self, index: int):
        self.player.current_index = index
        track = self.player.queue[index]
        self.lbl_now_playing.configure(text=f"Resolving: {track['title']}...")

        def worker():
            stream_url, _ = self.resolver.resolve_stream_url(track)
            self.root.after(0, lambda: self._start_playback_ui(track, stream_url))

        threading.Thread(target=worker, daemon=True).start()

    def _start_playback_ui(self, track: Dict, stream_url: Optional[str]):
        self.player.play_current(stream_url)
        self.lbl_now_playing.configure(text=f"Playing: {track['title']} • {track['artist']}")
        self.btn_play.configure(text="⏸")

    def _on_play_pause(self):
        is_playing = self.player.toggle_play_pause()
        self.btn_play.configure(text="⏸" if is_playing else "▶")

    def _on_next(self):
        if self.player.next_track():
            self._play_track_at_index(self.player.current_index)

    def _on_prev(self):
        if self.player.previous_track():
            self._play_track_at_index(self.player.current_index)

    def _on_volume_change(self, val: float):
        self.player.set_volume(float(val))

    def _show_override_dialog(self):
        if not CTK_AVAILABLE or not self.player.current_track:
            return
        dialog = ctk.CTkInputDialog(text="Paste direct YouTube video URL:", title="Fix Match Override")
        yt_url = dialog.get_input()
        if yt_url:
            spotify_id = self.player.current_track["id"]
            stream_url = self.resolver.override_match(spotify_id, yt_url)
            if stream_url:
                self.player.play_current(stream_url)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = blaiseGUI()
    app.run()
