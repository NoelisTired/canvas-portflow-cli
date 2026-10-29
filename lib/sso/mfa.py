"""MFA detection — if the IdP shows any of these strings, we bail out."""

from __future__ import annotations

import requests


MFA_MARKERS = (
    "verify your identity",
    "approve sign in request",
    "authenticator app",
    "enter the code",
    "two-factor",
    "multifactor",
    "/proofup",
    "begincheckpoll",
)


class MfaChallengeError(RuntimeError):
    """Raised when an IdP page asks for a second factor."""


def assert_no_mfa(r: requests.Response) -> None:
    # Als Microsoft hier z'n authenticator-app erbij sleept is het feestje voorbij.
    body = r.text.lower()
    if any(m in body for m in MFA_MARKERS):
        raise MfaChallengeError(
            "MFA challenge detected — requests-only login cannot continue.\n"
            "Use Playwright for the initial login and cache the session cookies."
        )
