#!/usr/bin/env python3
"""canvas — place Roosterke evidence into the Portflow portfolio.

Flow:
  1. Log into Canvas via Fontys ADFS (vendored from fontys-canvas-bypass).
  2. LTI 1.3 launch into Portflow -> cookie session + CSRF.
  3. Read portfolio structure + existing evidence.
  4. Plan per source folder (folder name -> goal): for each file decide
     ADD (new), VERSION (changed), or SKIP (already present, unchanged).
  5. Print the plan. With --apply, execute it. Reviews are NEVER automatic:
     --review prints what could be requested and still needs confirmation
     via --review-confirm; the model asks the user first.

Dedupe key: evidence name == file name. "Changed" is decided by byte size
(Portflow does not expose the stored file's checksum on list), so a same-name
file with a different size becomes a new VERSION; identical size is SKIPped.
Use --force-version to always add a version for same-name files.

Usage:
  python canvas.py --source <dir> [--map goal-map.json] [--apply]
  python canvas.py --source <dir> --review [--review-confirm \
      --recipient <id> --message "..."]
  python canvas.py --whoami        # login + launch + print structure only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib import (  # noqa: E402
    PlaywrightLaunchError,
    PortflowClient,
    PortflowError,
    launch_with_playwright,
)
from lib.config import (  # noqa: E402
    PORTFLOW_LAUNCH_URL,
    ConfigError,
    load_credentials,
)

ALLOWED_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
               ".doc", ".docx", ".txt", ".md", ".mp4", ".zip"}

# Local content manifest: {collection_id: {file_name: content_hash}}. Lets the
# skill tell a genuine content change (new VERSION) from an identical rebuild.
MANIFEST_PATH = Path(__file__).resolve().parent / ".canvas-manifest.json"


def _load_manifest() -> dict:
    try:
        raw = json.loads(MANIFEST_PATH.read_text())
        # JSON keys are strings; normalise collection ids to int.
        return {int(k): v for k, v in raw.items()}
    except Exception:
        return {}


def _save_manifest(m: dict) -> None:
    try:
        MANIFEST_PATH.write_text(json.dumps(m, indent=2, ensure_ascii=False))
    except Exception as e:
        eprint(f"  (kon manifest niet opslaan: {e})")


MANIFEST: dict = {}


def eprint(*a):
    print(*a, file=sys.stderr)


# ---- session -------------------------------------------------------------

def connect() -> tuple[PortflowClient, dict, int]:
    user, pw = load_credentials()
    eprint("· launching Portflow via browser (ADFS login + LTI 1.3)…")
    s, launch = launch_with_playwright(PORTFLOW_LAUNCH_URL, user, pw)
    pf = PortflowClient(s)
    me = pf.current_user()
    eprint(f"· signed in as {me.get('name')} (portflow id {me.get('id')})")
    pid = int(os.environ.get("PORTFLOW_PORTFOLIO_ID") or launch.get("portfolio_id") or 0)
    if not pid:
        raise ConfigError(
            "Could not determine the portfolio id from the launch. Pass "
            "PORTFLOW_PORTFOLIO_ID=<id> (visible in any Portflow URL)."
        )
    return pf, me, pid


# ---- goal mapping --------------------------------------------------------

def load_map(path: Path | None) -> dict:
    if path and path.exists():
        return json.loads(path.read_text())
    default = Path(__file__).parent / "goal-map.json"
    example = Path(__file__).parent / "goal-map.example.json"
    return json.loads((default if default.exists() else example).read_text())


def match_goal(goal_wraps: list[dict], target: str) -> dict | None:
    t = target.strip().lower()
    for cg in goal_wraps:
        g = cg.get("goal", cg)
        if (g.get("nickname") or "").strip().lower() == t:
            return g
        if (g.get("name") or "").strip().lower() == t:
            return g
    return None


# ---- project auto-detection ---------------------------------------------

def _norm(s: str) -> str:
    """Normalise a name for fuzzy matching: lowercase, strip non-alphanumerics."""
    return "".join(ch for ch in s.lower() if ch.isalnum())


# Generic words that should not drive a project<->collection match on their own.
_STOP = {"documentatie", "docs", "documentation", "portfolio", "project",
         "reader", "nl", "app", "the", "van", "de", "het"}


def _tokens(name: str) -> set[str]:
    """Meaningful lowercase word tokens of a name (split on non-alphanumerics)."""
    import re as _re
    parts = _re.split(r"[^a-z0-9]+", name.lower())
    return {p for p in parts if p and len(p) >= 4 and p not in _STOP}


def detect_source(project: Path) -> Path:
    """Pick the evidence source dir for a project.

    Prefers an `output/` subdir (the Roosterke convention); else the project
    dir itself if it already contains goal-named subfolders.
    """
    out = project / "output"
    if out.is_dir():
        return out
    return project


def match_collection(cols: list[dict], project: Path, gmap: dict) -> tuple[dict, list]:
    """Find the Portflow collection a project maps to.

    Order: explicit gmap["collection"] -> name-match on the project folder
    name -> ambiguous/none (caller asks the user). Returns (collection,
    candidates); collection is None when the caller must choose.
    """
    want = gmap.get("collection")
    if want:
        col = next((c for c in cols if c["name"] == want), None)
        if col:
            return col, []
    # Name-match: a project and a collection match when they share a
    # meaningful word token (e.g. "roosterke" in both "Roosterke-Documentatie"
    # and "Roosterke.nl"), or when one normalised name contains the other.
    ptoks = _tokens(project.name)
    pkey = _norm(project.name)
    scored = []
    for c in cols:
        ctoks = _tokens(c["name"])
        ckey = _norm(c["name"])
        shared = ptoks & ctoks
        if shared or (pkey and (pkey in ckey or ckey in pkey)):
            scored.append(c)
    if len(scored) == 1:
        return scored[0], []
    return None, scored or cols


# ---- planning ------------------------------------------------------------

def plan(pf: PortflowClient, pid: int, col: dict, source: Path, gmap: dict, force_version: bool):
    goal_wraps = pf.collection_goals(pid, col["id"])
    goal_wraps = pf.collection_goals(pid, col["id"])
    # collection_evidence wraps each item as {id, position, evidence:{...}};
    # index the inner evidence object by its file name.
    existing = {}
    for item in pf.collection_evidence(pid, col["id"]):
        ev = item.get("evidence", item)
        if ev.get("name"):
            existing[ev["name"]] = ev

    actions = []
    skipped_folders = set(gmap.get("skip_folders", []))
    for folder in sorted(p for p in source.iterdir() if p.is_dir()):
        if folder.name in skipped_folders:
            continue
        target = gmap.get("folders", {}).get(folder.name)
        goal = match_goal(goal_wraps, target) if target else None
        for f in sorted(folder.iterdir()):
            if not f.is_file() or f.suffix.lower() not in ALLOWED_EXT:
                continue
            if f.name.startswith("."):
                continue
            ev = existing.get(f.name)
            digest = content_hash(f)
            if ev is None:
                kind = "ADD"
            elif force_version:
                kind = "VERSION"
            else:
                # Content-based change detection via a local manifest: the file
                # is a new VERSION only when its content hash differs from the
                # one recorded at the last upload. First time we see an existing
                # evidence with no recorded hash, we SKIP (don't re-upload) but
                # record nothing until it is (re)uploaded.
                prev = MANIFEST.get(col["id"], {}).get(f.name)
                kind = "VERSION" if (prev and prev != digest) else "SKIP"
            actions.append({
                "kind": kind, "file": f, "folder": folder.name,
                "goal": goal, "goal_target": target,
                "evidence_id": ev["id"] if ev else None,
                "collection_id": col["id"], "hash": digest,
            })
    return actions


def content_hash(f: Path) -> str:
    """Stable content hash of a file.

    For PDFs, hash the extracted text layer (ignoring volatile metadata like the
    build timestamp reportlab embeds), so a rebuild with identical content does
    not look 'changed'. For everything else, hash the raw bytes.
    """
    import hashlib
    if f.suffix.lower() == ".pdf":
        text = _pdf_text(f)
        if text is not None:
            return "pdftext:" + hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()
    return "bytes:" + hashlib.sha256(f.read_bytes()).hexdigest()


def _pdf_text(f: Path) -> str | None:
    try:
        from pypdf import PdfReader
    except Exception:
        return None
    try:
        reader = PdfReader(str(f))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:
        return None


def print_plan(col: dict, actions: list[dict]):
    counts = {"ADD": 0, "VERSION": 0, "SKIP": 0}
    print(f"\nCollection: {col['name']} (id {col['id']})")
    for a in actions:
        counts[a["kind"]] += 1
        g = a["goal"]
        gl = (g.get("nickname") or g.get("name")) if g else \
            (f"?{a['goal_target']}?" if a["goal_target"] else "— geen goal —")
        mark = {"ADD": "＋", "VERSION": "↻", "SKIP": "·"}[a["kind"]]
        print(f"  {mark} {a['kind']:8} [{gl}]  {a['folder']}/{a['file'].name}")
    print(f"\n  {counts['ADD']} toevoegen · {counts['VERSION']} nieuwe versie · "
          f"{counts['SKIP']} overslaan")
    nogoal = [a for a in actions if a["kind"] != "SKIP" and not a["goal"]]
    if nogoal:
        print(f"  ⚠ {len(nogoal)} bestand(en) zonder gekoppeld leerdoel "
              "(upload wel, maar niet aan een goal gekoppeld).")


# ---- apply ---------------------------------------------------------------

def apply(pf: PortflowClient, pid: int, actions: list[dict]) -> list[dict]:
    done = {"ADD": 0, "VERSION": 0, "SKIP": 0, "linked": 0}
    uploaded: list[dict] = []
    coll = MANIFEST.setdefault(pid_to_col(actions), {})
    for a in actions:
        if a["kind"] == "SKIP":
            done["SKIP"] += 1
            # Record the hash of already-present evidence so future rebuilds can
            # detect a real change even if this run didn't (re)upload it.
            coll.setdefault(a["file"].name, a.get("hash"))
            continue
        path = str(a["file"])
        try:
            if a["kind"] == "ADD":
                ev = pf.create_evidence(pid, path, a["collection_id"])
                eid = ev["id"]
                print(f"  ＋ {a['file'].name} -> evidence {eid}")
                done["ADD"] += 1
            else:
                eid = a["evidence_id"]
                ev = pf.add_version(pid, eid, path)
                print(f"  ↻ {a['file'].name} -> new version on {eid}")
                done["VERSION"] += 1
            if a["goal"]:
                pf.link_to_goals(pid, eid, [a["goal"]["id"]])
                done["linked"] += 1
            coll[a["file"].name] = a.get("hash")
            uploaded.append({"evidence_id": eid, "name": a["file"].name,
                             "kind": a["kind"]})
        except PortflowError as e:
            eprint(f"  ✗ {a['file'].name}: {e}")
    _save_manifest(MANIFEST)
    print(f"\nKlaar: {done['ADD']} toegevoegd, {done['VERSION']} ge-herversied, "
          f"{done['linked']} aan goal gekoppeld, {done['SKIP']} overgeslagen.")
    return uploaded


def pid_to_col(actions: list[dict]) -> int:
    return actions[0]["collection_id"] if actions else 0


# ---- main ----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Place project evidence into Portflow.")
    ap.add_argument("--project", type=Path, default=Path.cwd(),
                    help="Project dir (default: cwd). Source + collection are "
                         "auto-detected from it.")
    ap.add_argument("--source", type=Path,
                    help="Override the evidence root (folders = goals).")
    ap.add_argument("--collection",
                    help="Override the Portflow collection name to target.")
    ap.add_argument("--map", type=Path, help="goal-map.json (default: skill's).")
    ap.add_argument("--apply", action="store_true", help="Execute the plan (else dry-run).")
    ap.add_argument("--force-version", action="store_true",
                    help="Add a new version for same-name files even if size matches.")
    ap.add_argument("--whoami", action="store_true",
                    help="Login + launch + print portfolio structure, nothing else.")
    ap.add_argument("--json", action="store_true", help="Emit the plan as JSON.")
    args = ap.parse_args()

    global MANIFEST
    MANIFEST = _load_manifest()

    try:
        pf, me, pid = connect()
    except PlaywrightLaunchError as e:
        eprint(f"✗ Launch mislukt.\n{e}")
        sys.exit(2)
    except ConfigError as e:
        eprint(f"✗ {e}")
        sys.exit(2)

    if args.whoami:
        p = pf.portfolio(pid)
        print(json.dumps({
            "user": {"id": me["id"], "name": me.get("name")},
            "portfolio_id": pid,
            "collections": [
                {"id": c["id"], "name": c["name"], "evidence_count": c.get("evidence_count")}
                for c in pf.collections(pid)
            ],
        }, indent=2, ensure_ascii=False))
        return

    gmap = load_map(args.map)
    if args.collection:
        gmap = {**gmap, "collection": args.collection}

    # Auto-detect source from the project unless overridden.
    source = args.source or detect_source(args.project)
    if not source.is_dir():
        ap.error(f"Source not a directory: {source}")

    # Auto-detect the target collection (name-match), unless gmap pins one.
    cols = pf.collections(pid)
    col, candidates = match_collection(cols, args.project, gmap)
    if col is None:
        # Ambiguous: let the caller (the model) ask the user which collection.
        print(json.dumps({
            "status": "ambiguous_collection",
            "project": args.project.name,
            "candidates": [{"id": c["id"], "name": c["name"],
                            "evidence_count": c.get("evidence_count")}
                           for c in candidates],
            "hint": "Re-run with --collection \"<name>\" to pick one.",
        }, indent=2, ensure_ascii=False))
        sys.exit(3)

    eprint(f"· project '{args.project.name}' -> collection '{col['name']}' "
           f"| source {source}")
    actions = plan(pf, pid, col, source, gmap, args.force_version)

    if args.json:
        print(json.dumps({
            "collection": {"id": col["id"], "name": col["name"]},
            "source": str(source),
            "actions": [
                {k: (str(v) if isinstance(v, Path) else
                     (v.get("id") if k == "goal" and v else v))
                 for k, v in a.items()} for a in actions
            ],
        }, indent=2, ensure_ascii=False, default=str))
        return

    print_plan(col, actions)
    if args.apply:
        print("\n— UITVOEREN —")
        uploaded = apply(pf, pid, actions)
        if uploaded:
            print("\nWil je een review aanvragen op (een van) deze? Dat is een "
                  "aparte, bevestigde stap:")
            for u in uploaded:
                print(f"   review.py --evidence {u['evidence_id']}  "
                      f"# {u['name']}")
    else:
        # Dry-run: seed the manifest for files already present (no prior hash),
        # so a later rebuild can detect genuine content changes. This records
        # local hashes only; it uploads nothing.
        coll = MANIFEST.setdefault(col["id"], {})
        seeded = 0
        for a in actions:
            if a["kind"] == "SKIP" and a["file"].name not in coll:
                coll[a["file"].name] = a.get("hash")
                seeded += 1
        if seeded:
            _save_manifest(MANIFEST)
            eprint(f"· manifest geseed met {seeded} bestaande bestand-hashes")
        print("\n(dry-run — voeg --apply toe om echt te uploaden)")


if __name__ == "__main__":
    main()
