"""Fontys ADFS login (login.fhict.nl/adfs/ls/).

Standard ADFS forms-based authentication: scrape the form, fill the
detected username/password fields, preserve hidden inputs (notably the
SAMLRequest passthrough and `AuthMethod=FormsAuthentication`), POST back.
"""

from __future__ import annotations

import re

import requests

from ..http import parse_form, resolve_action
from .kmsi import walk_to_canvas
from .mfa import assert_no_mfa


_USER_FIELD_RE = re.compile(r"user|email|login", re.IGNORECASE)
_PASS_FIELD_RE = re.compile(r"pass", re.IGNORECASE)


def submit_adfs_form(
    session: requests.Session,
    r: requests.Response,
    username: str,
    password: str,
) -> None:
    form = parse_form(r.text)
    if form.action is None and not form.inputs:
        raise RuntimeError(f"No form found on ADFS page: {r.url}")

    user_field = next(
        (n for n in form.inputs if _USER_FIELD_RE.search(n)), "UserName"
    )
    pass_field = next(
        (n for n in form.inputs if _PASS_FIELD_RE.search(n)), "Password"
    )
    form.inputs[user_field] = username
    form.inputs[pass_field] = password

    # ADFS wil dit veld zien — anders gooit-ie een vage 500 en lacht-ie je uit.
    if "AuthMethod" in form.inputs and not form.inputs["AuthMethod"]:
        form.inputs["AuthMethod"] = "FormsAuthentication"

    action = resolve_action(form.action, r.url)
    r2 = session.post(action, data=form.inputs, headers={"Referer": r.url})
    r2.raise_for_status()

    assert_no_mfa(r2)
    walk_to_canvas(session, r2)
