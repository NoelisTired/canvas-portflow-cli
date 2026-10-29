"""Top-level Canvas SSO orchestrator.

Hits the Canvas root, observes which IdP host Canvas redirected to, and
delegates to the matching IdP handler:

    fhict.instructure.com (no /login) -> already authenticated, done
    login.fhict.nl                    -> lib.sso.adfs
    login.microsoftonline.com         -> lib.sso.microsoft
"""

from __future__ import annotations

import requests

from ..http import host_of
from .adfs import submit_adfs_form
from .microsoft import do_microsoft_flow


class UnknownIdpError(RuntimeError):
    pass


class CanvasAuthenticator:
    def __init__(
        self,
        session: requests.Session,
        canvas_url: str = "https://fhict.instructure.com/",
    ) -> None:
        self._session = session
        self._canvas_url = canvas_url

    def login(self, username: str, password: str) -> None:
        r = self._session.get(self._canvas_url, allow_redirects=True)
        r.raise_for_status()

        host = host_of(r.url)

        if "fhict.instructure.com" in host and "/login" not in r.url:
            return  # cookie nog vers — gratis ritje, ja graag

        # Fontys ADFS has appeared under more than one host (login.fhict.nl and
        # login.fontysict.nl). Both are the same /adfs/ls/ forms flow, so treat
        # any ADFS host the same way.
        if "login.fhict.nl" in host or "login.fontysict.nl" in host or "/adfs/" in r.url:
            return submit_adfs_form(self._session, r, username, password)

        if "login.microsoftonline.com" in host:
            return do_microsoft_flow(self._session, r, username, password)

        raise UnknownIdpError(f"Unexpected landing URL: {r.url}")
