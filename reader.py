#!/usr/bin/env python3
"""canvas reader - view or update a collection's reader (intro) text.

The "reader" is the HTML description shown at the top of a Portflow collection
(e.g. the Roosterke intro). Updating it REPLACES the whole field, so this is a
confirmed step: default shows the current reader, --set writes a new one.

Source of the new text (pick one), all treated as the full reader body:
  --html "<p>...</p>"     inline HTML (passed through as-is, @refs still expand)
  --from <file.html|.md>  a file; .md/.txt is wrapped into simple <p> blocks

@file references
----------------
Anywhere in the body you can reference a piece of evidence with
  @Bestandsnaam.pdf
and the skill expands it into the proper Portflow mention span that renders as a
clickable link to that evidence. This lets the reader be a chronological story
that links to the documents it talks about. Reference by the evidence/file name
(the .pdf is optional, matching is case-insensitive). Wrap a name that contains
spaces in @[...] to be explicit, e.g. @[De AI-assistent.pdf]. An @name that does
not match any evidence in the collection is reported and left as plain text, so
a typo never silently drops a link.

Usage:
  python reader.py --project <dir>                      # show current reader
  python reader.py --project <dir> --mentionable        # list @-referable names
  python reader.py --project <dir> --set --from intro.md
  python reader.py --collection "Roosterke.nl" --set --html "<p>@[X.pdf] ...</p>"
"""

from __future__ import annotations

import argparse
import html as htmllib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from canvas import connect, eprint, match_collection  # noqa: E402
from lib import PortflowError  # noqa: E402

# Explicit bracket form @[Name With Spaces.pdf] is matched first and is always
# safe. The bare @... form is resolved greedily against the known evidence names
# (see expand_mentions), because most names contain spaces.
_BRACKET_RE = re.compile(r"@\[([^\]]+)\]")


def _to_html(text: str, is_markdownish: bool) -> str:
    if not is_markdownish:
        return text
    # Minimal, safe conversion: blank-line-separated paragraphs, escaped.
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return "".join(f"<p>{htmllib.escape(p)}</p>" for p in paras)


def _norm(name: str) -> str:
    """Normalise an evidence/file name for tolerant matching."""
    n = name.strip().lower()
    if n.endswith(".pdf"):
        n = n[:-4]
    return n


def _mention_span(ev: dict, pid: int) -> str:
    """The Portflow rich-text span that renders as a link to an evidence."""
    label = htmllib.escape(ev["name"], quote=True)
    return (
        f'<span data-type="mention" data-id="{ev["id"]}" '
        f'data-label="{label}" data-mention-type="evidence" '
        f'data-portfolio-id="{pid}">@{htmllib.escape(ev["name"])}</span>'
    )


def expand_mentions(body: str, evidence: list[dict], pid: int) -> tuple[str, list[dict], list[str]]:
    """Replace @file references in `body` with Portflow mention spans.

    Two forms are supported:
      @[Exact Name.pdf]  explicit, always safe (recommended for names with spaces)
      @Name...           bare; resolved greedily against the known evidence names,
                         longest match first, since most names contain spaces

    Matching is by evidence name: exact, then case-insensitive, then ignoring a
    .pdf suffix. Returns (expanded_html, resolved_mentions, unresolved_names);
    an unresolved @ is left as literal text and reported to the caller.
    """
    by_exact = {e["name"]: e for e in evidence if e.get("name")}
    by_norm = {_norm(e["name"]): e for e in evidence if e.get("name")}
    resolved: dict[int, dict] = {}
    unresolved: list[str] = []

    def lookup(name: str) -> dict | None:
        return by_exact.get(name) or by_norm.get(_norm(name))

    # 1) Bracket form first (explicit, unambiguous).
    def repl_bracket(m: re.Match) -> str:
        name = m.group(1).strip()
        ev = lookup(name)
        if not ev:
            unresolved.append(name)
            return m.group(0)
        resolved[ev["id"]] = ev
        return _mention_span(ev, pid)

    body = _BRACKET_RE.sub(repl_bracket, body)

    # 2) Bare @... form: at each '@' not already inside a span, try to match the
    # longest evidence name that the text starts with (greedy, so multi-word
    # names resolve without brackets). Sort names longest-first to prefer the
    # most specific match.
    names_sorted = sorted((e["name"] for e in evidence if e.get("name")),
                          key=len, reverse=True)
    out = []
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch != "@":
            out.append(ch)
            i += 1
            continue
        rest = body[i + 1:]
        # Skip @ that belongs to an already-expanded span (preceded by '>').
        if out and out[-1] == ">":
            out.append(ch)
            i += 1
            continue
        matched = None
        for name in names_sorted:
            cand = rest[:len(name)]
            if cand == name or _norm(cand) == _norm(name):
                matched = next(e for e in evidence if e["name"] == name)
                break
        if matched:
            resolved[matched["id"]] = matched
            out.append(_mention_span(matched, pid))
            i += 1 + len(matched["name"])
        else:
            # Record the token after @ (up to whitespace) as unresolved, once.
            tok = rest.split()[0] if rest.split() else ""
            if tok and tok not in [u for u in unresolved]:
                unresolved.append(tok)
            out.append(ch)
            i += 1
    expanded = "".join(out)
    return expanded, list(resolved.values()), unresolved


def _collection_evidence(pf, pid: int, cid: int) -> list[dict]:
    """Evidence in a collection as [{id, name}], unwrapping the list shape."""
    out = []
    for item in pf.collection_evidence(pid, cid):
        ev = item.get("evidence", item)
        if ev.get("id") and ev.get("name"):
            out.append({"id": ev["id"], "name": ev["name"]})
    return out


def main():
    ap = argparse.ArgumentParser(description="View/update a collection reader.")
    ap.add_argument("--project", type=Path, default=Path.cwd())
    ap.add_argument("--collection", help="Override the collection name.")
    ap.add_argument("--set", action="store_true", help="Write a new reader.")
    ap.add_argument("--html", help="Inline HTML body for the reader.")
    ap.add_argument("--from", dest="src", type=Path, help="File with the reader body.")
    ap.add_argument("--mentionable", action="store_true",
                    help="List the evidence names you can @-reference, then exit.")
    ap.add_argument("--allow-unresolved", action="store_true",
                    help="Write even if some @refs did not match (they stay plain text).")
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

    evidence = _collection_evidence(pf, pid, col["id"])

    if args.mentionable:
        print(f"# @-refereerbare bewijsstukken in '{col['name']}' "
              f"({len(evidence)}):\n")
        for e in sorted(evidence, key=lambda x: x["name"].lower()):
            print(f"  @[{e['name']}]   (id {e['id']})")
        return

    if not args.set:
        cur = pf.collection(pid, col["id"])
        print(f"# Reader van '{col['name']}' (collection {col['id']}):\n")
        print(cur.get("description") or "(leeg)")
        print("\n(gebruik --set --from <bestand> of --html \"…\"; "
              "@Bestandsnaam.pdf wordt een link. --mentionable toont de namen.)",
              file=sys.stderr)
        return

    # Source of the reader body: --html, --from, or an auto-detected reader.txt
    # (or reader.md) in the project dir. The reader file uses plain prose with
    # @[Bestandsnaam.pdf] references, which this tool turns into links.
    src = args.src
    if not args.html and not src:
        for name in ("reader.txt", "reader.md"):
            cand = args.project / name
            if cand.is_file():
                src = cand
                eprint(f"· reader-bron automatisch gevonden: {cand.name}")
                break

    if args.html:
        body = args.html
    elif src:
        if not src.is_file():
            eprint(f"✗ Bestand niet gevonden: {src}")
            sys.exit(2)
        raw = src.read_text()
        body = _to_html(raw, src.suffix.lower() in (".md", ".txt"))
    else:
        eprint("✗ Geen reader-bron. Maak een reader.txt in de projectmap, of geef "
               "--from <bestand> of --html \"…\".")
        sys.exit(2)

    # Expand @file references into Portflow mention spans.
    body, mentions, unresolved = expand_mentions(body, evidence, pid)
    if mentions:
        eprint(f"· {len(mentions)} @-referentie(s) omgezet naar links.")
        # Validate them server-side the way the UI does before saving.
        try:
            pf.resolve_mentions(pid, [{"type": "evidence", "id": m["id"]}
                                      for m in mentions])
        except PortflowError as e:
            eprint(f"  (mention-validatie overgeslagen: {e})")
    if unresolved:
        eprint("⚠ Deze @-verwijzingen matchen geen bewijsstuk en blijven platte "
               "tekst:")
        for n in unresolved:
            eprint(f"    @{n}")
        eprint("  Controleer de naam (gebruik --mentionable voor de juiste namen).")
        if not args.allow_unresolved:
            eprint("✗ Niet opgeslagen. Corrigeer de namen of gebruik "
                   "--allow-unresolved om toch te schrijven.")
            sys.exit(4)

    try:
        pf.update_collection_description(pid, col["id"], body)
    except PortflowError as e:
        eprint(f"✗ {e}")
        sys.exit(1)
    print(f"✓ Reader van '{col['name']}' bijgewerkt ({len(body)} tekens HTML, "
          f"{len(mentions)} link(s)).")


if __name__ == "__main__":
    main()
