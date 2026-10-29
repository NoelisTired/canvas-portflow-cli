#!/usr/bin/env python3
"""canvas share — give someone access to the portfolio (always confirmed).

Sharing is outward-facing (another person gets access), so it is never
automatic. Default (search) mode just finds candidate users; --send performs
the share, and only after the user approved who + until when in chat.

Usage:
  python share.py --search "rene"                 # find user ids by name
  python share.py --current                       # who it's shared with now
  python share.py --send --user <id> [--user <id>] \
      [--until 2027-08-31] [--note "..."] [--feedback-restricted]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from canvas import connect, eprint  # noqa: E402
from lib import PortflowError  # noqa: E402


def _iso_end_of_day(date_str: str) -> str:
    # Portflow stores expiry as end-of-day UTC-ish; mirror the UI's shape.
    return f"{date_str}T21:59:59.999Z"


def main():
    ap = argparse.ArgumentParser(description="Share the Portflow portfolio.")
    ap.add_argument("--search", help="Find users by name.")
    ap.add_argument("--current", action="store_true", help="List current shares.")
    ap.add_argument("--send", action="store_true", help="Actually share.")
    ap.add_argument("--user", type=str, action="append", default=[],
                    help="User id to share with (repeatable).")
    ap.add_argument("--until", default="2027-08-31",
                    help="Expiry date YYYY-MM-DD (default 2027-08-31).")
    ap.add_argument("--note", default="", help="Optional note.")
    ap.add_argument("--feedback-restricted", action="store_true",
                    help="Restrict the shared user to feedback only.")
    args = ap.parse_args()

    pf, me, pid = connect()

    if args.search:
        users = pf.search_users(args.search)
        print(json.dumps(
            [{"id": u["id"], "name": u.get("name"), "role": u.get("current_role")}
             for u in users], indent=2, ensure_ascii=False))
        return

    if args.current or not args.send:
        shares = pf.portfolio_shares(pid)
        print(json.dumps(
            [{"id": s.get("id"), "user": (s.get("shared_to") or {}).get("name"),
              "expires": s.get("expiration_date")} for s in shares],
            indent=2, ensure_ascii=False))
        if not args.send:
            print("\n(voeg --send --user <id> toe, na bevestiging, om te delen)",
                  file=sys.stderr)
        return

    if not args.user:
        eprint("✗ --user vereist bij --send.")
        sys.exit(2)
    try:
        pf.share_portfolio(
            pid, args.user, _iso_end_of_day(args.until),
            feedback_restricted=args.feedback_restricted, note=args.note,
        )
    except PortflowError as e:
        eprint(f"✗ {e}")
        sys.exit(1)
    print(f"✓ Portfolio gedeeld met user(s) {', '.join(args.user)} "
          f"t/m {args.until}.")


if __name__ == "__main__":
    main()
