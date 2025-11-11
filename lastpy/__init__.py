"""Last.fm API wrapper for scrobbling and authentication."""

import os
import time
import hashlib
from typing import Optional
import requests
from dotenv import load_dotenv

load_dotenv()

# Constants
API_ENDPOINT = 'http://ws.audioscrobbler.com/2.0/'
API_KEY = os.environ.get('LAST_FM_API', '')
API_SECRET = os.environ.get('LAST_FM_API_SECRET', '')


def authorize(user_token: str) -> str:
    """
    Convert Last.fm auth token to session key.

    Args:
        user_token: Authentication token from Last.fm auth flow

    Returns:
        XML response containing session key
    """
    params = {
        'api_key': API_KEY,
        'method': 'auth.getSession',
        'token': user_token
    }
    request_hash = _hash_request(params, API_SECRET)
    params['api_sig'] = request_hash

    response = requests.post(API_ENDPOINT, params, timeout=10)
    response.raise_for_status()
    return response.text


def now_playing(song_name: str, artist_name: str, session_key: str) -> str:
    """
    Update now playing status on Last.fm.

    Args:
        song_name: Track title
        artist_name: Artist name
        session_key: Last.fm session key

    Returns:
        XML response from Last.fm API
    """
    params = {
        'method': 'track.updateNowPlaying',
        'api_key': API_KEY,
        'track': song_name,
        'artist': artist_name,
        'sk': session_key
    }
    request_hash = _hash_request(params, API_SECRET)
    params['api_sig'] = request_hash

    response = requests.post(API_ENDPOINT, params, timeout=10)
    response.raise_for_status()
    return response.text


def scrobble(
    song_name: str,
    artist_name: str,
    album_name: str,
    session_key: str,
    timestamp: Optional[str] = None
) -> str:
    """
    Scrobble a track to Last.fm.

    Args:
        song_name: Track title
        artist_name: Artist name
        album_name: Album name
        session_key: Last.fm session key
        timestamp: Unix timestamp when track was played (defaults to 30 seconds ago)

    Returns:
        XML response from Last.fm API
    """
    if timestamp is None:
        timestamp = str(int(time.time() - 30))

    params = {
        'method': 'track.scrobble',
        'api_key': API_KEY,
        'timestamp': timestamp,
        'track': song_name,
        'artist': artist_name,
        'album': album_name,
        'sk': session_key
    }
    request_hash = _hash_request(params, API_SECRET)
    params['api_sig'] = request_hash

    response = requests.post(API_ENDPOINT, params, timeout=10)
    response.raise_for_status()
    return response.text


def _hash_request(params: dict, secret_key: str) -> str:
    """
    Generate MD5 signature for Last.fm API request.

    Args:
        params: Request parameters
        secret_key: Last.fm API secret

    Returns:
        MD5 hash as hexadecimal string
    """
    # Sort parameters alphabetically and concatenate
    sorted_keys = sorted(params.keys())
    signature_string = ''.join(
        key + (params[key] if params[key] is not None else '')
        for key in sorted_keys
    )
    signature_string += secret_key

    # Generate MD5 hash
    return hashlib.md5(signature_string.encode('utf-8')).hexdigest()
