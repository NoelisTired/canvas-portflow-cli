"""Microsoft Azure AD login flow (login.microsoftonline.com).

Two-step:
  1. POST /common/GetCredentialType with the username — Microsoft tells us
     either to keep going on their page (managed account) or to follow a
     FederationRedirectUrl to the tenant's own IdP.
  2. If managed: POST the password form with the captured flow token + ctx.

This module is only used as a fallback — Fontys currently federates straight
to ADFS at login.fhict.nl, but the fallback is here in case the tenant config
flips back to MS-hosted auth.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import requests

from ..http import origin_of
from .adfs import submit_adfs_form
from .kmsi import walk_to_canvas
from .mfa import assert_no_mfa


_CONFIG_KEYS = ("sFT", "sCtx", "sFTName", "urlPost", "canary", "hpgid", "hpgact")


def extract_js_config(html: str) -> dict[str, str]:
    """Pull select fields out of Microsoft's embedded `$Config = {...}` blob."""
    fields: dict[str, str] = {}
    for key in _CONFIG_KEYS:
        m = re.search(rf'"{key}":"([^"]+)"', html)
        if m:
            fields[key] = m.group(1)
    return fields


def do_microsoft_flow(
    session: requests.Session,
    r: requests.Response,
    username: str,
    password: str,
) -> None:
    cfg = extract_js_config(r.text)
    if "sFT" not in cfg or "sCtx" not in cfg:
        raise RuntimeError(
            "Could not find Microsoft login config on page. "
            "The SSO flow may have changed."
        )

    ms_origin = origin_of(r.url)
    gct_data = _get_credential_type(session, r.url, ms_origin, cfg, username)

    # Microsoft zegt "ga jij maar naar je eigen IdP babbelen" — wij volgen braaf.
    redirect = (gct_data.get("Credentials") or {}).get("FederationRedirectUrl")
    if redirect:
        r2 = session.get(redirect, allow_redirects=True)
        r2.raise_for_status()
        return submit_adfs_form(session, r2, username, password)

    _submit_password(session, r, ms_origin, cfg, gct_data, username, password)


def _get_credential_type(
    session: requests.Session,
    referer: str,
    ms_origin: str,
    cfg: dict[str, str],
    username: str,
) -> dict:
    gct = session.post(
        f"{ms_origin}/common/GetCredentialType?mkt=en-US",
        json={
            "username": username,
            "isOtherIdpSupported": True,
            "checkPhones": False,
            "isRemoteNGCSupported": True,
            "isCookieBannerShown": False,
            "isFidoSupported": True,
            "originalRequest": cfg["sCtx"],
            "country": "NL",
            "flowToken": cfg["sFT"],
        },
        headers={"Referer": referer, "Origin": ms_origin},
    )
    gct.raise_for_status()
    return gct.json()


def _submit_password(
    session: requests.Session,
    r: requests.Response,
    ms_origin: str,
    cfg: dict[str, str],
    gct_data: dict,
    username: str,
    password: str,
) -> None:
    flow_token = gct_data.get("FlowToken") or cfg["sFT"]
    post_url = cfg.get("urlPost") or "/common/login"
    if post_url.startswith("/"):
        post_url = ms_origin + post_url

    payload = {
        "i13": "0",
        "login": username,
        "loginfmt": username,
        "type": "11",
        "LoginOptions": "3",
        "passwd": password,
        "ps": "2",
        "canary": cfg.get("canary", ""),
        "ctx": cfg["sCtx"],
        "flowToken": flow_token,
        "NewUser": "1",
        "fspost": "0",
        "i21": "0",
        "CookieDisclosure": "0",
        "IsFidoSupported": "1",
        "isSignupPost": "0",
        "i19": "1000",
    }
    r2 = session.post(post_url, data=payload, headers={"Referer": r.url})
    r2.raise_for_status()

    assert_no_mfa(r2)
    walk_to_canvas(session, r2)
