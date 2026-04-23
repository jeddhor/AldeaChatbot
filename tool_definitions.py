"""Tool registry for Sentinel Chat.

This module provides:
- Tool function implementations
- OpenAI/Ollama-compatible tool schema definitions
- A safe execution helper used by the Flask chat loop

The goal is portability: hardware-dependent features are supported when
optional dependencies are available, and otherwise return informative
messages instead of crashing.
"""

from __future__ import annotations

from configparser import ConfigParser
from datetime import datetime
import hashlib
import json
import os
import random
import string
import subprocess
import time
import uuid
import webbrowser
from typing import Any, Callable, Dict, List, Optional

import feedparser
import requests


_CONFIG = ConfigParser()
_CONFIG.read("config.ini")

_RUNTIME_TOOL_SETTINGS: Dict[str, Any] = {}
_NAVIDROME_PLAYBACK: Dict[str, Any] = {
    "mode": None,
    "path": None,
    "process": None,
}


def set_runtime_tool_settings(settings: Optional[Dict[str, Any]]) -> None:
    """Update runtime tool configuration from app settings storage."""
    global _RUNTIME_TOOL_SETTINGS
    _RUNTIME_TOOL_SETTINGS = settings or {}


def _get_tool_setting(runtime_key: str, fallback: str = "", config_section: str = "", config_key: str = "") -> str:
    value = _RUNTIME_TOOL_SETTINGS.get(runtime_key, fallback)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if value and not isinstance(value, str):
        return str(value)
    if config_section and config_key:
        return _CONFIG.get(config_section, config_key, fallback=fallback)
    return fallback

RSS_FEEDS: Dict[str, str] = {
    "cnn": "http://rss.cnn.com/rss/cnn_topstories.rss",
    "nytimes": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml",
    "bbc": "http://newsrss.bbc.co.uk/rss/newsonline_uk_edition/front_page/rss.xml",
    "reuters": "https://reutersbest.com/feed/",
    "foxnews": "https://moxie.foxnews.com/google-publisher/latest.xml",
    "techcrunch": "http://feeds.feedburner.com/TechCrunch/",
    "theverge": "https://www.theverge.com/rss/index.xml",
    "wired": "https://www.wired.com/feed/rss",
    "arstechnica": "http://feeds.arstechnica.com/arstechnica/index",
    "slashdot": "http://rss.slashdot.org/Slashdot/slashdotMain",
    "engadget": "https://www.engadget.com/rss.xml",
    "thenextweb": "https://thenextweb.com/feed",
    "gizmodo": "https://gizmodo.com/feed",
    "bloomberg": "https://feeds.bloomberg.com/politics/news.rss",
    "wowhead": "http://www.wowhead.com/news/rss/all",
    "bluesnews": "https://www.bluesnews.com/news/news_1_0.rdf",
}


def _safe_json(value: Any) -> Any:
    """Convert value into a JSON-serializable structure where possible."""
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def get_news(sources: List[str], limit: int = 5) -> List[dict]:
    if not sources:
        return [{"error": "No sources provided"}]

    limit = max(0, _to_int(limit, 5))
    all_headlines: List[dict] = []

    for src in sources:
        feed_url = RSS_FEEDS.get(str(src).lower(), str(src))
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:limit]:
                pub_date = None
                if getattr(entry, "published_parsed", None):
                    pub_date = datetime(*entry.published_parsed[:6])
                all_headlines.append(
                    {
                        "source": src,
                        "title": getattr(entry, "title", "(untitled)"),
                        "link": getattr(entry, "link", ""),
                        "published": pub_date.isoformat() if pub_date else None,
                    }
                )
        except Exception as exc:
            all_headlines.append({"source": src, "error": str(exc)})

    all_headlines.sort(key=lambda x: x.get("published") or "", reverse=True)
    return all_headlines


def get_dictionary_definition(word: str) -> str:
    api_url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
    response = requests.get(api_url, timeout=10)
    if response.status_code != 200:
        return f"Sorry, I could not find the definition for {word}."

    data = response.json()
    try:
        definition = data[0]["meanings"][0]["definitions"][0]["definition"]
    except (IndexError, KeyError, TypeError):
        return f"Sorry, I could not extract a definition for {word}."

    return f"The definition of {word} is: {definition}"


def get_square_root(number: float) -> float:
    number_f = _to_float(number, 0.0)
    if number_f < 0:
        raise ValueError("Cannot compute square root of a negative number")
    return round(number_f ** 0.5, 2)


def get_current_time() -> str:
    now = datetime.now().astimezone()
    is_dst = now.dst() is not None and now.dst().total_seconds() > 0
    tz_suffix = " PDT" if is_dst else " PST"
    return f"The current time is {now.strftime('%A, %Y-%m-%d %H:%M:%S')}{tz_suffix}."


def get_current_date() -> str:
    now = datetime.now().astimezone()
    return f"The current date is {now.strftime('%A, %B %d, %Y')}."


def subtract_two_numbers(a: int, b: int) -> int:
    return _to_int(a, 0) - _to_int(b, 0)


def get_subreddit_feed(subreddit: str, limit: int = 5) -> List[Dict[str, str]]:
    limit = max(0, _to_int(limit, 5))
    feed_url = f"https://www.reddit.com/r/{subreddit}/.rss"
    feed = feedparser.parse(feed_url)
    posts: List[Dict[str, str]] = []
    for entry in feed.entries[:limit]:
        posts.append({"title": getattr(entry, "title", "(untitled)"), "link": getattr(entry, "link", "")})
    return posts


def open_sites(sites: List[str]) -> str:
    if not sites:
        return "No sites provided."

    opened = 0
    for site in sites:
        url = str(site)
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        webbrowser.open_new_tab(url)
        opened += 1
    return f"Opened {opened} site(s) in your default web browser."


def get_current_weather(zip_code: str, country_code: str = "US", units: str = "imperial") -> dict:
    api_key = _get_tool_setting("openweathermap_api_key", "", "OpenWeatherMap", "API_KEY")
    if not api_key:
        raise ValueError("OpenWeatherMap API key not set in config.ini")

    response = requests.get(
        "https://api.openweathermap.org/data/2.5/weather",
        params={
            "zip": f"{zip_code},{country_code}",
            "appid": api_key,
            "units": units,
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def wikipedia_search_with_summaries(query: str, limit: int = 5) -> List[Dict[str, str]]:
    if not query:
        return [{"error": "Query parameter is required."}]

    limit = max(1, min(10, _to_int(limit, 5)))
    headers = {"User-Agent": "SentinelChat/1.0"}

    search_response = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "utf8": 1,
            "format": "json",
            "srlimit": limit,
        },
        headers=headers,
        timeout=10,
    )
    search_response.raise_for_status()
    search_results = search_response.json().get("query", {}).get("search", [])
    if not search_results:
        return []

    page_ids = [str(item.get("pageid")) for item in search_results if item.get("pageid")]
    if not page_ids:
        return []

    summaries_response = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "pageids": "|".join(page_ids),
            "prop": "extracts",
            "exintro": 1,
            "explaintext": 1,
            "format": "json",
        },
        headers=headers,
        timeout=10,
    )
    summaries_response.raise_for_status()
    pages = summaries_response.json().get("query", {}).get("pages", {})

    results: List[Dict[str, str]] = []
    for item in search_results:
        pageid = str(item.get("pageid"))
        title = item.get("title", "")
        page = pages.get(pageid, {})
        results.append(
            {
                "title": title,
                "url": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
                "summary": page.get("extract", ""),
            }
        )

    return results


def play_song_from_navidrome(song_name: str) -> str:
    """Stubbed unless dependencies/config are present; retains tool compatibility."""
    username = _get_tool_setting("navidrome_username", "", "Navidrome", "NAVIDROME_USERNAME")
    password = _get_tool_setting("navidrome_password", "", "Navidrome", "NAVIDROME_PASSWORD")
    server_url = _get_tool_setting("navidrome_server_url", "", "Navidrome", "NAVIDROME_SERVER")

    if not username or not password or not server_url:
        return "Navidrome is not configured. Set server URL, username, and password in Configuration -> Tools."

    try:
        import pygame  # type: ignore
    except Exception:
        pygame = None

    subsonic_version = _get_tool_setting("navidrome_subsonic_version", "1.16.1", "Navidrome", "SUBSONIC_VERSION")

    def subsonic_auth(raw_password: str) -> tuple[str, str]:
        salt = "".join(random.choices(string.ascii_letters + string.digits, k=6))
        token = hashlib.md5((raw_password + salt).encode("utf-8")).hexdigest()
        return token, salt

    def subsonic_request(endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        token, salt = subsonic_auth(password)
        base_params = {
            "u": username,
            "t": token,
            "s": salt,
            "v": subsonic_version,
            "c": "sentinel-chat",
            "f": "json",
        }
        r = requests.get(f"{server_url}/rest/{endpoint}", params={**base_params, **params}, timeout=20)
        r.raise_for_status()
        return r.json()

    result = subsonic_request("search3.view", {"query": song_name})
    songs = result.get("subsonic-response", {}).get("searchResult3", {}).get("song", [])
    if not songs:
        return f"No song found matching: {song_name}"

    song_id = songs[0].get("id")
    if not song_id:
        return f"No playable id found for: {song_name}"

    token, salt = subsonic_auth(password)
    stream_params = {
        "u": username,
        "t": token,
        "s": salt,
        "v": subsonic_version,
        "c": "sentinel-chat",
        "id": song_id,
    }
    stream = requests.get(f"{server_url}/rest/stream.view", params=stream_params, stream=True, timeout=30)
    stream.raise_for_status()

    mp3_cache_dir = _get_tool_setting("navidrome_mp3_cache_dir", "mp3_cache", "Navidrome", "MP3_CACHE_DIR")
    os.makedirs(mp3_cache_dir, exist_ok=True)
    output_path = os.path.join(mp3_cache_dir, f"{uuid.uuid4()}.mp3")
    with open(output_path, "wb") as handle:
        for chunk in stream.iter_content(8192):
            if chunk:
                handle.write(chunk)

    # Stop prior playback before starting a new track.
    stop_navidrome_playback()

    # Prefer in-app playback when pygame is available; otherwise use a process we can terminate.
    if pygame is not None:
        try:
            pygame.mixer.init()
            pygame.mixer.music.load(output_path)
            pygame.mixer.music.play()
            _NAVIDROME_PLAYBACK["mode"] = "pygame"
            _NAVIDROME_PLAYBACK["path"] = output_path
            _NAVIDROME_PLAYBACK["process"] = None
            return f"Playback started for song: {song_name}"
        except Exception:
            pass

    try:
        if os.name == "nt":
            process = subprocess.Popen(["cmd", "/c", "start", "", output_path], shell=False)
            _NAVIDROME_PLAYBACK["mode"] = "external"
            _NAVIDROME_PLAYBACK["path"] = output_path
            _NAVIDROME_PLAYBACK["process"] = process
        else:
            process = subprocess.Popen(["xdg-open", output_path])
            _NAVIDROME_PLAYBACK["mode"] = "external"
            _NAVIDROME_PLAYBACK["path"] = output_path
            _NAVIDROME_PLAYBACK["process"] = process
            webbrowser.open(f"file://{os.path.abspath(output_path)}")
        return f"Opened song in your default media player: {song_name}"
    except Exception as exc:
        return f"Downloaded '{song_name}', but could not launch playback automatically: {exc}"


def stop_navidrome_playback() -> str:
    """Best-effort stop for Navidrome playback started by this process."""
    stopped = False

    try:
        import pygame  # type: ignore

        if pygame.mixer.get_init() and pygame.mixer.music.get_busy():
            pygame.mixer.music.stop()
            stopped = True
    except Exception:
        pass

    process = _NAVIDROME_PLAYBACK.get("process")
    if process is not None:
        try:
            if process.poll() is None:
                process.terminate()
                stopped = True
        except Exception:
            pass

    _NAVIDROME_PLAYBACK["mode"] = None
    _NAVIDROME_PLAYBACK["process"] = None

    if stopped:
        return "Stopped active Navidrome playback."
    return "No active Navidrome playback to stop."


def perform_windows_search_with_pyautogui(query: str) -> str:
    try:
        import pyautogui  # type: ignore
    except Exception:
        return "pyautogui is not installed; cannot automate Windows search."

    pyautogui.hotkey("win", "s")
    pyautogui.write(str(query), interval=0.05)
    pyautogui.press("enter")
    return f"Performed Windows search for: {query}"


def close_named_window_with_pyautogui(window_title: str) -> str:
    try:
        import pyautogui  # type: ignore
    except Exception:
        return "pyautogui is not installed; cannot close windows."

    windows = pyautogui.getWindowsWithTitle(window_title)
    if not windows:
        return f"No window found with title: {window_title}"

    window = windows[0]
    window.activate()
    pyautogui.hotkey("alt", "f4")
    return f"Closed window with title: {window_title}"


def type_phrase_with_pyautogui(phrase: str, newline: bool = False) -> str:
    try:
        import pyautogui  # type: ignore
    except Exception:
        return "pyautogui is not installed; cannot type phrases."

    pyautogui.write(str(phrase), interval=0.05)
    if bool(newline):
        pyautogui.press("enter")
    return f"Typed phrase: {phrase}"


def set_volume_up_or_down(direction: str, presses: int = 1) -> str:
    try:
        import pyautogui  # type: ignore
    except Exception:
        return "pyautogui is not installed; cannot adjust system volume."

    direction = str(direction).lower().strip()
    presses = max(1, _to_int(presses, 1))
    if direction not in ("up", "down"):
        raise ValueError("Direction must be 'up' or 'down'")

    key = "volumeup" if direction == "up" else "volumedown"
    pyautogui.press(key, presses=presses)
    return f"Adjusted volume {direction} by {presses} step(s)."


def vector_says(message: str) -> str:
    try:
        import anki_vector  # type: ignore
        from anki_vector import audio as vector_audio  # type: ignore
    except Exception:
        return "anki_vector package is not installed; cannot control Vector."

    serial = _get_tool_setting("vector_serial", "", "Vector", "SERIAL")
    robot_kwargs: Dict[str, Any] = {}
    if serial:
        robot_kwargs["serial"] = serial

    with anki_vector.Robot(**robot_kwargs) as robot:
        robot.audio.set_master_volume(vector_audio.RobotVolumeLevel.MEDIUM_HIGH)
        robot.behavior.say_text(message)
    return json.dumps({"success": True, "message": message})


def wait(seconds: Any) -> str:
    seconds_value = _to_float(seconds, 0.0)
    if seconds_value < 0:
        seconds_value = 0.0
    time.sleep(seconds_value)
    return f"Waited for {seconds_value} seconds."


def discover_hue_bridge_ip(timeout: float = 3.0) -> Optional[str]:
    explicit_ip = _get_tool_setting("hue_bridge_ip", "", "Hue", "BRIDGE_IP")
    if explicit_ip:
        return explicit_ip

    try:
        resp = requests.get("https://discovery.meethue.com", timeout=timeout)
        resp.raise_for_status()
        bridges = resp.json()
        if bridges:
            return bridges[0].get("internalipaddress")
    except Exception:
        return None
    return None


def control_hue_lights(room: str, action: str, brightness: Optional[int] = None, color: Optional[str] = None) -> str:
    try:
        from phue import Bridge  # type: ignore
    except Exception:
        return "phue is not installed; cannot control Hue lights."

    bridge_ip = discover_hue_bridge_ip()
    if not bridge_ip:
        return "No Hue Bridge found on the local network."

    bridge = Bridge(bridge_ip)
    bridge.connect()

    action_lower = str(action).lower()
    room_lower = str(room).lower()

    groups = bridge.get_group()
    group_id = None
    for gid, group in groups.items():
        if str(group.get("name", "")).lower() == room_lower:
            group_id = gid
            break

    if not group_id:
        return f"No Hue room named '{room}' found."

    command: Dict[str, Any] = {}
    if action_lower in ("on", "turn on"):
        command["on"] = True
    elif action_lower in ("off", "turn off"):
        command["on"] = False
    else:
        return f"Unknown action '{action}'."

    if brightness is not None:
        command["bri"] = max(1, min(254, _to_int(brightness, 128)))

    color_map = {
        "red": (0.675, 0.322),
        "green": (0.409, 0.518),
        "blue": (0.167, 0.04),
        "purple": (0.272, 0.109),
        "warm": None,
        "cool": None,
    }
    if color:
        color_key = str(color).lower()
        if color_key not in color_map:
            return f"Unknown color '{color}'."
        if color_map[color_key] is None:
            command["ct"] = 370 if color_key == "warm" else 250
        else:
            command["xy"] = color_map[color_key]

    bridge.set_group(int(group_id), command)
    return f"Hue lights in '{room}' set to {action}."


def control_wemo_switch(device_name: str, action: str) -> str:
    try:
        import pywemo  # type: ignore
    except Exception:
        return "pywemo is not installed; cannot control Wemo devices."

    devices = pywemo.discover_devices()
    if not devices:
        return "No Wemo devices found on the network."

    target = None
    for device in devices:
        if device.name.lower() == str(device_name).lower():
            target = device
            break

    if not target:
        return f"Device '{device_name}' not found."

    action_lower = str(action).lower()
    if action_lower == "on":
        target.on()
    elif action_lower == "off":
        target.off()
    else:
        return "Invalid action. Use 'on' or 'off'."

    return f"Turned {action_lower} '{device_name}'."


def control_govee_device(device_name: str, action: str) -> str:
    govee_api_key = _get_tool_setting("govee_api_key", "", "Govee", "API_KEY")
    if not govee_api_key:
        return "Govee API key not configured in config.ini."

    try:
        from govee import GoveeClient  # type: ignore
    except Exception:
        return "govee package is not installed; cannot control Govee devices."

    client = GoveeClient(api_key=govee_api_key)
    devices = client.discover_devices()
    if not devices:
        return "No Govee devices found on the network."

    target = client.get_device(device_name)
    if not target:
        return f"Device '{device_name}' not found."

    action_lower = str(action).lower()
    if action_lower == "on":
        client.power(target, True)
    elif action_lower == "off":
        client.power(target, False)
    else:
        return "Invalid action. Use 'on' or 'off'."

    return f"Turned {action_lower} '{device_name}'."


TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Get the current server time.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "subtract_two_numbers",
            "description": "Subtract two numbers (a - b).",
            "parameters": {
                "type": "object",
                "required": ["a", "b"],
                "properties": {
                    "a": {"type": "integer", "description": "First number."},
                    "b": {"type": "integer", "description": "Second number."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "Get the current weather by ZIP/postal code.",
            "parameters": {
                "type": "object",
                "required": ["zip_code"],
                "properties": {
                    "zip_code": {"type": "string"},
                    "country_code": {"type": "string"},
                    "units": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_square_root",
            "description": "Get the square root of a number.",
            "parameters": {
                "type": "object",
                "required": ["number"],
                "properties": {"number": {"type": "number"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dictionary_definition",
            "description": "Get the dictionary definition of a word.",
            "parameters": {
                "type": "object",
                "required": ["word"],
                "properties": {"word": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": "Fetch recent headlines from configured RSS sources.",
            "parameters": {
                "type": "object",
                "required": ["sources"],
                "properties": {
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_subreddit_feed",
            "description": "Fetch latest posts from a subreddit RSS feed.",
            "parameters": {
                "type": "object",
                "required": ["subreddit"],
                "properties": {
                    "subreddit": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wikipedia_search_with_summaries",
            "description": "Search Wikipedia and return titles, URLs, and summaries.",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_sites",
            "description": "Open one or more websites in browser tabs.",
            "parameters": {
                "type": "object",
                "required": ["sites"],
                "properties": {
                    "sites": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_date",
            "description": "Get the current server date.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_song_from_navidrome",
            "description": "Play a song from Navidrome by name.",
            "parameters": {
                "type": "object",
                "required": ["song_name"],
                "properties": {"song_name": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "perform_windows_search_with_pyautogui",
            "description": "Perform a Windows search by simulating keyboard input.",
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_named_window_with_pyautogui",
            "description": "Close a window by title using keyboard automation.",
            "parameters": {
                "type": "object",
                "required": ["window_title"],
                "properties": {"window_title": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_phrase_with_pyautogui",
            "description": "Type a phrase into the active window.",
            "parameters": {
                "type": "object",
                "required": ["phrase"],
                "properties": {
                    "phrase": {"type": "string"},
                    "newline": {"type": "boolean"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume_up_or_down",
            "description": "Adjust system volume up or down.",
            "parameters": {
                "type": "object",
                "required": ["direction"],
                "properties": {
                    "direction": {"type": "string"},
                    "presses": {"type": "integer"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vector_says",
            "description": "Make an Anki Vector robot speak a message.",
            "parameters": {
                "type": "object",
                "required": ["message"],
                "properties": {"message": {"type": "string"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait for a number of seconds.",
            "parameters": {
                "type": "object",
                "required": ["seconds"],
                "properties": {"seconds": {"type": "number"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_hue_lights",
            "description": "Control Philips Hue lights in a room.",
            "parameters": {
                "type": "object",
                "required": ["room", "action"],
                "properties": {
                    "room": {"type": "string"},
                    "action": {"type": "string"},
                    "brightness": {"type": "integer"},
                    "color": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_wemo_switch",
            "description": "Control a Wemo switch by name.",
            "parameters": {
                "type": "object",
                "required": ["device_name", "action"],
                "properties": {
                    "device_name": {"type": "string"},
                    "action": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "control_govee_device",
            "description": "Control a Govee device by name.",
            "parameters": {
                "type": "object",
                "required": ["device_name", "action"],
                "properties": {
                    "device_name": {"type": "string"},
                    "action": {"type": "string"},
                },
            },
        },
    },
]


AVAILABLE_FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "get_current_time": get_current_time,
    "subtract_two_numbers": subtract_two_numbers,
    "get_current_weather": get_current_weather,
    "get_square_root": get_square_root,
    "get_dictionary_definition": get_dictionary_definition,
    "get_news": get_news,
    "get_subreddit_feed": get_subreddit_feed,
    "wikipedia_search_with_summaries": wikipedia_search_with_summaries,
    "open_sites": open_sites,
    "play_song_from_navidrome": play_song_from_navidrome,
    "get_current_date": get_current_date,
    "perform_windows_search_with_pyautogui": perform_windows_search_with_pyautogui,
    "close_named_window_with_pyautogui": close_named_window_with_pyautogui,
    "type_phrase_with_pyautogui": type_phrase_with_pyautogui,
    "set_volume_up_or_down": set_volume_up_or_down,
    "vector_says": vector_says,
    "wait": wait,
    "control_hue_lights": control_hue_lights,
    "control_wemo_switch": control_wemo_switch,
    "control_govee_device": control_govee_device,
}


def execute_tool_call(name: str, arguments: Any) -> Dict[str, Any]:
    """Execute a named tool with normalized arguments.

    Returns a normalized payload so the chat loop can serialize and
    return tool output to the model safely.
    """
    if name not in AVAILABLE_FUNCTIONS:
        return {"ok": False, "error": f"Unknown tool: {name}"}

    args_obj: Dict[str, Any]
    if isinstance(arguments, dict):
        args_obj = arguments
    elif isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
            args_obj = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            args_obj = {}
    else:
        args_obj = {}

    try:
        result = AVAILABLE_FUNCTIONS[name](**args_obj)
        return {"ok": True, "result": _safe_json(result)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
