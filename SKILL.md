---
name: canvas
description: Place Roosterke portfolio evidence into Noël's Portflow portfolio (portfolio.drieam.app) on Fontys Canvas, automatically and idempotently. Logs into Canvas via Fontys ADFS SSO (pure requests, reusing fontys-canvas-bypass), performs the LTI 1.3 launch into Portflow, reads the existing portfolio structure and prior submissions, then plans per evidence folder which files to ADD, add a new VERSION of, or SKIP (already present, unchanged). Maps folder names to learning-outcome goals. Reviews/feedback requests are NEVER automatic — the skill always asks first who to request a review from. Use when Noël wants to upload/sync his Roosterke evidence to Portflow, update his portfolio, "zet mijn bewijs in portflow", "sync mijn portfolio", or request a review on a piece of evidence.
metadata:
  type: skill
---

# canvas

Turns a folder of evidence into Portflow portfolio evidence, without creating
duplicates, and lets you request reviews — but only after confirming with you.

## When to use

- "zet mijn bewijs / evidence in Portflow", "sync mijn portfolio"
- "update mijn Roosterke-bewijs in portflow"
- "vraag een review aan op <evidence>"

Not for: Canvas grades/assignments/modules (Canvas-native, not Portflow),
FeedPulse feedback (that's the separate `fhict-feedpulse` skill), or other
Instructure tenants — this is hard-wired to `fhict.instructure.com` +
`portfolio.drieam.app`.

## Credentials

Reads `~/.claude/secrets.json` key `fontys` `{ "user": "...", "pass": "..." }`
(or env `CANVAS_USER` / `CANVAS_PASS`). The skill never writes or prints them.

## How login works (Playwright, not requests)

Portflow's LTI 1.3 launch cannot be done with pure `requests`: the handshake
routes through `sso.canvaslms.com/api/lti/authorize_redirect` and uses LTI
Platform Storage (postMessage, JavaScript), and the API then authorizes with an
in-memory `Authorization: Bearer` JWT the SPA never stores in a cookie. So:

1. A headless Chromium (Playwright) logs into Canvas via Fontys ADFS and runs
   the full launch, reusing a persistent profile (`.pw-profile/`) so repeat runs
   skip the Canvas login.
2. The launcher captures the Bearer + CSRF from the SPA's own first API call and
   the portfolio id from the launch, then hands a plain `requests.Session` to
   `PortflowClient` for the fast API work (upload, create, link, review, share).

The ADFS login + launch step reuses the IdP-detection ideas from
`fontys-canvas-bypass` (`lib/sso`, `lib/http` are vendored), but the actual
launch is browser-driven here.

**MFA:** if Fontys shows a second-factor prompt, run once with
`CANVAS_HEADFUL=1` to approve it interactively; the persistent profile then
remembers the Canvas session for later headless runs.

**Setup (one-time):** the skill needs its own venv with requests + Playwright:
```bash
cd ~/.claude/skills/canvas
python3 -m venv .venv
./.venv/bin/pip install requests playwright
./.venv/bin/python -m playwright install chromium
```

## How to run it

Always dry-run first, show the plan, then apply.

The skill auto-detects both the **source** and the **target collection** from a
project directory:
- **source**: `<project>/output` if present, else the project dir itself.
- **collection**: name-matched against the user's Portflow collections (e.g.
  `Roosterke-Documentatie` -> `Roosterke.nl`). If the match is ambiguous it
  prints `status: ambiguous_collection` with candidates and exits 3 — the model
  then asks the user and re-runs with `--collection "<name>"`.

```bash
cd ~/.claude/skills/canvas

# Sanity check: login + launch + portfolio structure
./.venv/bin/python canvas.py --whoami

# Plan (dry-run) from a project dir — source + collection auto-detected
./.venv/bin/python canvas.py --project "/Users/noelpdotdev/Roosterke-Documentatie"

# Apply, ONLY after the user approved the plan
./.venv/bin/python canvas.py --project "/Users/noelpdotdev/Roosterke-Documentatie" --apply
```

Overrides: `--source <dir>` and `--collection "<name>"`. `--project` defaults to
the current working directory, so "update my docs in my portfolio" from inside a
project folder just works. After `--apply` the skill prints the uploaded
evidence ids and the `review.py` command to request feedback — never automatic.

### The plan (dedupe logic)

Per sub-folder of `--source` (folder name → goal via the map):

| Mark | Meaning |
| --- | --- |
| `＋ ADD` | No evidence with this file name exists → create new evidence. |
| `↻ VERSION` | Evidence with this name exists but the file size differs → add a new version (no duplicate). |
| `· SKIP` | Same name, same size → already up to date, left alone. |

`--force-version` turns every same-name match into a new VERSION.
Size is the only change-signal Portflow exposes on list; if it can't tell, it
skips rather than risk a duplicate.

### Goal mapping

`goal-map.json` (copy from `goal-map.example.json`) maps folder names to goal
nicknames (LO1, LO2, …) or full names, names the target `collection`
("Roosterke.nl"), and lists `skip_folders`. A file whose folder has no mapped
goal is still uploaded, but reported as "geen goal" and left unlinked.

## Reviews — always ask first

Reviews are a **separate, confirmed** step. Never send one without the user
naming the recipient and approving the message.

```bash
# 1. Show the evidence's version id + who it's shared with (valid recipients)
./.venv/bin/python review.py --evidence <evidence_id>

# 2. Only after the user confirms recipient + text:
./.venv/bin/python review.py --evidence <evidence_id> --send \
    --recipient <user_id> --message "Graag feedback op dit bewijs."
```

A review attaches to the evidence's **latest version**, and the recipient must
be someone the evidence is already shared with (`shared_users`) — the script
refuses other ids. If they aren't shared yet, share the portfolio first (below).

## Reader (collection intro) — confirmed

The "reader"/leeswijzer is the HTML `description` at the top of a collection
(`PUT /portfolios/{pid}/collections/{cid}`). Updating it replaces the whole
field, so it is confirmed. The project's `portfolio.txt` is an ASCII layout for
the PDF, NOT the web reader — do not push it verbatim; pass real HTML or a prose
file.

```bash
./.venv/bin/python reader.py --project <dir>                 # show current reader
./.venv/bin/python reader.py --project <dir> --set --from intro.html
./.venv/bin/python reader.py --collection "Roosterke.nl" --set --html "<p>…</p>"
```

## Sharing — always ask first

Giving someone access is outward-facing, so it is never automatic. It's the
prerequisite for requesting a review from them.

```bash
./.venv/bin/python share.py --search "rene"      # find user ids by name
./.venv/bin/python share.py --current            # who has access now
# only after the user confirms who + until when:
./.venv/bin/python share.py --send --user <id> --until 2027-08-31
```

## How it's built (for editing)

| Path | Purpose |
| --- | --- |
| `lib/sso/`, `lib/http/` | Vendored **unchanged** from `fontys-canvas-bypass`: ADFS login + transport. Re-sync from there if the login breaks. |
| `lib/lti/launcher_pw.py` | **Active** launcher: Playwright-driven ADFS login + LTI 1.3 launch; captures Bearer + CSRF + portfolio id, exports a requests.Session. |
| `lib/lti/launcher13.py` | Earlier pure-requests LTI 1.3 attempt, kept for reference; does not complete the launch (needs the browser). |
| `lib/portflow.py` | Portflow `/api/v1` client: read structure/evidence, upload (direct-upload → S3 → evidence), link to goals, request feedback. |
| `lib/config.py` | Credentials + Canvas/Portflow constants. |
| `canvas.py` | Orchestrator: plan + apply. |
| `review.py` | Review requests (confirmed). |
| `share.py` | Grant portfolio access / search users (confirmed). |
| `reader.py` | View/update a collection's reader (intro HTML); confirmed. |

## Fragility (flag to the user if it breaks)

- **Private API.** Everything under `portfolio.drieam.app/api/v1` was reverse-
  engineered from the UI. Drieam can change it; the client raises
  `PortflowError` with status + body so failures are loud, not silent.
- **Launch handshake.** `launcher13.py` replays Canvas/Drieam auto-submit
  forms without JavaScript. A change there is the most likely break point.
- **MFA.** Any Fontys MFA enforcement ends the headless login.
