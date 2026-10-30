#!/usr/bin/env python3
"""canvas review: request feedback on a piece of evidence (always confirmed).

Reviews are never sent automatically. The model calls this to:
  1. list mode (default): show an evidence's latest version id and the users
     it is shared with (the only valid recipients), so the user can choose.
  2. send mode (--send): actually POST the feedback request, only after the
     user has confirmed recipient + message in chat.

Usage:
  python review.py --evidence <evidence_id>                 # list recipients
  python review.py --evidence <evidence_id> --send \
      --recipient <user_id> --message "Graag feedback op ..."
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from canvas import connect, eprint  # noqa: E402
from lib import PortflowError  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Request feedback on Portflow evidence.")
    ap.add_argument("--evidence", type=int, required=True, help="Evidence id.")
    ap.add_argument("--send", action="store_true", help="Actually send the request.")
    ap.add_argument("--recipient", type=int, action="append", default=[],
                    help="Recipient user id (repeatable). Must be a shared user.")
    ap.add_argument("--message", default="", help="Feedback request text (HTML ok).")
    ap.add_argument("--goal", type=int, action="append", default=[],
                    help="Goal id(s) the feedback should address (optional).")
    args = ap.parse_args()

    pf, me, pid = connect()
    ev = pf.evidence(pid, args.evidence)
    version = ev.get("latest_version") or {}
    version_id = version.get("id")
    shared = pf.shared_users(pid, args.evidence)

    if not args.send:
        print(json.dumps({
            "evidence": {"id": ev["id"], "name": ev.get("name"),
                         "latest_version_id": version_id,
                         "feedback_status": ev.get("feedback_status")},
            "shared_with": [{"id": u["id"], "name": u.get("name")} for u in shared],
            "note": "Kies een recipient uit shared_with en bevestig de tekst, "
                    "roep dan opnieuw aan met --send.",
        }, indent=2, ensure_ascii=False))
        return

    if not version_id:
        eprint("✗ Geen versie-id op deze evidence; kan geen review aanvragen.")
        sys.exit(2)
    if not args.recipient:
        eprint("✗ --recipient vereist bij --send.")
        sys.exit(2)
    valid = {u["id"] for u in shared}
    bad = [r for r in args.recipient if r not in valid]
    if bad:
        eprint(f"✗ Recipient(s) {bad} staan niet in shared_with "
               f"({sorted(valid)}). Deel de evidence eerst, of kies een geldige.")
        sys.exit(2)
    if not args.message.strip():
        eprint("✗ --message mag niet leeg zijn.")
        sys.exit(2)

    try:
        pf.request_feedback(
            pid, version_id, args.recipient, args.message, goal_ids=args.goal,
        )
    except PortflowError as e:
        eprint(f"✗ {e}")
        sys.exit(1)
    names = ", ".join(u["name"] for u in shared if u["id"] in set(args.recipient))
    print(f"✓ Review aangevraagd bij {names} op versie {version_id} "
          f"van '{ev.get('name')}'.")


if __name__ == "__main__":
    main()
