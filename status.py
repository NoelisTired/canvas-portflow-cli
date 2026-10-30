#!/usr/bin/env python3
"""canvas status - read-only overview of the portfolio (nothing is written).

One command that answers three questions without touching anything:

  1. Sync: per collection, how many files would ADD / VERSION / SKIP if you
     ran canvas now (reuses canvas.py's plan logic, so it matches exactly).
  2. Reviews: which evidence has no feedback requested yet, and who the
     portfolio is shared with (the pool of valid review recipients).
  3. Goals: evidence that is not linked to any learning outcome, with a
     suggested goal from the goal-map / a name match (a suggestion only;
     linking still happens through canvas.py --apply).

Usage:
  python status.py                       # all collections for the project (cwd)
  python status.py --project <dir>
  python status.py --collection "<collection naam>"
  python status.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from canvas import (  # noqa: E402
    MANIFEST_PATH,
    _load_manifest,
    _tokens,
    connect,
    detect_source,
    eprint,
    load_map,
    match_collection,
    match_goal,
    plan,
)
import canvas as _canvas  # noqa: E402
from lib import PortflowError  # noqa: E402


def _goal_label(goal: dict) -> str:
    return (goal.get("nickname") or goal.get("name") or "?").strip()


def suggest_goal(ev_name: str, folder: str | None, goal_wraps: list, gmap: dict):
    """Best-effort goal suggestion for an unlinked evidence (read-only).

    Order: the goal-map entry for the file's folder, else a token overlap
    between the evidence name and a goal's name/nickname. Returns the goal
    dict or None. This never links anything; it only proposes.
    """
    target = gmap.get("folders", {}).get(folder) if folder else None
    if target:
        g = match_goal(goal_wraps, target)
        if g:
            return g
    etoks = _tokens(ev_name)
    if not etoks:
        return None
    best, best_overlap = None, 0
    for cg in goal_wraps:
        g = cg.get("goal", cg)
        gtoks = _tokens(f"{g.get('name','')} {g.get('nickname','')}")
        overlap = len(etoks & gtoks)
        if overlap > best_overlap:
            best, best_overlap = g, overlap
    return best


def _evidence_is_unlinked(ev: dict) -> bool:
    """True when the evidence has no goals attached.

    Portflow returns goals under a few shapes depending on the endpoint; treat
    any non-empty goal list as linked.
    """
    for key in ("goals", "goal_ids", "collection_goals"):
        val = ev.get(key)
        if val:
            return False
    return True


def _feedback_known(ev: dict) -> bool:
    """Whether the list response exposes any feedback signal for this evidence.

    The collection-evidence list does not include a feedback status, so we only
    report "no review" when a feedback field is actually present and empty;
    otherwise the status is unknown from the list (don't assert a missing review).
    """
    return any(k in ev for k in
               ("feedback_status", "feedback_requested", "has_feedback"))


def _has_feedback(ev: dict) -> bool:
    status = (ev.get("feedback_status") or "").lower()
    if status and status not in ("none", "not_requested", "no_feedback"):
        return True
    return bool(ev.get("feedback_requested") or ev.get("has_feedback"))


def collection_report(pf, pid: int, col: dict, source: Path | None, gmap: dict) -> dict:
    goal_wraps = pf.collection_goals(pid, col["id"])
    evidence = [item.get("evidence", item)
                for item in pf.collection_evidence(pid, col["id"])]

    # Map evidence name -> source folder (for goal suggestions), if we have a
    # source dir to look at.
    folder_of: dict[str, str] = {}
    sync = None
    if source and source.is_dir():
        actions = plan(pf, pid, col, source, gmap, force_version=False)
        sync = {"ADD": 0, "VERSION": 0, "SKIP": 0}
        for a in actions:
            sync[a["kind"]] += 1
            folder_of[a["file"].name] = a["folder"]

    unlinked, no_review = [], []
    feedback_known = False
    for ev in evidence:
        name = ev.get("name") or f"#{ev.get('id')}"
        if _evidence_is_unlinked(ev):
            g = suggest_goal(name, folder_of.get(name), goal_wraps, gmap)
            unlinked.append({
                "evidence_id": ev.get("id"), "name": name,
                "suggested_goal": _goal_label(g) if g else None,
                "suggested_goal_id": g.get("id") if g else None,
            })
        if _feedback_known(ev):
            feedback_known = True
            if not _has_feedback(ev):
                no_review.append({"evidence_id": ev.get("id"), "name": name})

    return {
        "collection": {"id": col["id"], "name": col["name"],
                       "evidence_count": len(evidence)},
        "sync": sync,
        "unlinked": unlinked,
        "no_review": no_review,
        "feedback_known": feedback_known,
    }


def print_report(rep: dict, shares: list):
    col = rep["collection"]
    print(f"\n=== {col['name']} (id {col['id']}, {col['evidence_count']} bewijsstukken) ===")
    s = rep["sync"]
    if s is None:
        print("  sync: (geen bronmap gevonden voor deze collection)")
    elif s["ADD"] == 0 and s["VERSION"] == 0:
        print(f"  sync: in sync ({s['SKIP']} overslaan) OK")
    else:
        print(f"  sync: {s['ADD']} toevoegen, {s['VERSION']} nieuwe versie, "
              f"{s['SKIP']} overslaan  (draai canvas.py --apply)")

    if rep["unlinked"]:
        print(f"  zonder leerdoel ({len(rep['unlinked'])}):")
        for u in rep["unlinked"]:
            sug = f"  -> suggestie: {u['suggested_goal']}" if u["suggested_goal"] else \
                  "  -> geen suggestie"
            print(f"    - {u['name']}{sug}")
    else:
        print("  leerdoelen: alles gekoppeld OK")

    if not rep.get("feedback_known"):
        print("  reviews: status niet zichtbaar in de lijst "
              "(gebruik review.py --evidence <id> per bewijsstuk)")
    elif rep["no_review"]:
        print(f"  zonder review-aanvraag ({len(rep['no_review'])}):")
        for n in rep["no_review"][:12]:
            print(f"    - {n['name']}  (review.py --evidence {n['evidence_id']})")
        if len(rep["no_review"]) > 12:
            print(f"    ... en nog {len(rep['no_review']) - 12}")
    else:
        print("  reviews: alle bewijsstukken hebben een feedback-status OK")


def main():
    ap = argparse.ArgumentParser(description="Read-only portfolio status report.")
    ap.add_argument("--project", type=Path, default=Path.cwd(),
                    help="Project dir (default: cwd), used to find the source "
                         "and auto-detect the collection.")
    ap.add_argument("--source", type=Path, help="Override the evidence root.")
    ap.add_argument("--collection",
                    help="Limit to one collection by name (default: the "
                         "project's matched collection, else all).")
    ap.add_argument("--all", action="store_true",
                    help="Report every collection in the portfolio.")
    ap.add_argument("--map", type=Path, help="goal-map.json (default: skill's).")
    ap.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    args = ap.parse_args()

    _canvas.MANIFEST = _load_manifest()

    try:
        pf, me, pid = connect()
    except Exception as e:  # noqa: BLE001 - surface any connect failure cleanly
        eprint(f"X {e}")
        sys.exit(2)

    gmap = load_map(args.map)
    if args.collection:
        gmap = {**gmap, "collection": args.collection}
    cols = pf.collections(pid)

    # Which collections to report on.
    if args.all:
        targets = [(c, None) for c in cols]
    elif args.collection:
        col = next((c for c in cols if c["name"] == args.collection), None)
        if not col:
            eprint(f"X Collection niet gevonden: {args.collection}")
            sys.exit(3)
        targets = [(col, _source_for(args, args.project))]
    else:
        col, candidates = match_collection(cols, args.project, gmap)
        if col is None:
            print(json.dumps({"status": "ambiguous_collection",
                              "candidates": [{"id": c["id"], "name": c["name"]}
                                             for c in candidates]},
                             indent=2, ensure_ascii=False))
            sys.exit(3)
        targets = [(col, _source_for(args, args.project))]

    shares = []
    try:
        shares = pf.portfolio_shares(pid)
    except PortflowError:
        pass

    reports = []
    for col, source in targets:
        try:
            reports.append(collection_report(pf, pid, col, source, gmap))
        except PortflowError as e:
            eprint(f"X {col['name']}: {e}")

    if args.json:
        print(json.dumps({
            "user": {"id": me["id"], "name": me.get("name")},
            "portfolio_id": pid,
            "shared_with": [{"id": (u.get("invitee") or {}).get("id"),
                             "name": _share_name(u)} for u in shares],
            "collections": reports,
        }, indent=2, ensure_ascii=False))
        return

    print(f"\nPortfolio van {me.get('name')} (id {pid})")
    if shares:
        names = [_share_name(u) for u in shares]
        print(f"Gedeeld met: {', '.join(names)}")
    else:
        print("Gedeeld met: niemand (reviews vragen kan pas na delen)")
    for rep in reports:
        print_report(rep, shares)
    print("\n(read-only overzicht - er is niets gewijzigd)")


def _source_for(args, project: Path) -> Path:
    return args.source or detect_source(project)


def _share_name(share: dict) -> str:
    """Display name of a portfolio share (the invitee)."""
    inv = share.get("invitee") or share.get("user") or {}
    return inv.get("name") or share.get("name") or "?"


if __name__ == "__main__":
    main()
