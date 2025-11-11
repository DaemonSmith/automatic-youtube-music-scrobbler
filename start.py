"""YouTube Music to Last.fm automatic scrobbler."""

import os
import time
import sqlite3
import webbrowser
import threading
import http.server
import socketserver
from typing import Optional, Tuple, Set
import xml.etree.ElementTree as ET
from datetime import datetime

import lastpy
from ytmusicapi import YTMusic
from dotenv import load_dotenv, set_key

load_dotenv()

# Constants
LOCALHOST_PORT = 5588
CALLBACK_URL = f"http://localhost:{LOCALHOST_PORT}"
DATABASE_PATH = './data.db'
SCROBBLE_DELAY_SECONDS = 90  # Delay between scrobbles to appear natural
DUPLICATE_CHECK_HOURS = 2
DATABASE_CLEANUP_HOURS = 6
API_RATE_LIMIT_DELAY = 0.5  # Seconds between API calls
SCROBBLE_TIMESTAMP_OFFSET = 30  # Scrobble tracks as if played 30s ago


class TokenHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler for Last.fm OAuth callback."""

    def do_GET(self):
        """Handle GET request for OAuth token."""
        if self.path.startswith('/?token='):
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(
                b'<html><body><p>Authentication successful! You can close this window.</p></body></html>'
            )
            self.server.token = self.path.split('?token=')[1]
        else:
            super().do_GET()

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


class TokenServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Threaded TCP server for OAuth token capture."""

    token: Optional[str] = None
    allow_reuse_address = True


class Scrobbler:
    """Main scrobbler class for YouTube Music to Last.fm sync."""

    def __init__(self):
        """Initialize scrobbler with environment credentials."""
        self.api_key = os.environ['LAST_FM_API']
        self.username = os.environ['LAST_FM_USERNAME']
        self.session = os.environ.get('LASTFM_SESSION')
        self.conn: Optional[sqlite3.Connection] = None
        self.db_available = False
        self.init_db()

    def log(self, message: str) -> None:
        """Log message with timestamp."""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")

    def init_db(self) -> None:
        """Initialize SQLite database for duplicate tracking."""
        try:
            db_exists = os.path.exists(DATABASE_PATH)
            self.log(f"Database exists: {db_exists}")

            self.conn = sqlite3.connect(DATABASE_PATH)

            with self.conn:
                cursor = self.conn.cursor()

                # Check if table exists
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='recent_scrobbles'"
                )
                table_exists = cursor.fetchone() is not None

                # Create table if needed
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS recent_scrobbles (
                        track_name TEXT,
                        artist_name TEXT,
                        scrobbled_at INTEGER,
                        video_id TEXT,
                        PRIMARY KEY (track_name, artist_name, scrobbled_at)
                    )
                ''')

                if table_exists:
                    cursor.execute('SELECT COUNT(*) FROM recent_scrobbles')
                    count = cursor.fetchone()[0]
                    self.log(f"Found {count} existing records in database")

                # Clean old entries
                cleanup_timestamp = int(time.time()) - (DATABASE_CLEANUP_HOURS * 3600)
                cursor.execute(
                    'DELETE FROM recent_scrobbles WHERE scrobbled_at < ?',
                    (cleanup_timestamp,)
                )
                deleted = cursor.rowcount
                if deleted > 0:
                    self.log(f"Cleaned {deleted} old records (>{DATABASE_CLEANUP_HOURS}h)")

            self.db_available = True
            self.log("Database initialized successfully")

        except Exception as e:
            self.log(f"Database initialization failed: {e}")
            self.db_available = False
            self.conn = None

    def get_auth_token(self) -> str:
        """
        Get Last.fm authentication token via OAuth flow.

        Returns:
            Authentication token string
        """
        auth_url = f"https://www.last.fm/api/auth/?api_key={self.api_key}&cb={CALLBACK_URL}"

        with TokenServer(('localhost', LOCALHOST_PORT), TokenHandler) as httpd:
            self.log(f"Opening browser for Last.fm authentication...")
            webbrowser.open(auth_url)

            thread = threading.Thread(target=httpd.serve_forever)
            thread.daemon = True
            thread.start()

            # Wait for token
            while not httpd.token:
                time.sleep(0.1)

            httpd.shutdown()
            self.log("Authentication token received")
            return httpd.token

    def get_session_key(self, token: str) -> str:
        """
        Convert authentication token to session key.

        Args:
            token: Last.fm auth token

        Returns:
            Session key for API requests
        """
        xml_response = lastpy.authorize(token)
        root = ET.fromstring(xml_response)
        session_key = root.find('session/key').text

        # Save session key to .env
        set_key('.env', 'LASTFM_SESSION', session_key)
        self.log("Session key saved to .env")

        return session_key

    def normalize_track(self, track_name: str, artist_name: str) -> Tuple[str, str]:
        """
        Normalize track and artist names for consistent matching.

        Args:
            track_name: Original track title
            artist_name: Original artist name

        Returns:
            Tuple of (normalized_track, normalized_artist)
        """
        # Remove " - Topic" suffix from YouTube auto-generated artist channels
        if artist_name.endswith(" - Topic"):
            artist_name = artist_name[:-8].strip()

        # Remove common video suffixes
        video_suffixes = [
            " (official video)",
            " (official music video)",
            " (lyric video)",
            " (official audio)",
            " (audio)",
            " (lyrics)",
        ]

        track_lower = track_name.lower()
        for suffix in video_suffixes:
            if track_lower.endswith(suffix):
                track_name = track_name[:-len(suffix)].strip()
                break

        return track_name.strip(), artist_name.strip()

    def is_recent_duplicate(self, track_name: str, artist_name: str, video_id: str) -> bool:
        """
        Check if track was recently scrobbled to prevent duplicates.

        Args:
            track_name: Track title
            artist_name: Artist name
            video_id: YouTube video ID

        Returns:
            True if duplicate found within time window, False otherwise
        """
        if not self.db_available or not self.conn:
            return False

        try:
            with self.conn:
                cursor = self.conn.cursor()
                cutoff_time = int(time.time()) - (DUPLICATE_CHECK_HOURS * 3600)

                # Check by track/artist combination (case-insensitive)
                cursor.execute('''
                    SELECT track_name, artist_name, scrobbled_at
                    FROM recent_scrobbles
                    WHERE track_name COLLATE NOCASE = ?
                      AND artist_name COLLATE NOCASE = ?
                      AND scrobbled_at > ?
                    ORDER BY scrobbled_at DESC
                    LIMIT 1
                ''', (track_name, artist_name, cutoff_time))

                result = cursor.fetchone()
                if result:
                    seconds_ago = int(time.time()) - result[2]
                    self.log(
                        f"Duplicate found: '{result[0]}' by '{result[1]}' "
                        f"(scrobbled {seconds_ago}s ago)"
                    )
                    return True

                # Check by video ID as secondary identifier
                if video_id:
                    cursor.execute('''
                        SELECT track_name, artist_name, scrobbled_at
                        FROM recent_scrobbles
                        WHERE video_id = ? AND scrobbled_at > ?
                        ORDER BY scrobbled_at DESC
                        LIMIT 1
                    ''', (video_id, cutoff_time))

                    result = cursor.fetchone()
                    if result:
                        seconds_ago = int(time.time()) - result[2]
                        self.log(
                            f"Duplicate found (by video ID): '{result[0]}' by '{result[1]}' "
                            f"(scrobbled {seconds_ago}s ago)"
                        )
                        return True

                return False

        except Exception as e:
            self.log(f"Duplicate check failed: {e}")
            return False

    def store_scrobble(self, track_name: str, artist_name: str, video_id: str) -> None:
        """
        Store scrobble record in database.

        Args:
            track_name: Track title
            artist_name: Artist name
            video_id: YouTube video ID
        """
        if not self.db_available or not self.conn:
            return

        try:
            with self.conn:
                cursor = self.conn.cursor()
                current_time = int(time.time())
                cursor.execute('''
                    INSERT OR IGNORE INTO recent_scrobbles
                    (track_name, artist_name, scrobbled_at, video_id)
                    VALUES (?, ?, ?, ?)
                ''', (track_name, artist_name, current_time, video_id))

        except Exception as e:
            self.log(f"Failed to store scrobble: {e}")

    def scrobble_track(
        self,
        track_name: str,
        artist_name: str,
        album_name: str,
        delay_offset: int
    ) -> Tuple[bool, any]:
        """
        Scrobble a single track to Last.fm.

        Args:
            track_name: Track title
            artist_name: Artist name
            album_name: Album name
            delay_offset: Time offset in seconds to backdate scrobble

        Returns:
            Tuple of (success: bool, result: int or error message)
        """
        # Calculate timestamp: current time - offset - delay for natural spacing
        scrobble_timestamp = int(time.time()) - SCROBBLE_TIMESTAMP_OFFSET - delay_offset
        timestamp_str = str(scrobble_timestamp)

        try:
            xml_response = lastpy.scrobble(
                track_name,
                artist_name,
                album_name,
                self.session,
                timestamp_str
            )

            root = ET.fromstring(xml_response)
            scrobbles = root.find('scrobbles')

            if scrobbles is not None and scrobbles.get('accepted') == '1':
                return True, scrobble_timestamp
            else:
                return False, xml_response

        except Exception as e:
            return False, str(e)

    def process_history(self, history: list) -> Tuple[int, int, int]:
        """
        Process YouTube Music history and scrobble tracks.

        Args:
            history: List of history items from YouTube Music

        Returns:
            Tuple of (scrobbled_count, skipped_count, error_count)
        """
        scrobbled = skipped = errors = 0
        current_session_tracks: Set[Tuple[str, str]] = set()

        for item in history:
            # Only process recent tracks
            if item.get("played") not in ["Today", "Yesterday"]:
                continue

            video_id = item.get("videoId", "")
            raw_track = item.get("title", "")
            raw_artist = item.get("artists", [{}])[0].get("name", "")

            # Skip auto-generated Topic channels entirely
            if raw_artist.endswith(" - Topic"):
                continue

            # Normalize track metadata
            track_name, artist_name = self.normalize_track(raw_track, raw_artist)
            album_name = item.get("album", {}).get("name", track_name) if item.get("album") else track_name

            # Check database for duplicates
            if self.is_recent_duplicate(track_name, artist_name, video_id):
                skipped += 1
                continue

            # Check current session for duplicates
            track_key = (track_name.lower(), artist_name.lower())
            if track_key in current_session_tracks:
                self.log(f"Duplicate in current session: {track_name} by {artist_name}")
                skipped += 1
                continue

            # Scrobble to Last.fm
            success, result = self.scrobble_track(
                track_name,
                artist_name,
                album_name,
                scrobbled * SCROBBLE_DELAY_SECONDS
            )

            if success:
                scrobbled += 1
                current_session_tracks.add(track_key)
                self.store_scrobble(track_name, artist_name, video_id)
                self.log(f"Scrobbled: {track_name} by {artist_name}")
            else:
                errors += 1
                self.log(f"Failed: {track_name} - {result}")

            # Rate limiting
            time.sleep(API_RATE_LIMIT_DELAY)

        return scrobbled, skipped, errors

    def run(self) -> None:
        """Main execution workflow."""
        try:
            # Initialize YouTube Music client
            self.log("Initializing YouTube Music client...")
            # oauth.json contains all necessary credentials including client_id/secret
            ytmusic = YTMusic('oauth.json')

            # Get Last.fm session if needed
            if not self.session:
                self.log("No Last.fm session found, starting OAuth flow...")
                token = self.get_auth_token()
                self.session = self.get_session_key(token)

            # Fetch YouTube Music history
            self.log("Fetching YouTube Music history...")
            history = ytmusic.get_history()
            self.log(f"Retrieved {len(history)} history items")

            # Process and scrobble tracks
            scrobbled, skipped, errors = self.process_history(history)

            # Summary
            self.log(f"Completed - Scrobbled: {scrobbled} | Skipped: {skipped} | Errors: {errors}")

            # Database final stats
            if self.conn:
                with self.conn:
                    cursor = self.conn.cursor()
                    cursor.execute('SELECT COUNT(*) FROM recent_scrobbles')
                    final_count = cursor.fetchone()[0]
                    self.log(f"Database contains {final_count} records")

        except Exception as e:
            self.log(f"Error during execution: {e}")
            raise

        finally:
            if self.conn:
                self.conn.close()
                self.log("Database connection closed")


if __name__ == '__main__':
    Scrobbler().run()
