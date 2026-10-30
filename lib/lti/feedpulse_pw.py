"""Playwright launcher for FeedPulse (fhict.feedpulse.com).

FeedPulse is another LTI tool in Canvas, launched from a course's external
tool. Unlike Portflow it authorizes its /api/v3 with a plain cookie session plus
an X-CSRF-Token meta tag (no in-memory Bearer), so the launch is simpler: log in
to Canvas, open the tool launch, wait until the tab is on fhict.feedpulse.com,
read the csrf meta, and export the browser cookies as a requests.Session.

Reuses the Canvas login / launch-form helpers from launcher_pw so the ADFS flow
and persistent profile are shared with the Portflow launcher.
"""

from __future__ import annotations

import os
import time

import requests

from .launcher_pw import (
    PROFILE_DIR,
    PlaywrightLaunchError,
    _export_session,
    _login_if_needed,
)

FEEDPULSE_ORIGIN = "https://fhict.feedpulse.com"


def _submit_feedpulse_launch(page, timeout_ms: int) -> None:
    """Submit the LTI 1.1 launch form that posts to feedpulse.com/launch.

    FeedPulse is an LTI 1.1 tool: the Canvas external-tool page renders a form
    (usually inside an iframe) whose action is .../launch and whose body carries
    the OAuth-signed parameters. That POST is what sets the _feedpulse_session
    cookie, so we must submit *that* form (not the LTI 1.3 open_id form), as a
    full-page navigation so the session lands on the top-level origin.
    """
    js = r"""() => {
        const isLaunch = (f) => f && /feedpulse\.com\/launch|\/launch$/.test(f.action || "");
        const find = (doc) => {
            for (const f of doc.querySelectorAll('form')) if (isLaunch(f)) return f;
            return null;
        };
        let f = find(document);
        if (!f) {
            for (const fr of document.querySelectorAll('iframe')) {
                try { f = find(fr.contentDocument); if (f) break; } catch (e) {}
            }
        }
        if (!f) return false;
        f.target = '_self';
        f.submit();
        return true;
    }"""
    for _ in range(int(timeout_ms / 500)):
        if page.url.startswith(FEEDPULSE_ORIGIN):
            return
        try:
            if page.evaluate(js):
                return
        except Exception:
            pass
        page.wait_for_timeout(500)


def launch_feedpulse(
    canvas_tool_url: str,
    username: str,
    password: str,
    headful: bool | None = None,
    timeout_ms: int = 45000,
) -> tuple[requests.Session, dict]:
    """Launch FeedPulse and return (requests.Session, info).

    info = {csrf, final_url}. The session carries the FeedPulse cookies and the
    X-CSRF-Token header, ready for FeedPulseClient against /api/v3.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError as e:  # pragma: no cover
        raise PlaywrightLaunchError(
            "Playwright is not installed. In the skill venv run:\n"
            "  ./.venv/bin/pip install playwright && "
            "./.venv/bin/python -m playwright install chromium"
        ) from e

    if headful is None:
        headful = os.environ.get("CANVAS_HEADFUL") == "1"

    PROFILE_DIR.mkdir(exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=not headful,
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.set_default_timeout(timeout_ms)

        try:
            _login_if_needed(page, username, password)
            page.goto(canvas_tool_url, wait_until="domcontentloaded")
            _submit_feedpulse_launch(page, timeout_ms)
            _wait_for_feedpulse(page, timeout_ms)

            csrf = page.evaluate(
                "() => { const m = document.querySelector('meta[name=csrf-token]');"
                " return m && m.content; }"
            )
            session = _export_session(ctx, csrf, None)
            return session, {"csrf": csrf, "final_url": page.url}
        except PWTimeout as e:
            raise PlaywrightLaunchError(
                f"Timed out during FeedPulse launch (last URL: {page.url}). If "
                "MFA is required, run once with CANVAS_HEADFUL=1 to approve it."
            ) from e
        finally:
            ctx.close()


def _wait_for_feedpulse(page, timeout_ms: int) -> None:
    """Wait until FeedPulse has an authorized API session.

    Landing on the origin with a csrf meta is not enough: the LTI /launch POST
    must have set the session cookie. We confirm by probing /api/v3/users/current
    in-page until it returns 200 (not the 401 "Login Required" seen before the
    session is established).
    """
    deadline = time.monotonic() + timeout_ms / 1000
    last = ""
    while time.monotonic() < deadline:
        try:
            if page.url.startswith(FEEDPULSE_ORIGIN):
                probe = page.evaluate(
                    """async () => {
                        try {
                            const r = await fetch('/api/v3/users/current',
                                {headers:{Accept:'application/json'}});
                            return r.status;
                        } catch (e) { return -1; }
                    }"""
                )
                last = f"api status {probe}"
                if probe == 200:
                    return
        except Exception:
            pass  # mid-navigation; retry
        page.wait_for_timeout(400)
    raise PlaywrightLaunchError(
        f"Reached FeedPulse but never got an authorized API session "
        f"(last URL: {page.url}, {last}). If MFA is required, run once with "
        "CANVAS_HEADFUL=1 to approve it."
    )
