"""Reusable pieces for the `canvas` skill.

Login (`CanvasAuthenticator`) + transport (`build_session`) are vendored
unchanged from NoelisTired/fontys-canvas-bypass. The LTI 1.3 launcher and
the Portflow client are specific to this skill.
"""

from .http import build_session
from .sso import CanvasAuthenticator, MfaChallengeError, UnknownIdpError
from .lti.launcher13 import Lti13Launcher, Lti13LaunchError
from .lti.launcher_pw import launch_with_playwright, PlaywrightLaunchError
from .portflow import PortflowClient, PortflowError

__all__ = [
    "build_session",
    "CanvasAuthenticator",
    "MfaChallengeError",
    "UnknownIdpError",
    "Lti13Launcher",
    "Lti13LaunchError",
    "launch_with_playwright",
    "PlaywrightLaunchError",
    "PortflowClient",
    "PortflowError",
]
