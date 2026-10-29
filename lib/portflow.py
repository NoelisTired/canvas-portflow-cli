"""Portflow (portfolio.drieam.app) API client.

Talks to Portflow's private `/api/v1` with the cookie session + X-CSRF-Token
that Lti13Launcher installed. Every endpoint and request/response shape here
was reverse-engineered from the live UI's own XHRs, so treat it as
best-effort: Drieam can change it without notice. Methods raise PortflowError
with the offending status + body so a break is loud, never silent.

Nothing here invents data. Upload/create/link/review all take explicit inputs
from the caller; dedupe and goal-mapping decisions live in the orchestrator.
"""

from __future__ import annotations

import base64
import hashlib
import os
from typing import Any

import requests

API = "https://portfolio.drieam.app/api/v1"


class PortflowError(RuntimeError):
    pass


class PortflowClient:
    def __init__(self, session: requests.Session) -> None:
        self._s = session

    # ---- low-level -------------------------------------------------------

    def _req(self, method: str, path: str, **kw) -> requests.Response:
        url = path if path.startswith("http") else f"{API}{path}"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://portfolio.drieam.app",
            "Referer": "https://portfolio.drieam.app/",
        }
        headers.update(kw.pop("headers", {}))
        r = self._s.request(method, url, headers=headers, **kw)
        if r.status_code >= 400:
            raise PortflowError(
                f"{method} {path} -> {r.status_code}\n{r.text[:500]}"
            )
        return r

    @staticmethod
    def _json(r: requests.Response) -> Any:
        if not r.content:
            return None
        return r.json()

    def _paged(self, path: str, per_page: int = 100) -> list[dict]:
        """Collect all pages of a `?page=N` list endpoint."""
        out: list[dict] = []
        page = 1
        sep = "&" if "?" in path else "?"
        while True:
            chunk = self._json(
                self._req("GET", f"{path}{sep}page={page}&per_page={per_page}")
            )
            if not chunk:
                break
            out.extend(chunk)
            if len(chunk) < per_page:
                break
            page += 1
        return out

    # ---- identity / structure (read) ------------------------------------

    def current_user(self) -> dict:
        return self._json(self._req("GET", "/users/current"))

    def capabilities(self) -> dict:
        return self._json(self._req("GET", "/capabilities"))

    def portfolio(self, pid: int) -> dict:
        return self._json(self._req("GET", f"/portfolios/{pid}"))

    def collections(self, pid: int) -> list[dict]:
        return self._paged(f"/portfolios/{pid}/collections")

    def collection_goals(self, pid: int, cid: int) -> list[dict]:
        """Goals attached to a collection, each wrapping the goal object."""
        return self._paged(f"/portfolios/{pid}/collections/{cid}/collection-goals")

    def collection_evidence(self, pid: int, cid: int) -> list[dict]:
        return self._paged(f"/portfolios/{pid}/collections/{cid}/evidence")

    def evidence(self, pid: int, eid: int) -> dict:
        return self._json(self._req("GET", f"/portfolios/{pid}/evidence/{eid}"))

    def evidence_versions(self, pid: int, eid: int) -> list[dict]:
        return self._json(
            self._req("GET", f"/portfolios/{pid}/evidence/{eid}/versions?page=1")
        ) or []

    def shared_users(self, pid: int, eid: int) -> list[dict]:
        """People an evidence is shared with — the only valid review recipients."""
        return self._paged(f"/portfolios/{pid}/evidence/{eid}/shared-users")

    def portfolio_shares(self, pid: int) -> list[dict]:
        """People the whole portfolio is currently shared with."""
        return self._paged(f"/portfolios/{pid}/shares")

    def search_users(self, query: str, limit_pages: int = 3) -> list[dict]:
        """Search the Portflow directory by name (for sharing/recipients)."""
        from urllib.parse import quote
        out: list[dict] = []
        for page in range(1, limit_pages + 1):
            chunk = self._json(
                self._req("GET", f"/users?page={page}&search={quote(query)}")
            )
            if not chunk:
                break
            out.extend(chunk)
            if len(chunk) < 25:
                break
        return out

    def share_portfolio(
        self,
        pid: int,
        user_ids: list[str | int],
        expiration_date: str,
        feedback_restricted: bool = False,
        note: str = "",
        collection_ids: list[int] | None = None,
    ) -> None:
        """Grant users access to the portfolio (prerequisite for reviews).

        `expiration_date` is ISO 8601 UTC (e.g. "2027-08-31T21:59:59.999Z").
        `user_ids` are Portflow user ids as strings, from search_users().
        """
        body = {
            "shared_to_user_ids": [str(u) for u in user_ids],
            "expiration_date": expiration_date,
            "feedback_restricted": feedback_restricted,
            "note": note,
            "share_type": "portfolio",
            "collection_ids": collection_ids or [],
        }
        self._req("POST", f"/portfolios/{pid}/shares", json=body)

    # ---- upload (write) --------------------------------------------------

    def _direct_upload(self, path: str) -> dict:
        """Register a blob and PUT its bytes to the returned S3 URL.

        Returns the blob descriptor (incl. signed_id) used to attach the file.
        """
        data = open(path, "rb").read()
        md5_b64 = base64.b64encode(hashlib.md5(data).digest()).decode()
        blob = {
            "filename": os.path.basename(path),
            "content_type": _content_type(path),
            "byte_size": len(data),
            "checksum": md5_b64,
        }
        reg = self._json(self._req("POST", "/direct-uploads", json={"blob": blob}))
        du = reg["direct_upload"]
        # The presigned S3 URL carries its own auth in the query string. Use a
        # bare request (not self._s) so the session's Authorization/CSRF headers
        # don't leak to S3 — S3 rejects "two auth mechanisms" with a 400.
        put = requests.put(du["url"], data=data, headers=du["headers"])
        if put.status_code >= 400:
            raise PortflowError(
                f"S3 upload failed -> {put.status_code}\n{put.text[:300]}"
            )
        return reg

    # ---- evidence (write) ------------------------------------------------

    def create_evidence(
        self, pid: int, file_path: str, collection_id: int, name: str | None = None
    ) -> dict:
        """Upload a file and create new evidence in a collection.

        Returns the created evidence (incl. id + latest_version.id).
        """
        blob = self._direct_upload(file_path)
        fname = os.path.basename(file_path)
        body = {
            "files": [
                {
                    "signed_id": blob["signed_id"],
                    "file_name": fname,
                    "type": _content_type(file_path),
                }
            ],
            "collection_ids": [collection_id],
            "goal_ids": [],
            "activity_ids": [],
            "version_type": "file",
            "name": name or fname,
        }
        return self._json(self._req("POST", f"/portfolios/{pid}/evidence", json=body))

    def add_version(self, pid: int, eid: int, file_path: str) -> dict:
        """Add a new file version to existing evidence (no duplicate)."""
        blob = self._direct_upload(file_path)
        fname = os.path.basename(file_path)
        body = {
            "files": [
                {
                    "signed_id": blob["signed_id"],
                    "file_name": fname,
                    "type": _content_type(file_path),
                }
            ],
            "existing_files": [],
            "version_type": "file",
        }
        return self._json(
            self._req("POST", f"/portfolios/{pid}/evidence/{eid}/versions", json=body)
        )

    def link_to_goals(
        self, pid: int, eid: int, goal_ids: list[int], sub_goal_ids: list[int] | None = None
    ) -> None:
        body = {"goal_ids": goal_ids, "sub_goal_ids": sub_goal_ids or []}
        self._req(
            "POST",
            f"/portfolios/{pid}/evidence/{eid}/link-to-goals-and-sub-goals",
            json=body,
        )

    def rename_evidence(self, pid: int, eid: int, name: str) -> None:
        body = {"name": name, "date": None, "location": None, "duration": None}
        self._req("PUT", f"/portfolios/{pid}/evidence/{eid}", json=body)

    def collection(self, pid: int, cid: int) -> dict:
        return self._json(self._req("GET", f"/portfolios/{pid}/collections/{cid}"))

    def update_collection_description(self, pid: int, cid: int, html: str) -> None:
        """Update a collection's reader text (the intro shown at the top).

        PUT replaces the full field; the caller passes the complete HTML body.
        """
        self._req(
            "PUT",
            f"/portfolios/{pid}/collections/{cid}",
            json={"description": html},
        )

    # ---- reviews / feedback (write — always gated by the orchestrator) ---

    def request_feedback(
        self,
        pid: int,
        version_id: int,
        recipient_ids: list[int],
        text: str,
        goal_ids: list[int] | None = None,
        sub_goal_ids: list[int] | None = None,
    ) -> None:
        """Ask one or more shared users to review a specific evidence version.

        `version_id` is the evidence's latest_version.id; `recipient_ids` must
        be users the evidence is shared with (see shared_users()).
        """
        body = {
            "text": text,
            "created_by_role": "student",
            "goal_ids": goal_ids or [],
            "sub_goal_ids": sub_goal_ids or [],
            "feedback_recipient_ids": recipient_ids,
            "attachments": [],
        }
        self._req(
            "POST",
            f"/portfolios/{pid}/evidence-versions/{version_id}/feedback-requests",
            json=body,
        )


def _content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".zip": "application/zip",
        ".mp4": "video/mp4",
    }.get(ext, "application/octet-stream")
