"""Credential + configuration loading for the canvas skill.

Credentials come only from ~/.claude/secrets.json (key `fontys`) or env vars.
Nothing is ever written back and nothing is printed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

SECRETS_PATH = Path.home() / ".claude" / "secrets.json"

CANVAS_URL = "https://fhict.instructure.com/"
PORTFLOW_HOST = "portfolio.drieam.app"
# Canvas global-navigation launch for the Portflow external tool.
PORTFLOW_LAUNCH_URL = (
    "https://fhict.instructure.com/accounts/1/external_tools/23360"
    "?launch_type=global_navigation&toolId=portfolio-23360"
)


class ConfigError(RuntimeError):
    pass


def load_credentials() -> tuple[str, str]:
    """Return (username, password) from env or secrets.json key `fontys`.

    Env wins: CANVAS_USER / CANVAS_PASS. Otherwise secrets.json ->
    {"fontys": {"user": "...", "pass": "..."}} (also accepts username/password).
    """
    user = os.environ.get("CANVAS_USER")
    pw = os.environ.get("CANVAS_PASS")
    if user and pw:
        return user, pw

    if not SECRETS_PATH.exists():
        raise ConfigError(
            f"No credentials. Set CANVAS_USER/CANVAS_PASS, or add a `fontys` "
            f"entry to {SECRETS_PATH}: "
            '{"fontys": {"user": "<nr>@student.fontys.nl", "pass": "<password>"}}'
        )
    data = json.loads(SECRETS_PATH.read_text())
    f = data.get("fontys") or data.get("canvas") or {}
    user = user or f.get("user") or f.get("username")
    pw = pw or f.get("pass") or f.get("password")
    if not user or not pw:
        raise ConfigError(
            f"`fontys` entry in {SECRETS_PATH} needs both `user` and `pass`."
        )
    return user, pw
