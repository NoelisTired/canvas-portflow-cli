#!/usr/bin/env python3
"""canvas reader — view or update a collection's reader (intro) text.

The "reader" is the HTML description shown at the top of a Portflow collection
(e.g. the Roosterke intro). Updating it REPLACES the whole field, so this is a
confirmed step: default shows the current reader, --set writes a new one.

Source of the new text (pick one), all treated as the full reader body:
  --html "<p>...</p>"     inline HTML
  --from <file.html|.md>  a file; .md/.txt is wrapped into simple <p> blocks
The project's portfolio.txt is an ASCII layout for PDF, NOT a web reader, so it
is not used as-is; pass real HTML or a prose file.

Usage:
  python reader.py --project <dir>                      # show current reader
  python reader.py --project <dir> --set --from intro.html
  python reader.py --collection "Roosterke.nl" --set --html "<p>…</p>"
"""

from __future__ import annotations

import argparse
import html as htmllib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from canvas import connect, eprint, match_collection  # noqa: E402
from lib import PortflowError  # noqa: E402


def _to_html(text: str, is_markdownish: bool) -> str:
    if not is_markdownish:
        return text
    # Minimal, safe conversion: blank-line-separated paragraphs, escaped.
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{htmllib.escape(p)}</p>" for p in paras)


def main():
    ap = argparse.ArgumentParser(description="View/update a collection reader.")
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--collection", help="Override the collection name.")
    ap.add_argument("--set", action="store_true", help="Write a new reader.")
    ap.add_argument("--html", help="Inline HTML body for the reader.")
    ap.add_argument("--from", dest="src", type=Path, help="File with the reader body.")
    args = ap.parse_args()

    pf, me, pid = connect()
    cols = pf.collections(pid)
    gmap = {"collection": args.collection} if args.collection else {}
    col, candidates = match_collection(cols, args.project, gmap)
    if col is None:
        eprint("✗ Kon de collection niet eenduidig bepalen. Kandidaten: "
               + ", ".join(c["name"] for c in candidates)
               + "\n  Gebruik --collection \"<naam>\".")
        sys.exit(3)

    if not args.set:
        cur = pf.collection(pid, col["id"])
        print(f"# Reader van '{col['name']}' (collection {col['id']}):\n")
        print(cur.get("description") or "(leeg)")
        print("\n(gebruik --set --from <bestand> of --html \"…\" om te wijzigen)",
              file=sys.stderr)
        return

    if args.html:
        body = args.html
    elif args.src:
        if not args.src.is_file():
            eprint(f"✗ Bestand niet gevonden: {args.src}")
            sys.exit(2)
        raw = args.src.read_text()
        body = _to_html(raw, args.src.suffix.lower() in (".md", ".txt"))
    else:
        eprint("✗ Geef --html \"…\" of --from <bestand> bij --set.")
        sys.exit(2)

    try:
        pf.update_collection_description(pid, col["id"], body)
    except PortflowError as e:
        eprint(f"✗ {e}")
        sys.exit(1)
    print(f"✓ Reader van '{col['name']}' bijgewerkt ({len(body)} tekens HTML).")


if __name__ == "__main__":
    main()
