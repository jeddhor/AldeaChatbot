import json
import logging
import os
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from time import perf_counter

import requests
from flask import Flask, g, jsonify, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

try:
    from tool_definitions import TOOL_DEFINITIONS, execute_tool_call, set_runtime_tool_settings, stop_navidrome_playback
except Exception:
    TOOL_DEFINITIONS = []

    def execute_tool_call(name: str, arguments: Any) -> Dict[str, Any]:
        return {"ok": False, "error": "Tool system unavailable."}

    def set_runtime_tool_settings(settings: Dict[str, Any]) -> None:
        return None

    def stop_navidrome_playback() -> str:
        return "Audio tool system unavailable."

# Base folders for configuration and SQLite data.
BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
DB_PATH = INSTANCE_DIR / "chatbot.db"
SETTINGS_PATH = INSTANCE_DIR / "settings.json"
AVATAR_UPLOAD_DIR = BASE_DIR / "static" / "uploads" / "avatars"
ALLOWED_AVATAR_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}

THEME_OPTIONS = {
    "matrix-green",
    "blue-onyx",
    "crimson-steel",
    "midnight-violet",
    "cyber-neon",
    "acid-neon",
    "arctic-mint",
    "peach-fuzz",
    "lavender-dream",
    "lemonade-pop",
}

# Default values are intentionally complete so the app can run immediately.
DEFAULT_SETTINGS: Dict[str, Any] = {
    "ui": {
        "theme_mode": "matrix-green",
        "assistant_avatar": "https://api.dicebear.com/9.x/bottts/svg?seed=Sentinel",
        "user_avatar": "https://api.dicebear.com/9.x/personas/svg?seed=Operator",
    },
    "llm": {
        "provider": "openai_compatible",
        "server_url": "http://localhost:11434",
        "api_key": "",
        "model": "llama3.1:8b",
        "context_window": 16384,
        "temperature": 0.7,
        "system_prompt": (
            "You are a general-purpose assistant who is clear, practical, and composed. "
            "Give accurate and thoughtful answers using concise structure when useful. "
            "Ask focused follow-up questions when requirements are ambiguous. "
            "Avoid over-familiar language and avoid being cold or abrupt. "
            "When uncertain, say what is unknown and offer a sensible next step."
        ),
    },
    "tts": {
        "enabled": False,
        "server_url": "http://localhost:5002",
        "endpoint": "/v1/audio/speech",
        "model": "coqui-tts",
        "voice": "",
        "speed": 1.0,
        "response_format": "mp3",
        "auto_speak_default": False,
    },
    "stt": {
        "enabled": False,
        "server_url": "http://localhost:8080",
        "endpoint": "/inference",
        "temperature": 0.0,
        "vad_threshold": 0.018,
        "silence_timeout_ms": 900,
        "transcribe_speech_default": False,
        "wake_word_activation": False,
        "auto_send_on_silence_detected": False,
        "auto_send_silence_ms": 600,
    },
    "tools": {
        "openweathermap_api_key": "",
        "govee_api_key": "",
        "hue_bridge_ip": "",
        "vector_serial": "",
        "navidrome_server_url": "",
        "navidrome_username": "",
        "navidrome_password": "",
        "navidrome_subsonic_version": "1.16.1",
        "navidrome_mp3_cache_dir": "mp3_cache",
    },
    "random_chats": {
        "enabled": False,
        "idle_seconds": 180,
    },
}


app = Flask(__name__, instance_path=str(INSTANCE_DIR))


def configure_logging() -> None:
    """Configure application logging for Waitress and Flask routes.

    By default logs go to console. Set ALDEA_LOG_FILE to also mirror logs
    into a file path, and ALDEA_LOG_LEVEL to override verbosity.
    Legacy HARBOR_LOG_FILE/HARBOR_LOG_LEVEL are still honored.
    """
    log_level_name = os.getenv("ALDEA_LOG_LEVEL", os.getenv("HARBOR_LOG_LEVEL", "INFO")).upper().strip()
    log_level = getattr(logging, log_level_name, logging.INFO)
    log_file = os.getenv("ALDEA_LOG_FILE", os.getenv("HARBOR_LOG_FILE", "")).strip()

    handlers: List[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        handlers=handlers,
        force=True,
    )

    app.logger.setLevel(log_level)
    logging.getLogger("waitress").setLevel(log_level)


configure_logging()


# -----------------------------
# Database and settings helpers
# -----------------------------
def get_db() -> sqlite3.Connection:
    """Create a SQLite connection with row access by column name."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_storage() -> None:
    """Create instance folder, settings file, and database tables if needed."""
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    AVATAR_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if not SETTINGS_PATH.exists():
        SETTINGS_PATH.write_text(json.dumps(DEFAULT_SETTINGS, indent=2), encoding="utf-8")

    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            )
            """
        )


def deep_merge(default: Dict[str, Any], loaded: Dict[str, Any]) -> Dict[str, Any]:
    """Merge loaded settings into defaults so missing keys still exist."""
    merged = dict(default)
    for key, value in loaded.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def migrate_legacy_settings(settings: Dict[str, Any]) -> bool:
    """Migrate older settings keys to the current schema.

    Returns True when any in-memory value changed and should be saved.
    """
    changed = False
    ui = settings.get("ui", {})

    legacy_theme = (ui.get("theme_mode") or "").strip().lower()
    legacy_theme_map = {
        "dark": "matrix-green",
        "light": "arctic-mint",
    }
    normalized_theme = legacy_theme_map.get(legacy_theme, legacy_theme)
    if normalized_theme not in THEME_OPTIONS:
        normalized_theme = "matrix-green"
    if ui.get("theme_mode") != normalized_theme:
        ui["theme_mode"] = normalized_theme
        changed = True

    tts = settings.get("tts", {})

    # Migrate legacy speaker key into OpenAI-compatible voice field.
    legacy_speaker = tts.get("speaker")
    if legacy_speaker and not tts.get("voice"):
        tts["voice"] = legacy_speaker
        changed = True

    # Remove legacy keys after migration.
    if "speaker" in tts:
        tts.pop("speaker", None)
        changed = True
    if "language" in tts:
        tts.pop("language", None)
        changed = True

    # Ensure endpoint defaults to OpenAI-compatible speech endpoint.
    if not tts.get("endpoint") or tts.get("endpoint") == "/api/tts":
        tts["endpoint"] = "/v1/audio/speech"
        changed = True

    # Ensure required OpenAI-compatible keys exist.
    if not tts.get("model"):
        tts["model"] = "coqui-tts"
        changed = True
    if "speed" not in tts:
        tts["speed"] = 1.0
        changed = True
    if not tts.get("response_format"):
        tts["response_format"] = "mp3"
        changed = True
    # Migrate old auto_speak to auto_speak_default.
    if "auto_speak_default" not in tts:
        tts["auto_speak_default"] = bool(tts.get("auto_speak", False))
        changed = True
    if "auto_speak" in tts:
        tts.pop("auto_speak", None)
        changed = True

    stt = settings.get("stt", {})
    if "transcribe_speech_default" not in stt:
        stt["transcribe_speech_default"] = False
        changed = True
    if "wake_word_activation" not in stt:
        stt["wake_word_activation"] = False
        changed = True
    if "auto_send_on_silence_detected" not in stt:
        stt["auto_send_on_silence_detected"] = False
        changed = True
    if "auto_send_silence_ms" not in stt or not stt.get("auto_send_silence_ms"):
        stt["auto_send_silence_ms"] = 600
        changed = True
    settings["stt"] = stt
    tools = settings.get("tools", {})
    if not tools.get("navidrome_subsonic_version"):
        tools["navidrome_subsonic_version"] = "1.16.1"
        changed = True
    if not tools.get("navidrome_mp3_cache_dir"):
        tools["navidrome_mp3_cache_dir"] = "mp3_cache"
        changed = True
    settings["tools"] = tools

    random_chats = settings.get("random_chats", {})
    if "enabled" not in random_chats:
        random_chats["enabled"] = False
        changed = True
    if not random_chats.get("idle_seconds"):
        random_chats["idle_seconds"] = 180
        changed = True
    settings["random_chats"] = random_chats
    settings["ui"] = ui

    settings["tts"] = tts
    return changed


def _build_idle_ponder_prompt(history: List[Dict[str, str]]) -> str:
    """Create a lightweight pondering prompt for idle-time random chats."""
    usable = [m for m in history if m.get("role") in {"user", "assistant"}]
    recent = usable[-10:]

    if recent:
        context_lines: List[str] = []
        for msg in recent:
            role = msg.get("role", "assistant")
            content = re.sub(r"\s+", " ", str(msg.get("content", "")).strip())
            if content:
                context_lines.append(f"{role}: {content[:220]}")

        context_block = "\n".join(context_lines) if context_lines else "(no useful history)"
        return (
            "The user is idle. Offer one short, thoughtful check-in based on recent conversation context. "
            "Do not pretend there was a new user message. Keep it concise (1-2 sentences), practical, and friendly.\n\n"
            f"Recent context:\n{context_block}"
        )

    return (
        "The user is idle and there is no chat history yet. "
        "Offer one short, friendly thought-provoking prompt or curiosity question (1 sentence). "
        "Avoid being sappy, dramatic, or repetitive."
    )


def generate_idle_ponder_reply(settings: Dict[str, Any], history: List[Dict[str, str]]) -> str:
    """Generate an assistant message for idle-time random chats."""
    llm = settings["llm"]
    provider = llm.get("provider", "openai_compatible")

    ponder_prompt = _build_idle_ponder_prompt(history)
    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "You are ALDEA, a grounded assistant. "
                "When user is idle, send one concise proactive thought that is relevant and useful."
            ),
        },
        {"role": "user", "content": ponder_prompt},
    ]

    if provider == "ollama":
        reply = call_ollama(settings, messages)
    else:
        reply = call_openai_compatible(settings, messages)

    cleaned = (reply or "").strip()
    if not cleaned:
        return "Quick thought while you are away: what is one small step you want to tackle next?"
    return cleaned


def load_settings() -> Dict[str, Any]:
    """Load settings and automatically heal missing keys via default merge."""
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    merged = deep_merge(DEFAULT_SETTINGS, data)

    if migrate_legacy_settings(merged):
        save_settings(merged)

    return merged


def save_settings(settings: Dict[str, Any]) -> None:
    """Persist settings in a human-readable JSON format."""
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def _save_avatar_upload(upload, avatar_kind: str) -> str | None:
    """Save uploaded avatar image and return its static URL path.

    Returns None when no valid file is provided.
    """
    if not upload:
        return None

    filename = (upload.filename or "").strip()
    if not filename:
        return None

    safe_name = secure_filename(filename)
    if not safe_name or "." not in safe_name:
        return None

    ext = safe_name.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_AVATAR_EXTENSIONS:
        raise ValueError("Unsupported avatar image format. Use png, jpg, jpeg, gif, webp, or svg.")

    stored_name = f"{avatar_kind}_{uuid.uuid4().hex}.{ext}"
    destination = AVATAR_UPLOAD_DIR / stored_name
    upload.save(destination)
    return f"/static/uploads/avatars/{stored_name}"


def _delete_local_avatar_if_managed(avatar_url: str) -> None:
    """Delete a previously uploaded avatar file if it is in managed storage."""
    if not avatar_url or not isinstance(avatar_url, str):
        return
    prefix = "/static/uploads/avatars/"
    if not avatar_url.startswith(prefix):
        return

    filename = avatar_url[len(prefix) :].strip()
    if not filename:
        return

    path = AVATAR_UPLOAD_DIR / filename
    try:
        if path.exists() and path.is_file():
            path.unlink()
    except Exception:
        app.logger.warning("Failed to delete avatar file: %s", path)


# -----------------------------
# LLM integration helpers
# -----------------------------
def trim_messages_to_budget(messages: List[Dict[str, str]], context_window: int) -> List[Dict[str, str]]:
    """Roughly trim chat history using a char budget approximation of tokens.

    A common approximation is about 4 characters per token in English prose.
    This is intentionally simple and model-agnostic.
    """
    char_budget = max(1024, context_window * 4)
    total = 0
    trimmed: List[Dict[str, str]] = []

    # Walk from newest to oldest and keep what fits, then restore order.
    for msg in reversed(messages):
        msg_len = len(msg.get("content", "")) + 16
        if total + msg_len > char_budget and trimmed:
            break
        trimmed.append(msg)
        total += msg_len

    return list(reversed(trimmed))


def _detect_direct_tool_intent(user_message: str) -> Dict[str, Any] | None:
    """Best-effort intent routing for obvious realtime requests.

    This keeps critical utility behavior reliable even when a model/server
    does not consistently emit tool calls.
    """
    text = (user_message or "").strip().lower()
    if not text:
        return None

    time_markers = ["what time", "current time", "time is it", "tell me the time"]
    date_markers = ["what date", "current date", "today's date", "what day is it"]

    if any(marker in text for marker in time_markers):
        return {"name": "get_current_time", "arguments": {}}

    if any(marker in text for marker in date_markers):
        return {"name": "get_current_date", "arguments": {}}

    return None


def _looks_like_realtime_refusal(reply: str) -> bool:
    text = (reply or "").lower()
    markers = [
        "i don't have real-time",
        "i do not have real-time",
        "i don't have realtime",
        "i do not have realtime",
        "i don't have access to the current time",
        "can't access the current time",
    ]
    return any(marker in text for marker in markers)


def _finalize_reply(reply: str, tool_events: List[Dict[str, Any]], include_debug: bool) -> Any:
    if not include_debug:
        return reply
    return {
        "reply": reply,
        "tool_debug": {
            "used": bool(tool_events),
            "events": tool_events,
        },
    }


def _execute_tool_with_trace(
    tool_name: str,
    tool_args: Any,
    provider: str,
    source: str,
    tool_events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    app.logger.info(
        "tool_call trigger source=%s provider=%s tool=%s args=%r",
        source,
        provider,
        tool_name,
        tool_args,
    )
    result = execute_tool_call(tool_name, tool_args)
    ok = bool(result.get("ok"))
    tool_events.append({"tool": tool_name, "source": source, "ok": ok})
    app.logger.info(
        "tool_call result source=%s provider=%s tool=%s ok=%s",
        source,
        provider,
        tool_name,
        ok,
    )
    return result


def call_openai_compatible(settings: Dict[str, Any], messages: List[Dict[str, str]]) -> str:
    """Call an OpenAI-compatible chat completions endpoint."""
    llm = settings["llm"]
    base_url = llm["server_url"].rstrip("/")
    endpoint = f"{base_url}/v1/chat/completions"

    headers = {"Content-Type": "application/json"}
    if llm.get("api_key"):
        headers["Authorization"] = f"Bearer {llm['api_key']}"

    payload = {
        "model": llm["model"],
        "messages": messages,
        "temperature": float(llm.get("temperature", 0.7)),
    }

    response = requests.post(endpoint, headers=headers, json=payload, timeout=90)
    response.raise_for_status()
    data = response.json()

    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected OpenAI-compatible response format: {data}") from exc


def call_ollama(settings: Dict[str, Any], messages: List[Dict[str, str]]) -> str:
    """Call Ollama native API for chat completions."""
    llm = settings["llm"]
    base_url = llm["server_url"].rstrip("/")
    endpoint = f"{base_url}/api/chat"

    payload = {
        "model": llm["model"],
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": float(llm.get("temperature", 0.7)),
            "num_ctx": int(llm.get("context_window", 16384)),
        },
    }

    response = requests.post(endpoint, json=payload, timeout=90)
    response.raise_for_status()
    data = response.json()

    try:
        return data["message"]["content"].strip()
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"Unexpected Ollama response format: {data}") from exc


def generate_assistant_reply(
    settings: Dict[str, Any], user_message: str, history: List[Dict[str, str]], include_debug: bool = False
) -> Any:
    """Compose LLM message list, execute tool calls, and return assistant text."""
    llm = settings["llm"]
    provider = llm.get("provider", "openai_compatible")
    set_runtime_tool_settings(settings.get("tools", {}))

    messages: List[Dict[str, str]] = [{"role": "system", "content": llm["system_prompt"]}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})
    tool_events: List[Dict[str, Any]] = []
    tool_name_set = {tool.get("function", {}).get("name") for tool in TOOL_DEFINITIONS if isinstance(tool, dict)}

    direct_intent = _detect_direct_tool_intent(user_message)
    if direct_intent and direct_intent["name"] in tool_name_set:
        direct_result = _execute_tool_with_trace(
            direct_intent["name"], direct_intent["arguments"], provider, "direct_intent", tool_events
        )
        if direct_result.get("ok"):
            app.logger.info("tool_fallback source=direct_intent tool=%s", direct_intent["name"])
            return _finalize_reply(str(direct_result.get("result", "")), tool_events, include_debug)

    trimmed = trim_messages_to_budget(messages, int(llm.get("context_window", 16384)))

    # If tools are unavailable, keep legacy single-shot behavior.
    if not TOOL_DEFINITIONS:
        if provider == "ollama":
            return _finalize_reply(call_ollama(settings, trimmed), tool_events, include_debug)
        return _finalize_reply(call_openai_compatible(settings, trimmed), tool_events, include_debug)

    # Allow a few tool-call turns so the model can chain lookups safely.
    max_tool_rounds = 4

    if provider == "ollama":
        base_url = llm["server_url"].rstrip("/")
        endpoint = f"{base_url}/api/chat"

        tool_messages: List[Dict[str, Any]] = list(trimmed)
        for _ in range(max_tool_rounds):
            payload = {
                "model": llm["model"],
                "messages": tool_messages,
                "tools": TOOL_DEFINITIONS,
                "stream": False,
                "options": {
                    "temperature": float(llm.get("temperature", 0.7)),
                    "num_ctx": int(llm.get("context_window", 16384)),
                },
            }
            response = requests.post(endpoint, json=payload, timeout=120)
            response.raise_for_status()
            data = response.json()

            message = data.get("message", {}) if isinstance(data, dict) else {}
            assistant_content = (message.get("content") or "").strip()
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                if direct_intent and _looks_like_realtime_refusal(assistant_content):
                    direct_result = _execute_tool_with_trace(
                        direct_intent["name"],
                        direct_intent["arguments"],
                        provider,
                        "realtime_refusal",
                        tool_events,
                    )
                    if direct_result.get("ok"):
                        app.logger.info(
                            "tool_fallback source=realtime_refusal tool=%s provider=ollama",
                            direct_intent["name"],
                        )
                        return _finalize_reply(str(direct_result.get("result", "")), tool_events, include_debug)
                return _finalize_reply(
                    assistant_content or "I could not produce a response.", tool_events, include_debug
                )

            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": tool_calls,
            }
            tool_messages.append(assistant_msg)

            for call in tool_calls:
                function_data = call.get("function", {}) if isinstance(call, dict) else {}
                tool_name = function_data.get("name")
                tool_args = function_data.get("arguments", {})
                tool_result = _execute_tool_with_trace(
                    str(tool_name), tool_args, provider, "model_tool_call", tool_events
                )
                tool_messages.append(
                    {
                        "role": "tool",
                        "name": str(tool_name),
                        "content": json.dumps(tool_result),
                    }
                )

        return _finalize_reply(
            "I reached the maximum tool-call steps before finishing your request.",
            tool_events,
            include_debug,
        )

    # OpenAI-compatible tool flow
    base_url = llm["server_url"].rstrip("/")
    endpoint = f"{base_url}/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if llm.get("api_key"):
        headers["Authorization"] = f"Bearer {llm['api_key']}"

    tool_messages_openai: List[Dict[str, Any]] = list(trimmed)
    for _ in range(max_tool_rounds):
        payload = {
            "model": llm["model"],
            "messages": tool_messages_openai,
            "tools": TOOL_DEFINITIONS,
            "tool_choice": "auto",
            "temperature": float(llm.get("temperature", 0.7)),
        }

        response = requests.post(endpoint, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        data = response.json()

        choices = data.get("choices", []) if isinstance(data, dict) else []
        if not choices:
            return _finalize_reply("I could not produce a response.", tool_events, include_debug)

        assistant_message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        assistant_content = (assistant_message.get("content") or "").strip()
        tool_calls = assistant_message.get("tool_calls") or []

        if not tool_calls:
            if direct_intent and _looks_like_realtime_refusal(assistant_content):
                direct_result = _execute_tool_with_trace(
                    direct_intent["name"],
                    direct_intent["arguments"],
                    provider,
                    "realtime_refusal",
                    tool_events,
                )
                if direct_result.get("ok"):
                    app.logger.info(
                        "tool_fallback source=realtime_refusal tool=%s provider=openai_compatible",
                        direct_intent["name"],
                    )
                    return _finalize_reply(str(direct_result.get("result", "")), tool_events, include_debug)
            return _finalize_reply(
                assistant_content or "I could not produce a response.", tool_events, include_debug
            )

        tool_messages_openai.append(
            {
                "role": "assistant",
                "content": assistant_content,
                "tool_calls": tool_calls,
            }
        )

        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            function_data = call.get("function", {})
            tool_name = function_data.get("name")
            tool_args = function_data.get("arguments", {})
            tool_call_id = call.get("id")

            tool_result = _execute_tool_with_trace(
                str(tool_name), tool_args, provider, "model_tool_call", tool_events
            )
            tool_messages_openai.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": str(tool_name),
                    "content": json.dumps(tool_result),
                }
            )

    return _finalize_reply(
        "I reached the maximum tool-call steps before finishing your request.", tool_events, include_debug
    )


def _sanitize_generated_title(raw_title: str) -> str:
    """Normalize generated title to a short clean 3-4 word label."""
    cleaned = (raw_title or "").replace("\n", " ").strip().strip('"\'`')
    cleaned = re.sub(r"^[\W_]+|[\W_]+$", "", cleaned)
    words = [w for w in cleaned.split() if w.strip()]
    if not words:
        return "Untitled Chat"

    # Keep title short and consistent in UI list.
    return " ".join(words[:4])


def _extract_openai_like_text(data: Dict[str, Any]) -> str:
    """Extract generated text from OpenAI-compatible variants."""
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first, dict) else None

        if isinstance(message, dict):
            content = message.get("content", "")
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                parts: List[str] = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict):
                        text_part = item.get("text") or item.get("content") or ""
                        if isinstance(text_part, str) and text_part.strip():
                            parts.append(text_part.strip())
                if parts:
                    return " ".join(parts).strip()

        choice_text = first.get("text") if isinstance(first, dict) else ""
        if isinstance(choice_text, str) and choice_text.strip():
            return choice_text.strip()

    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    response_text = data.get("response")
    if isinstance(response_text, str) and response_text.strip():
        return response_text.strip()

    return ""


def _fallback_title_from_messages(messages: List[Dict[str, str]]) -> str:
    """Create a deterministic short summary title when LLM title generation is unavailable."""
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "can",
        "by",
        "for",
        "from",
        "good",
        "help",
        "hello",
        "hey",
        "hi",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "so",
        "that",
        "the",
        "this",
        "there",
        "thanks",
        "thank",
        "to",
        "we",
        "with",
        "you",
        "your",
    }

    relevant = [m for m in messages if m.get("role") in {"user", "assistant"}]

    # Prefer the most substantial user message to avoid greeting-based titles.
    user_messages = [m for m in relevant if m.get("role") == "user"]
    best_user_text = ""
    best_score = -1
    for msg in user_messages:
        content = (msg.get("content") or "").strip().lower()
        tokens = [t for t in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", content) if t not in stopwords]
        if len(tokens) > best_score:
            best_score = len(tokens)
            best_user_text = content

    if best_user_text:
        ordered_keywords: List[str] = []
        for token in re.findall(r"[a-z0-9][a-z0-9_\-]{2,}", best_user_text):
            if token in stopwords or token.isdigit() or token in ordered_keywords:
                continue
            ordered_keywords.append(token)
            if len(ordered_keywords) == 4:
                break
        if ordered_keywords:
            return " ".join(w.capitalize() for w in ordered_keywords)

    # Last-resort fallback if there were no usable keywords.
    for msg in relevant:
        snippet = re.sub(r"\s+", " ", (msg.get("content") or "").strip())
        words = re.findall(r"[A-Za-z0-9']+", snippet)
        if words:
            return " ".join(words[:4]).title()

    return "Untitled Chat"


def _build_title_prompt(messages: List[Dict[str, str]]) -> str:
    """Create a compact text snapshot for fast title generation."""
    # Keep only a few recent turns to minimize latency and token use.
    recent = [m for m in messages if m.get("role") in {"user", "assistant"}][-8:]
    lines: List[str] = []
    for m in recent:
        role = m.get("role", "user")
        content = (m.get("content") or "").strip().replace("\n", " ")
        if content:
            lines.append(f"{role}: {content[:220]}")
    return "\n".join(lines)


def generate_chat_title(settings: Dict[str, Any], messages: List[Dict[str, str]]) -> str:
    """Generate a very short conversation title using fast low-token calls."""
    llm = settings["llm"]
    provider = llm.get("provider", "openai_compatible")
    context = _build_title_prompt(messages)
    if not context:
        app.logger.info("title_generation source=fallback reason=no_context title='Untitled Chat'")
        return "Untitled Chat"

    instruction = (
        "Create a 3-4 word title for this chat. "
        "Return only the title, no punctuation-heavy framing, no quotes."
    )

    def request_ollama_title() -> str:
        base_url = llm["server_url"].rstrip("/")
        endpoint = f"{base_url}/api/generate"
        payload = {
            "model": llm["model"],
            "prompt": f"{instruction}\n\n{context}\n\nTitle:",
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_ctx": min(4096, int(llm.get("context_window", 16384))),
                "num_predict": 12,
            },
        }
        response = requests.post(endpoint, json=payload, timeout=25)
        response.raise_for_status()
        data = response.json()
        return data.get("response", "")

    def request_openai_compatible_title() -> str:
        base_url = llm["server_url"].rstrip("/")
        endpoint = f"{base_url}/v1/chat/completions"
        headers = {"Content-Type": "application/json"}
        if llm.get("api_key"):
            headers["Authorization"] = f"Bearer {llm['api_key']}"

        payload = {
            "model": llm["model"],
            "messages": [
                {"role": "system", "content": "You write concise chat titles."},
                {"role": "user", "content": f"{instruction}\n\n{context}"},
            ],
            "temperature": 0.1,
            "max_tokens": 12,
        }
        response = requests.post(endpoint, headers=headers, json=payload, timeout=25)
        response.raise_for_status()
        data = response.json()
        return _extract_openai_like_text(data)

    raw_title = ""
    fallback_reason = "empty_model_output"
    try:
        if provider == "ollama":
            raw_title = request_ollama_title()
            if not (raw_title or "").strip():
                raw_title = request_openai_compatible_title()
        else:
            raw_title = request_openai_compatible_title()
            if not (raw_title or "").strip():
                raw_title = request_ollama_title()
    except requests.RequestException as exc:
        fallback_reason = f"request_error:{type(exc).__name__}"
        raw_title = ""

    sanitized = _sanitize_generated_title(raw_title)
    if sanitized == "Untitled Chat":
        fallback_title = _fallback_title_from_messages(messages)
        app.logger.info(
            "title_generation source=fallback provider=%s reason=%s title=%r",
            provider,
            fallback_reason,
            fallback_title,
        )
        return fallback_title

    app.logger.info(
        "title_generation source=chat provider=%s title=%r",
        provider,
        sanitized,
    )

    return sanitized


# -----------------------------
# View routes
# -----------------------------
@app.route("/")
def index():
    settings = load_settings()
    return render_template("chat.html", settings=settings)


@app.route("/settings", methods=["GET", "POST"])
def settings_page():
    if request.method == "POST":
        settings = load_settings()

        # UI settings
        requested_theme = (request.form.get("theme_mode", "matrix-green") or "").strip()
        settings["ui"]["theme_mode"] = requested_theme if requested_theme in THEME_OPTIONS else "matrix-green"

        assistant_avatar_url = request.form.get("assistant_avatar", "").strip()
        user_avatar_url = request.form.get("user_avatar", "").strip()
        assistant_upload = request.files.get("assistant_avatar_file")
        user_upload = request.files.get("user_avatar_file")
        remove_assistant_avatar = request.form.get("remove_assistant_avatar") == "on"
        remove_user_avatar = request.form.get("remove_user_avatar") == "on"

        previous_assistant_avatar = settings.get("ui", {}).get("assistant_avatar", "")
        previous_user_avatar = settings.get("ui", {}).get("user_avatar", "")

        assistant_uploaded_path = _save_avatar_upload(assistant_upload, "assistant")
        user_uploaded_path = _save_avatar_upload(user_upload, "user")

        if remove_assistant_avatar:
            settings["ui"]["assistant_avatar"] = assistant_avatar_url or DEFAULT_SETTINGS["ui"]["assistant_avatar"]
        elif assistant_uploaded_path:
            settings["ui"]["assistant_avatar"] = assistant_uploaded_path
        else:
            settings["ui"]["assistant_avatar"] = assistant_avatar_url or DEFAULT_SETTINGS["ui"]["assistant_avatar"]

        if remove_user_avatar:
            settings["ui"]["user_avatar"] = user_avatar_url or DEFAULT_SETTINGS["ui"]["user_avatar"]
        elif user_uploaded_path:
            settings["ui"]["user_avatar"] = user_uploaded_path
        else:
            settings["ui"]["user_avatar"] = user_avatar_url or DEFAULT_SETTINGS["ui"]["user_avatar"]

        if settings["ui"]["assistant_avatar"] != previous_assistant_avatar:
            _delete_local_avatar_if_managed(previous_assistant_avatar)
        if settings["ui"]["user_avatar"] != previous_user_avatar:
            _delete_local_avatar_if_managed(previous_user_avatar)

        # LLM settings
        settings["llm"]["provider"] = request.form.get("provider", "openai_compatible")
        settings["llm"]["server_url"] = request.form.get("llm_server_url", "").strip()
        settings["llm"]["api_key"] = request.form.get("api_key", "")
        settings["llm"]["model"] = request.form.get("model", "").strip()
        settings["llm"]["context_window"] = int(request.form.get("context_window", 16384) or 16384)
        settings["llm"]["temperature"] = float(request.form.get("temperature", 0.7) or 0.7)
        settings["llm"]["system_prompt"] = request.form.get("system_prompt", "").strip()

        # Text to Speech settings
        settings["tts"]["enabled"] = request.form.get("tts_enabled") == "on"
        settings["tts"]["server_url"] = request.form.get("tts_server_url", "").strip()
        settings["tts"]["endpoint"] = request.form.get("tts_endpoint", "").strip() or "/v1/audio/speech"
        settings["tts"]["model"] = request.form.get("tts_model", "coqui-tts").strip() or "coqui-tts"
        settings["tts"]["voice"] = request.form.get("tts_voice", "").strip()
        settings["tts"]["speed"] = float(request.form.get("tts_speed", 1.0) or 1.0)
        settings["tts"]["response_format"] = request.form.get("tts_response_format", "mp3").strip() or "mp3"
        settings["tts"]["auto_speak_default"] = request.form.get("tts_auto_speak_default") == "on"

        # Speech to Text settings
        settings["stt"]["enabled"] = request.form.get("stt_enabled") == "on"
        settings["stt"]["server_url"] = request.form.get("stt_server_url", "").strip()
        settings["stt"]["endpoint"] = request.form.get("stt_endpoint", "").strip() or "/inference"
        settings["stt"]["temperature"] = float(request.form.get("stt_temperature", 0.0) or 0.0)
        settings["stt"]["vad_threshold"] = float(request.form.get("stt_vad_threshold", 0.018) or 0.018)
        settings["stt"]["silence_timeout_ms"] = int(request.form.get("stt_silence_timeout_ms", 900) or 900)
        settings["stt"]["transcribe_speech_default"] = request.form.get("stt_transcribe_speech_default") == "on"
        settings["stt"]["wake_word_activation"] = request.form.get("stt_wake_word_activation") == "on"
        settings["stt"]["auto_send_on_silence_detected"] = request.form.get("stt_auto_send_on_silence_detected") == "on"
        auto_send_silence_ms = int(request.form.get("stt_auto_send_silence_ms", 600) or 600)
        settings["stt"]["auto_send_silence_ms"] = max(200, min(5000, auto_send_silence_ms))

        # Tool integration settings
        settings["tools"]["openweathermap_api_key"] = request.form.get("tools_openweathermap_api_key", "").strip()
        settings["tools"]["govee_api_key"] = request.form.get("tools_govee_api_key", "").strip()
        settings["tools"]["hue_bridge_ip"] = request.form.get("tools_hue_bridge_ip", "").strip()
        settings["tools"]["vector_serial"] = request.form.get("tools_vector_serial", "").strip()
        settings["tools"]["navidrome_server_url"] = request.form.get("tools_navidrome_server_url", "").strip()
        settings["tools"]["navidrome_username"] = request.form.get("tools_navidrome_username", "").strip()
        settings["tools"]["navidrome_password"] = request.form.get("tools_navidrome_password", "")
        settings["tools"]["navidrome_subsonic_version"] = (
            request.form.get("tools_navidrome_subsonic_version", "1.16.1").strip() or "1.16.1"
        )
        settings["tools"]["navidrome_mp3_cache_dir"] = (
            request.form.get("tools_navidrome_mp3_cache_dir", "mp3_cache").strip() or "mp3_cache"
        )

        # Random chats settings
        settings["random_chats"]["enabled"] = request.form.get("random_chats_enabled") == "on"
        idle_seconds = int(request.form.get("random_chats_idle_seconds", 180) or 180)
        settings["random_chats"]["idle_seconds"] = max(30, min(3600, idle_seconds))

        save_settings(settings)
        return redirect(url_for("settings_page"))

    return render_template("settings.html", settings=load_settings())


@app.route("/about")
def about_page():
    return render_template("about.html", settings=load_settings())


@app.before_request
def _request_started() -> None:
    g._request_started_at = perf_counter()


@app.after_request
def _request_finished(response):
    started = getattr(g, "_request_started_at", None)
    duration_ms = ((perf_counter() - started) * 1000.0) if started is not None else -1.0
    path = request.full_path.rstrip("?") if request.full_path else request.path
    app.logger.info("%s %s -> %s %.1fms", request.method, path, response.status_code, duration_ms)
    return response


# -----------------------------
# JSON API routes
# -----------------------------
@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(load_settings())


@app.route("/api/chat", methods=["POST"])
def api_chat():
    payload = request.get_json(force=True, silent=True) or {}
    user_message = (payload.get("message") or "").strip()
    history = payload.get("history") or []

    if not user_message:
        return jsonify({"error": "Message cannot be empty."}), 400

    safe_history = []
    for item in history:
        role = item.get("role", "")
        content = item.get("content", "")
        if role in {"user", "assistant"} and isinstance(content, str):
            safe_history.append({"role": role, "content": content})

    try:
        settings = load_settings()
        response_payload = generate_assistant_reply(settings, user_message, safe_history, include_debug=True)
        if isinstance(response_payload, dict) and "reply" in response_payload:
            return jsonify(response_payload)
        return jsonify({"reply": str(response_payload), "tool_debug": {"used": False, "events": []}})
    except requests.RequestException as exc:
        return jsonify({"error": f"Upstream request failed: {exc}"}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/random-chat", methods=["POST"])
def api_random_chat():
    payload = request.get_json(force=True, silent=True) or {}
    history = payload.get("history") or []

    safe_history: List[Dict[str, str]] = []
    for item in history:
        role = item.get("role", "")
        content = item.get("content", "")
        if role in {"user", "assistant"} and isinstance(content, str):
            safe_history.append({"role": role, "content": content})

    settings = load_settings()
    random_cfg = settings.get("random_chats", {})
    if not random_cfg.get("enabled"):
        return jsonify({"error": "Random Chats is disabled in settings."}), 400

    try:
        reply = generate_idle_ponder_reply(settings, safe_history)
        return jsonify({"reply": reply})
    except requests.RequestException as exc:
        return jsonify({"error": f"Upstream request failed: {exc}"}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/tts", methods=["POST"])
def api_tts_proxy():
    settings = load_settings()
    tts = settings["tts"]

    if not tts.get("enabled"):
        return jsonify({"error": "TTS is disabled in settings."}), 400

    payload = request.get_json(force=True, silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Text is required."}), 400

    url = f"{tts['server_url'].rstrip('/')}{tts['endpoint']}"
    upstream_payload = {
        # Coqui OpenAI-compatible endpoint expects `input`; `model` is optional but accepted.
        "model": tts.get("model") or "coqui-tts",
        "input": text,
        "voice": tts.get("voice") or tts.get("speaker") or "",
        "speed": float(tts.get("speed", 1.0)),
        "response_format": tts.get("response_format") or "mp3",
    }

    try:
        upstream = requests.post(url, json=upstream_payload, timeout=120)
        upstream.raise_for_status()

        # If upstream returns JSON (e.g., URL to audio), pass through JSON.
        if "application/json" in upstream.headers.get("Content-Type", ""):
            return jsonify(upstream.json())

        # Otherwise assume binary audio and stream it back.
        return (
            upstream.content,
            200,
            {
                "Content-Type": upstream.headers.get("Content-Type", "audio/wav"),
                "Content-Disposition": "inline; filename=tts_output.wav",
            },
        )
    except requests.RequestException as exc:
        return jsonify({"error": f"TTS request failed: {exc}"}), 502


@app.route("/api/audio/stop", methods=["POST"])
def api_stop_audio():
    """Stop any active local audio playback controlled by this app."""
    try:
        navidrome_status = stop_navidrome_playback()
        return jsonify({"ok": True, "navidrome": navidrome_status})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/stt", methods=["POST"])
def api_stt_proxy():
    settings = load_settings()
    stt = settings["stt"]

    if not stt.get("enabled"):
        return jsonify({"error": "Speech input is disabled in settings."}), 400

    if "audio" not in request.files:
        return jsonify({"error": "Missing audio file field 'audio'."}), 400

    audio_file = request.files["audio"]
    if audio_file.filename == "":
        return jsonify({"error": "Audio file is empty."}), 400

    url = f"{stt['server_url'].rstrip('/')}{stt['endpoint']}"

    try:
        files = {
            "file": (audio_file.filename, audio_file.stream, audio_file.mimetype or "application/octet-stream")
        }
        data = {"temperature": str(stt.get("temperature", 0.0))}
        upstream = requests.post(url, files=files, data=data, timeout=180)
        upstream.raise_for_status()

        # Support either plain text, JSON {text: ...}, or OpenAI-style formats.
        content_type = upstream.headers.get("Content-Type", "")
        if "application/json" in content_type:
            body = upstream.json()
            text = body.get("text") or body.get("transcript") or body.get("result") or ""
            if not text and "segments" in body and isinstance(body["segments"], list):
                text = " ".join(seg.get("text", "") for seg in body["segments"]).strip()
            return jsonify({"text": text, "raw": body})

        return jsonify({"text": upstream.text.strip(), "raw": upstream.text})
    except requests.RequestException as exc:
        return jsonify({"error": f"STT request failed: {exc}"}), 502


@app.route("/api/chats", methods=["GET", "POST"])
def api_chats():
    if request.method == "GET":
        with get_db() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM chats ORDER BY updated_at DESC"
            ).fetchall()
            chats = [dict(row) for row in rows]
        return jsonify(chats)

    payload = request.get_json(force=True, silent=True) or {}
    title = (payload.get("title") or "").strip()[:120]
    messages = payload.get("messages") or []

    if not title:
        try:
            app.logger.info("chat_save action=create title_source=auto")
            title = generate_chat_title(load_settings(), messages)
        except Exception:
            title = "Untitled Chat"
            app.logger.exception("chat_save action=create title_source=auto_failed fallback='Untitled Chat'")
    else:
        app.logger.info("chat_save action=create title_source=provided title=%r", title)

    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO chats(title, created_at, updated_at) VALUES (?, ?, ?)",
            (title, now, now),
        )
        chat_id = cursor.lastrowid

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                conn.execute(
                    "INSERT INTO messages(chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (chat_id, role, content, now),
                )

    return jsonify({"chat_id": chat_id}), 201


@app.route("/api/chats/<int:chat_id>", methods=["GET", "DELETE"])
def api_chat_detail(chat_id: int):
    if request.method == "DELETE":
        with get_db() as conn:
            conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
            conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        return jsonify({"status": "deleted"})

    with get_db() as conn:
        chat = conn.execute(
            "SELECT id, title, created_at, updated_at FROM chats WHERE id = ?",
            (chat_id,),
        ).fetchone()
        if not chat:
            return jsonify({"error": "Chat not found."}), 404

        messages = conn.execute(
            "SELECT role, content, created_at FROM messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()

    return jsonify({"chat": dict(chat), "messages": [dict(row) for row in messages]})


@app.route("/api/chats/<int:chat_id>", methods=["PUT"])
def api_chat_update(chat_id: int):
    payload = request.get_json(force=True, silent=True) or {}
    title = (payload.get("title") or "").strip()[:120]
    messages = payload.get("messages") or []
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    with get_db() as conn:
        chat_row = conn.execute("SELECT id, title FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if not chat_row:
            return jsonify({"error": "Chat not found."}), 404

        if not title:
            try:
                app.logger.info("chat_save action=update chat_id=%s title_source=auto", chat_id)
                title = generate_chat_title(load_settings(), messages)
            except Exception:
                title = chat_row["title"] or "Untitled Chat"
                app.logger.exception(
                    "chat_save action=update chat_id=%s title_source=auto_failed fallback=%r",
                    chat_id,
                    title,
                )
        else:
            app.logger.info(
                "chat_save action=update chat_id=%s title_source=provided title=%r",
                chat_id,
                title,
            )

        conn.execute(
            "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, chat_id),
        )
        conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                conn.execute(
                    "INSERT INTO messages(chat_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (chat_id, role, content, now),
                )

    return jsonify({"status": "updated", "chat_id": chat_id})


@app.route("/api/chats/<int:chat_id>/regenerate-title", methods=["POST"])
def api_chat_regenerate_title(chat_id: int):
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    with get_db() as conn:
        chat_row = conn.execute("SELECT id FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if not chat_row:
            return jsonify({"error": "Chat not found."}), 404

        rows = conn.execute(
            "SELECT role, content FROM messages WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        ).fetchall()

        messages = [
            {"role": row["role"], "content": row["content"]}
            for row in rows
            if row["role"] in {"user", "assistant"} and isinstance(row["content"], str) and row["content"].strip()
        ]

        if not messages:
            return jsonify({"error": "Cannot generate a title for an empty chat."}), 400

        try:
            app.logger.info("chat_title_regen chat_id=%s title_source=auto", chat_id)
            title = generate_chat_title(load_settings(), messages)
        except Exception:
            title = _fallback_title_from_messages(messages)
            app.logger.exception(
                "chat_title_regen chat_id=%s title_source=auto_failed fallback=%r",
                chat_id,
                title,
            )

        conn.execute(
            "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, chat_id),
        )

    return jsonify({"status": "updated", "chat_id": chat_id, "title": title})


@app.route("/api/chats/<int:chat_id>/title", methods=["POST"])
def api_chat_set_custom_title(chat_id: int):
    payload = request.get_json(force=True, silent=True) or {}
    title = (payload.get("title") or "").strip()[:120]
    if not title:
        return jsonify({"error": "Custom title cannot be empty."}), 400

    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with get_db() as conn:
        chat_row = conn.execute("SELECT id FROM chats WHERE id = ?", (chat_id,)).fetchone()
        if not chat_row:
            return jsonify({"error": "Chat not found."}), 404

        conn.execute(
            "UPDATE chats SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, chat_id),
        )

    app.logger.info("chat_title_custom chat_id=%s title=%r", chat_id, title)
    return jsonify({"status": "updated", "chat_id": chat_id, "title": title})


if __name__ == "__main__":
    init_storage()
    try:
        from waitress import serve
    except Exception as exc:
        raise SystemExit(
            "Waitress is required to run this app. Install dependencies with: pip install -r requirements.txt"
        ) from exc

    serve(app, host="0.0.0.0", port=5050, threads=8)
else:
    init_storage()
