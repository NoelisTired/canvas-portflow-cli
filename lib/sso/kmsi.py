"""Walk the post-credentials form chain back to Canvas.

After credentials are accepted at the IdP, the browser is bounced through
zero or more interstitial forms — most commonly the Microsoft "Keep me
signed in?" (KMSI) page and the auto-submitting SAMLResponse form. This
module replays those POSTs until we land back on the Canvas origin.
"""

from __future__ import annotations

import requests

from ..http import parse_form, resolve_action


def walk_to_canvas(
    session: requests.Session,
    r: requests.Response,
    canvas_host: str = "fhict.instructure.com",
    max_hops: int = 5,
) -> None:
    # Trampoline tussen SAML, KMSI en wat Microsoft verder nog verzint —
    # blijven stuiteren tot we terug op Canvas zijn.
    for _ in range(max_hops):
        if canvas_host in r.url and "/login" not in r.url:
            return

        form = parse_form(r.text)
        if form.action is None and not form.inputs:
            break

        # "Aangemeld blijven?" — nee bedankt, anders zeurt-ie elke run.
        if "kmsi" in r.url.lower() or "DontShowAgain" in form.inputs:
            form.inputs["DontShowAgain"] = "true"

        action = resolve_action(form.action, r.url)
        r = session.post(action, data=form.inputs, headers={"Referer": r.url})
        r.raise_for_status()

    if canvas_host not in r.url:
        raise RuntimeError(
            f"SSO finished at unexpected URL: {r.url}\n"
            "Check that the username/password are correct, or that no MFA is required."
        )
