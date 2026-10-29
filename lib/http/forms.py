"""HTML <form> scrapers used across SSO + LTI flows."""

from __future__ import annotations

from html.parser import HTMLParser


class FormParser(HTMLParser):
    """Extract the first <form>'s action and all <input name=...> values."""

    def __init__(self) -> None:
        super().__init__()
        self.action: str | None = None
        self.method: str = "post"
        self.inputs: dict[str, str] = {}
        self._in_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form" and not self._in_form:
            self._in_form = True
            self.action = a.get("action")
            self.method = (a.get("method") or "post").lower()
        elif tag == "input" and self._in_form:
            name = a.get("name")
            if name:
                self.inputs[name] = a.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_form = False


class LtiFormParser(HTMLParser):
    """Find a Canvas LTI launch <form id="tool_form_*"> and its hidden inputs."""

    def __init__(self) -> None:
        super().__init__()
        self.action: str | None = None
        self.inputs: dict[str, str] = {}
        self._in_target_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form" and (a.get("id") or "").startswith("tool_form_"):
            self._in_target_form = True
            self.action = a.get("action")
        elif tag == "input" and self._in_target_form:
            name = a.get("name")
            if name:
                self.inputs[name] = a.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_target_form = False


def parse_form(html: str) -> FormParser:
    p = FormParser()
    p.feed(html)
    return p


def parse_lti_form(html: str) -> LtiFormParser:
    p = LtiFormParser()
    p.feed(html)
    return p
