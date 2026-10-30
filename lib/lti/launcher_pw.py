"""Playwright-driven LTI 1.3 launch into Portflow.

Pure requests cannot complete Portflow's launch: the handshake routes through
sso.canvaslms.com/api/lti/authorize_redirect and uses LTI Platform Storage
(postMessage to the SSO iframe, driven by JavaScript). So a real browser does
the launch, and we then hand the resulting session (cookies + CSRF) to a plain
requests.Session for the fast API work.

Login itself is also done in-browser here (ADFS form fill), which doubles as
the MFA escape hatch: set CANVAS_HEADFUL=1 to watch/approve interactively, and
the persistent profile remembers the session so later runs skip the login.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import requests

from ..config import CANVAS_URL, PORTFLOW_HOST, PROFILE_DIR  # noqa: F401

# Re-exported for the FeedPulse launcher and others; resolved from config so the
# tool is not hard-wired to one school.
CANVAS_HOME = CANVAS_URL
PORTFLOW_ORIGIN = f"https://{PORTFLOW_HOST}"


class PlaywrightLaunchError(RuntimeError):
    pass


def launch_with_playwright(
    canvas_tool_url: str,
    username: str | None = None,
    password: str | None = None,
    headful: bool | None = None,
    timeout_ms: int = 45000,
) -> tuple[requests.Session, dict]:
    """Run the full browser launch and return (requests.Session, info).

    info = {csrf, portfolio_id, final_url}. The session carries the Portflow
    cookies and the X-CSRF-Token header, ready for PortflowClient.
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

        # Portflow authorizes its API with an Authorization: Bearer <JWT> the
        # SPA keeps in memory (not a cookie/storage). Capture it two ways for
        # reliability: (a) a CDP-level request listener, and (b) an in-page
        # fetch/XHR wrapper installed on every document before scripts run, so
        # the token is stashed on window regardless of navigation timing.
        bearer = {"value": None}
        ctx.add_init_script(
            """
            (() => {
              const stash = (h) => {
                try {
                  const a = h && (h.get ? h.get('authorization') : h['authorization'] || h['Authorization']);
                  if (a && /^bearer /i.test(a)) window.__pfBearer = a;
                } catch (e) {}
              };
              const of = window.fetch;
              window.fetch = function (input, init) {
                try {
                  const hh = (init && init.headers) || (input && input.headers);
                  if (hh) stash(hh instanceof Headers ? hh : new Headers(hh));
                } catch (e) {}
                return of.apply(this, arguments);
              };
              const os = XMLHttpRequest.prototype.setRequestHeader;
              XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
                try { if (/^authorization$/i.test(k) && /^bearer /i.test(v)) window.__pfBearer = v; } catch (e) {}
                return os.apply(this, arguments);
              };
            })();
            """
        )
        pid = {"value": None}

        def _grab(req):
            if "/api/v1/" not in req.url:
                return
            if not bearer["value"]:
                auth = req.headers.get("authorization")
                if auth and auth.lower().startswith("bearer "):
                    bearer["value"] = auth
            if not pid["value"]:
                m = re.search(r"/portfolios/(\d+)", req.url)
                if m:
                    pid["value"] = int(m.group(1))

        page.on("request", _grab)

        def _grab_pid_from_launch(resp):
            if pid["value"] or "/lti/launches" not in resp.url:
                return
            try:
                m = re.search(
                    r'(?:&quot;|")portfolio(?:&quot;|")'
                    r'\s*:\s*\{\s*(?:&quot;|")id(?:&quot;|")\s*:\s*(\d+)',
                    resp.text(),
                )
                if m:
                    pid["value"] = int(m.group(1))
            except Exception:
                pass

        page.on("response", _grab_pid_from_launch)

        try:
            _login_if_needed(page, username, password)
            # The tool URL renders the LTI launch inside an iframe, so the top
            # frame stays on Canvas. Load it, find the auto-submit launch form
            # (in the page or a child frame) and re-submit it with target=_self
            # so the whole tab drives the open_id -> sso authorize_redirect ->
            # platform-storage -> id_token -> /lti/launches chain top-level.
            page.goto(canvas_tool_url, wait_until="domcontentloaded")
            _submit_launch_form_top_level(page, timeout_ms)
            _wait_for_portflow(page, timeout_ms, bearer)

            csrf = page.evaluate(
                "() => { const m = document.querySelector('meta[name=csrf-token]');"
                " return m && m.content; }"
            )
            if not bearer["value"]:
                raise PlaywrightLaunchError(
                    "Launched but never saw the Authorization Bearer token on "
                    "an API request. Portflow's auth scheme may have changed."
                )
            # Give the SPA a brief moment to fire a /portfolios/<id> call so we
            # can intercept the id, if the launch response didn't already yield it.
            for _ in range(10):
                if pid["value"]:
                    break
                page.wait_for_timeout(300)
            portfolio_id = pid["value"] or _portfolio_id(page)

            session = _export_session(ctx, csrf, bearer["value"])
            return session, {
                "csrf": csrf,
                "portfolio_id": portfolio_id,
                "final_url": page.url,
            }
        except PWTimeout as e:
            raise PlaywrightLaunchError(
                f"Timed out during launch (last URL: {page.url}). If MFA is "
                "required, run once with CANVAS_HEADFUL=1 to approve it."
            ) from e
        finally:
            ctx.close()


def _login_if_needed(page, username: str | None, password: str | None) -> None:
    page.goto(CANVAS_HOME, wait_until="domcontentloaded")
    if "/login" not in page.url and _on_canvas(page):
        return  # persistent profile still authenticated

    # No stored credentials: the user logs in themselves in the visible window.
    # The tool never sees or stores the password; the persistent profile keeps
    # the session for later headless runs.
    if not (username and password):
        _interactive_login(page)
        return

    # ADFS / SSO login form. Field names vary; match by type/attr.
    try:
        page.wait_for_selector("input[type=password]", timeout=15000)
    except Exception:
        if _on_canvas(page):
            return
        raise
    user_sel = (
        "input[name*=User i], input[name*=user i], input[type=email], "
        "input[name*=login i]"
    )
    page.fill(user_sel, username)
    page.fill("input[type=password]", password)
    page.click(
        "button[type=submit], input[type=submit], #submitButton, span#submitButton"
    )
    # Dismiss a "keep me signed in" prompt if it appears.
    try:
        page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    for label in ("No", "Nee", "Ja", "Yes"):
        btn = page.query_selector(f"input[value='{label}'], button:has-text('{label}')")
        if btn:
            try:
                btn.click()
                break
            except Exception:
                pass
    page.wait_for_url(f"**{_canvas_host()}/**", timeout=20000)


def _canvas_host() -> str:
    """The Canvas host (e.g. fhict.instructure.com) from the configured URL."""
    from urllib.parse import urlparse
    return urlparse(CANVAS_HOME).netloc


def _on_canvas(page) -> bool:
    """True when the page is on the configured Canvas host (signed in)."""
    return _canvas_host() in page.url


def _interactive_login(page, timeout_s: int = 300) -> None:
    """First-time login: let the user sign in themselves in the open window.

    Used when no credentials are configured. We never fill or read the password;
    we only wait until the browser lands on Canvas (the user completed login,
    including any MFA), after which the persistent profile remembers the session.
    Requires a visible window: if running headless, raise with clear guidance.
    """
    import sys
    import time

    if os.environ.get("CANVAS_HEADFUL") != "1":
        raise PlaywrightLaunchError(
            "First-time login needs a visible browser window, but no saved "
            "session and no credentials were found.\n"
            "Run the one-time setup so you can log in yourself:\n"
            "    CANVAS_HEADFUL=1 python canvas.py --login\n"
            "You type your own password in the window; the tool never sees it, "
            "and remembers the session for later headless runs."
        )
    print("\n>> Log in to Canvas in the browser window that just opened.",
          file=sys.stderr)
    print(">> The tool never sees your password; it just waits until you are in.",
          file=sys.stderr)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if "/login" not in page.url and _on_canvas(page):
                print(">> Logged in. Session saved to the browser profile.",
                      file=sys.stderr)
                return
        except Exception:
            pass
        page.wait_for_timeout(1000)
    raise PlaywrightLaunchError(
        "Timed out waiting for you to finish logging in (5 min)."
    )


def _submit_launch_form_top_level(page, timeout_ms: int) -> None:
    """Find the LTI open_id launch form and submit it as a full-page nav."""
    deadline = timeout_ms
    js = """() => {
        const find = (doc) => doc.querySelector(
            "form[action*='lti/open_id'], form[id^='tool_form_']");
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
    # The iframe may post its own form automatically; poll briefly for it,
    # then force a top-level submit so we leave Canvas.
    for _ in range(int(deadline / 500)):
        if "instructure.com" not in page.url:
            return
        if page.evaluate(js):
            return
        page.wait_for_timeout(500)
    # Already navigated away? fine. Otherwise let wait_for_portflow time out
    # with a useful message.


def _wait_for_portflow(page, timeout_ms: int, bearer: dict) -> None:
    # The handshake fires several rapid form_post navigations; waiting on a
    # single navigation event races with them (ERR_ABORTED / detached frame).
    # The SPA authorizes with an in-memory Bearer, so "ready" = we are on
    # Portflow, the app shell rendered (csrf meta), and the app has fired at
    # least one authorized API request (so we captured the Bearer).
    import time

    deadline = time.monotonic() + timeout_ms / 1000
    nudged_at = 0.0
    while time.monotonic() < deadline:
        try:
            if page.url.startswith(PORTFLOW_ORIGIN):
                # Prefer the in-page wrapper's capture (survives navigation).
                if not bearer["value"]:
                    try:
                        inpage = page.evaluate("() => window.__pfBearer || null")
                        if inpage:
                            bearer["value"] = inpage
                    except Exception:
                        pass
                if bearer["value"]:
                    has_meta = page.evaluate(
                        "() => !!document.querySelector('meta[name=csrf-token]')"
                    )
                    if has_meta:
                        return
                # On /lti/launches the id_token is posted but the SPA may not
                # have booted yet, so no API call has fired and no Bearer was
                # seen. Nudge it to the app once to make it call the API.
                elif ("/lti/launches" in page.url
                      and time.monotonic() - nudged_at > 3):
                    nudged_at = time.monotonic()
                    try:
                        page.goto(f"{PORTFLOW_ORIGIN}/portfolio",
                                  wait_until="domcontentloaded")
                    except Exception:
                        pass
        except Exception:
            pass  # mid-navigation; retry
        page.wait_for_timeout(400)
    raise PlaywrightLaunchError(
        f"Did not reach an authorized Portflow session (last URL: {page.url}). "
        "If MFA is required, run once with CANVAS_HEADFUL=1 to approve it."
    )


def _portfolio_id(page) -> int | None:
    pid = page.evaluate(
        """async () => {
            const r = await fetch('/api/v1/users/current',
                {headers:{Accept:'application/json'}});
            if (!r.ok) return null;
            // users/current has no pid; derive from the bootstrap in the DOM.
            const html = document.documentElement.innerHTML;
            const m = html.match(/\\"portfolio\\":\\{\\"id\\":(\\d+)/);
            return m ? parseInt(m[1], 10) : null;
        }"""
    )
    return pid


def _export_session(ctx, csrf: str | None, bearer: str | None) -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": ctx.pages[0].evaluate("() => navigator.userAgent"),
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    if csrf:
        s.headers["X-CSRF-Token"] = csrf
    if bearer:
        s.headers["Authorization"] = bearer
    for c in ctx.cookies():
        s.cookies.set(
            c["name"], c["value"],
            domain=c.get("domain"), path=c.get("path", "/"),
        )
    return s
