"""LTI 1.3 (OIDC) launcher for the Portflow external tool.

Unlike FeedPulse (LTI 1.0, Bearer-JWT), Portflow uses an LTI 1.3 / OpenID
Connect launch and then authenticates its own API with a *cookie session*
plus an `X-CSRF-Token` header. So the job here is:

    1. GET the Canvas external_tools launch URL  -> HTML with an auto-submit
       <form> that POSTs OIDC login params to the tool's /lti/open_id.
    2. Replay that POST + every auto-submit form that follows (Canvas
       authorize_redirect -> id_token form -> tool /lti/launches), letting
       requests carry cookies, until we land on the tool origin.
    3. Scrape the CSRF token from the landing page's <meta name="csrf-token">.

After launch() the shared session holds the tool's session cookie and the
CSRF header, ready for Portflow API calls.

This is the fragile seam: it leans on Drieam/Canvas returning HTML forms we
can replay without JavaScript. If Drieam changes the launch handshake, this
is the first thing to break — the errors below say so explicitly.
"""

from __future__ import annotations

import re

import requests

from ..http import origin_of, parse_form, resolve_action

_CSRF_META_RE = re.compile(
    r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
# A meta/body carrying the token can also appear content-first.
_CSRF_META_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']csrf-token["\']',
    re.IGNORECASE,
)


class Lti13LaunchError(RuntimeError):
    pass


class Lti13Launcher:
    def __init__(self, session: requests.Session, tool_host: str) -> None:
        self._session = session
        self._tool_host = tool_host  # e.g. "portfolio.drieam.app"

    def launch(self, canvas_tool_url: str, max_hops: int = 6) -> dict:
        """Run the OIDC launch and install the CSRF header on the session.

        Returns {csrf, final_url}.
        """
        r = self._session.get(canvas_tool_url)
        r.raise_for_status()

        form = parse_form(r.text)
        if not form.action and not form.inputs:
            raise Lti13LaunchError(
                f"No LTI launch form on {canvas_tool_url}. Either the Canvas "
                "session is not authenticated, or the tool id is wrong."
            )

        # Replay the auto-submit form trampoline: open_id -> authorize ->
        # id_token -> /lti/launches. Each step is a POST of a form's hidden
        # inputs to its action, with cookies carried by the session.
        from urllib.parse import urlparse

        for hop in range(max_hops):
            action = resolve_action(form.action, r.url)
            r = self._session.post(
                action,
                data=form.inputs,
                headers={"Referer": r.url, "Origin": origin_of(r.url)},
                allow_redirects=True,
            )
            r.raise_for_status()

            # Match on host + path only. The Canvas authorize step carries the
            # tool host inside its `redirect_uri` query param, so checking the
            # whole URL would break one hop too early, before /lti/launches.
            parsed = urlparse(r.url)
            on_tool = parsed.netloc.lower() == self._tool_host.lower()
            if on_tool and parsed.path.endswith("/lti/launches"):
                break
            if on_tool and "/lti/open_id" not in parsed.path:
                # Landed on the tool app itself (SPA entry) — good enough.
                break

            next_form = parse_form(r.text)
            if not next_form.action and not next_form.inputs:
                # No further auto-submit form; assume we are on the app.
                break
            form = next_form
        else:
            raise Lti13LaunchError(
                f"OIDC launch did not settle on {self._tool_host} after "
                f"{max_hops} hops (last URL: {r.url}). The launch handshake "
                "may have changed."
            )

        if "Invalid open id connect response" in r.text or "already been consumed" in r.text:
            raise Lti13LaunchError(
                "Launch token was already consumed. A launch is single-use; "
                "start a fresh one instead of reusing a URL."
            )

        csrf = self._extract_csrf(r.text)
        if not csrf:
            # The SPA sometimes needs one more GET of the app root for the meta.
            root = f"{origin_of(r.url)}/"
            rr = self._session.get(root, headers={"Referer": r.url})
            csrf = self._extract_csrf(rr.text)
        if not csrf:
            raise Lti13LaunchError(
                "Launched, but no csrf-token meta tag was found. Portflow's "
                "page shape may have changed; mutations will fail without it."
            )

        self._session.headers["X-CSRF-Token"] = csrf
        # Portflow's XHRs are JSON and send these; mirror them so we look native.
        self._session.headers.setdefault("Accept", "application/json")
        self._session.headers["X-Requested-With"] = "XMLHttpRequest"
        portfolio_id = self._extract_portfolio_id(r.text)
        return {"csrf": csrf, "final_url": r.url, "portfolio_id": portfolio_id}

    @staticmethod
    def _extract_portfolio_id(html: str) -> int | None:
        # The landing page embeds a bootstrap blob (HTML-escaped) that carries
        # "portfolio":{"id":NNNNN,...}. Match both escaped and raw forms.
        for pat in (
            r'&quot;portfolio&quot;:\{&quot;id&quot;:(\d+)',
            r'"portfolio":\{"id":(\d+)',
            r'portfolios/(\d+)',
        ):
            m = re.search(pat, html)
            if m:
                return int(m.group(1))
        return None

    @staticmethod
    def _extract_csrf(html: str) -> str | None:
        m = _CSRF_META_RE.search(html) or _CSRF_META_RE2.search(html)
        return m.group(1) if m else None
