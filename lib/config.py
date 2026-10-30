"""Configuration + credential loading for the canvas tool.

Everything that ties the tool to one school is configurable, with Fontys as the
built-in default so it works out of the box for that setup. Settings resolve in
this order (first hit wins):

  1. environment variables (CANVAS_URL, PORTFLOW_HOST, ...)
  2. a config file: $CANVAS_CONFIG, else ./canvas.config.json, else
     ~/.config/canvas-portflow/config.json
  3. the Fontys defaults below

Credentials are OPTIONAL. The preferred flow needs no stored password: the user
logs in once themselves in a visible browser window and the persistent profile
remembers the session. Credentials are only read if the user chose to supply
them (env or the config file), to allow fully headless runs. The tool never
intercepts, captures, or writes a password, and never prints credentials.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# ---- defaults (Fontys) ---------------------------------------------------

DEFAULTS = {
    "canvas_url": "https://fhict.instructure.com/",
    "portflow_host": "portfolio.drieam.app",
    # The Canvas external-tool launch for the Portflow tool. For a different
    # school, find this URL in Canvas (the Portfolio/Portflow nav item) and put
    # it in the config file.
    "portflow_launch_url": (
        "https://fhict.instructure.com/accounts/1/external_tools/23360"
        "?launch_type=global_navigation&toolId=portfolio-23360"
    ),
    "ntfy_topic": "",  # empty = ntfy disabled unless the user sets one
    # FeedPulse (optional, per-course external tool).
    "feedpulse_host": "fhict.feedpulse.com",
    "feedpulse_course": "",
    "feedpulse_tool": "",
}

# Where secrets/credentials may live if the user chose to supply them. The first
# one that exists is used. ~/.claude/secrets.json is kept for backward compat.
_SECRET_PATHS = [
    Path(os.environ.get("CANVAS_SECRETS", "")) if os.environ.get("CANVAS_SECRETS") else None,
    Path.home() / ".config" / "canvas-portflow" / "secrets.json",
    Path.home() / ".claude" / "secrets.json",
]
SECRET_PATHS = [p for p in _SECRET_PATHS if p]
# Primary secrets path for messages / where a setup step would write non-secret
# config (never a password).
SECRETS_PATH = SECRET_PATHS[0] if SECRET_PATHS else (
    Path.home() / ".config" / "canvas-portflow" / "secrets.json")

# The persistent browser profile that remembers the login. Kept next to the
# tool by default; override with CANVAS_PROFILE_DIR.
PROFILE_DIR = Path(
    os.environ.get("CANVAS_PROFILE_DIR")
    or (Path(__file__).resolve().parents[1] / ".pw-profile")
)


class ConfigError(RuntimeError):
    pass


# ---- config file ---------------------------------------------------------

def _config_paths() -> list[Path]:
    out = []
    env = os.environ.get("CANVAS_CONFIG")
    if env:
        out.append(Path(env))
    out.append(Path.cwd() / "canvas.config.json")
    out.append(Path.home() / ".config" / "canvas-portflow" / "config.json")
    return out


def _load_config_file() -> dict:
    for p in _config_paths():
        try:
            if p.is_file():
                return json.loads(p.read_text())
        except Exception:
            continue
    return {}


_FILE_CONFIG = _load_config_file()
_ENV_KEYS = {
    "canvas_url": "CANVAS_URL",
    "portflow_host": "PORTFLOW_HOST",
    "portflow_launch_url": "PORTFLOW_LAUNCH_URL",
    "ntfy_topic": "CANVAS_NTFY_TOPIC",
    "feedpulse_host": "CANVAS_FEEDPULSE_HOST",
    "feedpulse_course": "CANVAS_FEEDPULSE_COURSE",
    "feedpulse_tool": "CANVAS_FEEDPULSE_TOOL",
}


def get(key: str) -> str:
    """Resolve one setting: env var -> config file -> default."""
    env_name = _ENV_KEYS.get(key)
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    if key in _FILE_CONFIG and _FILE_CONFIG[key] not in (None, ""):
        return str(_FILE_CONFIG[key])
    return DEFAULTS.get(key, "")


# Convenience module-level values (resolved once).
CANVAS_URL = get("canvas_url")
PORTFLOW_HOST = get("portflow_host")
PORTFLOW_LAUNCH_URL = get("portflow_launch_url")
NTFY_TOPIC = get("ntfy_topic")
FEEDPULSE_HOST = get("feedpulse_host")


def feedpulse_launch_url(course: str | None = None, tool: str | None = None) -> str:
    """Canvas external-tool launch URL for FeedPulse in a course.

    Precedence: explicit args -> env/config -> error if not configured. The
    course and tool ids are school- and semester-specific, so there is no safe
    default; the user must set CANVAS_FEEDPULSE_COURSE / _TOOL or the config file.
    """
    course = course or get("feedpulse_course")
    tool = tool or get("feedpulse_tool")
    if not course or not tool:
        raise ConfigError(
            "FeedPulse is not configured. Set feedpulse_course and feedpulse_tool "
            "in the config file (or CANVAS_FEEDPULSE_COURSE / _TOOL env vars). Find "
            "them in the FeedPulse launch URL in Canvas: "
            ".../courses/<course>/external_tools/<tool>."
        )
    base = CANVAS_URL.rstrip("/")
    return f"{base}/courses/{course}/external_tools/{tool}"


# ---- credentials (optional) ---------------------------------------------

def load_credentials() -> tuple[str | None, str | None]:
    """Return (username, password), or (None, None) if none are supplied.

    Credentials are optional: without them the tool falls back to the
    interactive first-time login (the user signs in once in a visible browser
    window and the persistent profile remembers it). This never raises for
    "no credentials"; it only returns what was explicitly provided via env or a
    secrets file. The password is only ever used to fill the login form, never
    stored or printed by the tool.
    """
    user = os.environ.get("CANVAS_USER")
    pw = os.environ.get("CANVAS_PASS")
    if user and pw:
        return user, pw

    for path in SECRET_PATHS:
        try:
            if not path.is_file():
                continue
            data = json.loads(path.read_text())
        except Exception:
            continue
        entry = (data.get("fontys") or data.get("canvas")
                 or data.get("portflow") or {})
        user = user or entry.get("user") or entry.get("username")
        pw = pw or entry.get("pass") or entry.get("password")
        if user and pw:
            return user, pw

    # Nothing supplied: that is fine, the caller does the interactive login.
    return user, pw
