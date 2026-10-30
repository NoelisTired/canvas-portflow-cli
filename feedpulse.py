#!/usr/bin/env python3
"""canvas feedpulse - view and (confirmed) fill FeedPulse checkpoints.

FeedPulse is the Fontys progress tool where you write short reflections on
checkpoints and set a self-rating (the traffic light). This command logs in the
same way as the portfolio sync (ADFS + LTI launch, shared browser profile) and
then talks to fhict.feedpulse.com/api/v3.

It is read-only by default. The two write actions mirror reviews/sharing in the
rest of the skill: they never fire automatically, and the content is always
YOURS - this tool never invents a reflection, it only submits text you pass in.

  python feedpulse.py --whoami                 # login + who am I + courses (raw)
  python feedpulse.py --course <id> --list     # checkpoints in a course (raw)
  python feedpulse.py --open                    # checkpoints without your text yet

  # write, only after you wrote the text and confirmed in chat:
  python feedpulse.py --checkpoint <id> --feedback-file reflectie.txt --send
  python feedpulse.py --checkpoint <id> --rating <n> --send

Discovery note: course/tool ids differ per semester. The defaults live in
lib/config and can be overridden with CANVAS_FEEDPULSE_COURSE / _TOOL or a
`feedpulse` entry in secrets.json. --whoami prints the courses so you can pick.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.config import (  # noqa: E402
    ConfigError,
    feedpulse_launch_url,
    load_credentials,
)
from lib.lti.feedpulse_pw import launch_feedpulse  # noqa: E402
from lib.lti.launcher_pw import PlaywrightLaunchError  # noqa: E402

API = "https://fhict.feedpulse.com/api/v3"


def eprint(*a):
    print(*a, file=sys.stderr)


class FeedPulseError(RuntimeError):
    pass


class FeedPulseClient:
    """Thin client over FeedPulse /api/v3 (cookie + X-CSRF-Token session)."""

    def __init__(self, session):
        self._s = session

    def _json(self, resp):
        if resp.status_code >= 400:
            raise FeedPulseError(f"{resp.request.method} {resp.url} -> "
                                 f"{resp.status_code}\n{resp.text[:400]}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def current_user(self) -> dict:
        return self._json(self._s.get(f"{API}/users/current"))

    def course(self, course_id: str) -> dict:
        return self._json(self._s.get(f"{API}/courses/{course_id}"))

    def students(self, course_id: str) -> list:
        return self._json(self._s.get(f"{API}/courses/{course_id}/students")) or []

    def user_in_course(self, course_id: str, user_id) -> dict:
        """A user's record in a course - includes their checkpoints/feedpulses."""
        return self._json(
            self._s.get(f"{API}/courses/{course_id}/users/{user_id}"))

    # ---- writes (confirmed) ---------------------------------------------

    def add_feedback(self, checkpoint_id: int, content: str) -> dict:
        """Post a feedback entry (your reflection) on a checkpoint.

        Body shape observed from the FeedPulse UI:
          {"feedback": {"checkpoint_id", "feedback_type": "user", "content"}}
        """
        body = {"feedback": {"checkpoint_id": checkpoint_id,
                             "feedback_type": "user", "content": content}}
        return self._json(
            self._s.post(f"{API}/checkpoints/{checkpoint_id}/feedback", json=body))

    def set_self_rating(self, checkpoint_id: int, rating: int) -> dict:
        """Set the self-rating (traffic light) on a checkpoint."""
        return self._json(
            self._s.put(f"{API}/checkpoints/{checkpoint_id}",
                        json={"self_rating": rating}))


def connect() -> tuple[FeedPulseClient, dict]:
    user, pw = load_credentials()
    eprint("· launching FeedPulse via browser (ADFS login + LTI launch)…")
    s, info = launch_feedpulse(feedpulse_launch_url(), user, pw)
    fp = FeedPulseClient(s)
    me = fp.current_user()
    eprint(f"· signed in as {(me or {}).get('name')} "
           f"(feedpulse id {(me or {}).get('id')})")
    return fp, me


def _read_text(args) -> str | None:
    if args.feedback_file:
        return Path(args.feedback_file).read_text(encoding="utf-8").strip()
    if args.feedback is not None:
        return args.feedback.strip()
    return None


def main():
    ap = argparse.ArgumentParser(description="View / fill FeedPulse checkpoints.")
    ap.add_argument("--whoami", action="store_true",
                    help="Login, print the current user and courses (raw JSON).")
    ap.add_argument("--course", help="Course id (default: configured FeedPulse course).")
    ap.add_argument("--list", action="store_true",
                    help="Dump your checkpoints in the course (raw JSON).")
    ap.add_argument("--open", action="store_true",
                    help="Show checkpoints that have no reflection from you yet.")
    ap.add_argument("--checkpoint", type=int, help="Checkpoint id to write to.")
    ap.add_argument("--feedback", help="Reflection text to submit (yours).")
    ap.add_argument("--feedback-file", help="File with the reflection text.")
    ap.add_argument("--rating", type=int, help="Self-rating value to set.")
    ap.add_argument("--send", action="store_true",
                    help="Actually perform the write (else preview only).")
    ap.add_argument("--json", action="store_true", help="Raw JSON where applicable.")
    args = ap.parse_args()

    try:
        fp, me = connect()
    except (PlaywrightLaunchError, ConfigError) as e:
        eprint(f"X {e}")
        sys.exit(2)

    course_id = args.course
    uid = (me or {}).get("id")

    if args.whoami:
        courses = []
        # current_user usually embeds the user's courses/enrolments; surface
        # whatever is there so the real shape is visible on first run.
        for key in ("courses", "enrollments", "enrolments"):
            if isinstance((me or {}).get(key), list):
                courses = me[key]
                break
        print(json.dumps({"user": me, "courses_guess": courses},
                         indent=2, ensure_ascii=False))
        return

    if not course_id:
        from lib.config import FEEDPULSE_DEFAULT_COURSE
        course_id = FEEDPULSE_DEFAULT_COURSE

    if args.list or args.open:
        data = fp.user_in_course(course_id, uid)
        if args.list:
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return
        # --open: find checkpoints with no user feedback yet. The exact field
        # names are confirmed on first live run; we look for the common shapes.
        checkpoints = _extract_checkpoints(data)
        openlist = [c for c in checkpoints if not _has_user_feedback(c)]
        if args.json:
            print(json.dumps(openlist, indent=2, ensure_ascii=False))
            return
        if not openlist:
            print("Geen open checkpoints gevonden (of de structuur wijkt af; "
                  "draai --list om de ruwe data te zien).")
            return
        print(f"\nOpen feedpulses ({len(openlist)}) - jij schrijft de tekst, "
              "daarna indienen met --send:")
        for c in openlist:
            cid = c.get("id")
            title = c.get("name") or c.get("title") or c.get("description") or f"#{cid}"
            print(f"  - [{cid}] {title}")
            print(f"      feedpulse.py --checkpoint {cid} "
                  f"--feedback-file <jouw-tekst.txt> --send")
        return

    # ---- write paths ----------------------------------------------------
    if args.checkpoint is None:
        ap.error("Geef --checkpoint op (met --rating en/of --feedback/-file).")

    text = _read_text(args)
    if text is None and args.rating is None:
        ap.error("Niks om te doen: geef --feedback(-file) en/of --rating.")

    # Preview first. Writing to FeedPulse is outward-facing and goes to your
    # assessor, so --send is required and the model shows this preview to you
    # for confirmation before ever passing --send.
    print("\n=== FeedPulse schrijf-actie (preview) ===")
    print(f"checkpoint : {args.checkpoint}")
    if text is not None:
        print(f"reflectie  : ({len(text)} tekens)")
        print("  " + "\n  ".join(text.splitlines()[:12]))
        if len(text.splitlines()) > 12:
            print("  …")
    if args.rating is not None:
        print(f"self_rating: {args.rating}")

    if not args.send:
        print("\n(preview - voeg --send toe om dit echt in te dienen bij FeedPulse)")
        return

    if text is not None:
        res = fp.add_feedback(args.checkpoint, text)
        print(f"\n✓ reflectie ingediend op checkpoint {args.checkpoint} "
              f"(feedback id {(res or {}).get('id')})")
    if args.rating is not None:
        fp.set_self_rating(args.checkpoint, args.rating)
        print(f"✓ self_rating {args.rating} gezet op checkpoint {args.checkpoint}")


def _extract_checkpoints(data) -> list:
    if not isinstance(data, dict):
        return []
    for key in ("checkpoints", "pulses", "feedpulses"):
        v = data.get(key)
        if isinstance(v, list):
            return v
    # sometimes nested under a group/enrolment
    out = []
    for v in data.values():
        if isinstance(v, list):
            out.extend(x for x in v if isinstance(x, dict) and "id" in x
                       and ("feedback" in x or "self_rating" in x
                            or "checkpoint_id" in x))
    return out


def _has_user_feedback(checkpoint: dict) -> bool:
    fb = checkpoint.get("feedback") or checkpoint.get("feedbacks")
    if isinstance(fb, list):
        return any((f or {}).get("feedback_type") == "user" for f in fb)
    return bool(checkpoint.get("has_user_feedback"))


if __name__ == "__main__":
    main()
