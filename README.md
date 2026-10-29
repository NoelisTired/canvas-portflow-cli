<p align="center">
  <img src="docs/banner.png" alt="FontysCanvasCLI" width="100%">
</p>

<h1 align="center">FontysCanvasCLI</h1>

<p align="center">
  Turn a folder of evidence into Portflow portfolio evidence, idempotently, without duplicates.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white">
  <img alt="Playwright" src="https://img.shields.io/badge/login-Playwright-2ead33?logo=microsoft&logoColor=white">
  <img alt="Status" src="https://img.shields.io/badge/status-working-21d4a3">
</p>

---

FontysCanvasCLI logs into Fontys Canvas (`fhict.instructure.com`) through the
ADFS single sign-on, completes the LTI 1.3 launch into Portflow
(`portfolio.drieam.app`), reads your existing portfolio, and then syncs a local
folder of files into it. It compares what is already there against what is on
disk, so re-running it is safe: new files are added, changed files get a new
version, and unchanged files are left alone. It can also request a review, share
the portfolio, and update a collection's reader, but those outward-facing steps
always ask first.

It is purpose-built for one account and one tenant. The Canvas tenant and
Portflow host are hard-wired in `lib/config.py`.

## Why it needs a browser

Portflow's LTI 1.3 launch cannot be done with plain HTTP requests. The handshake
routes through `sso.canvaslms.com/api/lti/authorize_redirect` and relies on LTI
Platform Storage (`postMessage`, so JavaScript), and the API then authorizes with
an in-memory `Authorization: Bearer` JWT that the single-page app never stores in
a cookie. So the login runs in a headless Chromium:

1. Playwright logs into Canvas via Fontys ADFS and runs the full launch, reusing
   a persistent profile (`.pw-profile/`) so later runs skip the Canvas login.
2. The launcher captures the Bearer token, the CSRF token, and the portfolio id
   from the app's own first API call, then hands a plain `requests.Session` to
   `PortflowClient` for the fast API work (upload, create, link, review, share).

The ADFS login and transport helpers in `lib/sso/` and `lib/http/` are vendored
from [fontys-canvas-bypass](https://github.com/NoelisTired/fontys-canvas-bypass);
the launch itself is browser-driven here.

## Install

```bash
git clone https://github.com/NoelisTired/FontysCanvasCLI.git
cd FontysCanvasCLI
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium
```

## Credentials

Credentials never live in this repo. They are read at run time from env vars, or
from `~/.claude/secrets.json` under the key `fontys`:

```json
{ "fontys": { "user": "you@fontys", "pass": "..." } }
```

Environment variables win when set: `CANVAS_USER` and `CANVAS_PASS`. The tool
never prints or writes your credentials.

If Fontys shows a second factor, run once with `CANVAS_HEADFUL=1` to approve it
interactively; the persistent profile then remembers the Canvas session for later
headless runs.

## Usage

Always dry-run first, read the plan, then apply. Both the source folder and the
target collection are auto-detected from a project directory:

- **source**: `<project>/output` if it exists, otherwise the project directory.
- **collection**: name-matched against your Portflow collections (for example
  `Roosterke-Documentatie` maps to `Roosterke.nl`). An ambiguous match exits with
  a list of candidates so you can re-run with `--collection "<name>"`.

```bash
# Sanity check: login, launch, and print the portfolio structure
./.venv/bin/python canvas.py --whoami

# Plan (dry-run): source and collection auto-detected from the project dir
./.venv/bin/python canvas.py --project "/path/to/Roosterke-Documentatie"

# Apply, only after you have read and approved the plan
./.venv/bin/python canvas.py --project "/path/to/Roosterke-Documentatie" --apply
```

Overrides: `--source <dir>`, `--collection "<name>"`. `--project` defaults to the
current working directory.

### The plan (dedupe logic)

Per sub-folder of the source (folder name maps to a goal via `goal-map.json`):

| Mark | Meaning |
| --- | --- |
| `＋ ADD` | No evidence with this file name exists, so create new evidence. |
| `↻ VERSION` | Evidence with this name exists but the content changed, so add a new version (no duplicate). |
| `· SKIP` | Same name, same content, so it is already up to date. |

Change detection hashes PDF text (PDFs embed build timestamps, so raw bytes are
not stable) and falls back to raw bytes for other files. The hashes are stored in
a local `.canvas-manifest.json`. `--force-version` turns every same-name match
into a new version.

### Goal mapping

Copy `goal-map.example.json` to `goal-map.json`. It maps folder names to goal
nicknames or full names, names the target collection, and lists `skip_folders`.
A file whose folder has no mapped goal is still uploaded, but reported as
unlinked.

## Reviews, sharing, reader: always confirmed

These three touch other people or replace content, so they are separate,
explicit steps. Nothing here happens automatically.

```bash
# Review: show the evidence version id and who it is shared with
./.venv/bin/python review.py --evidence <evidence_id>
# then, only after you confirm recipient and text:
./.venv/bin/python review.py --evidence <evidence_id> --send \
    --recipient <user_id> --message "Graag feedback op dit bewijs."

# Sharing: find a user id, see current access, then grant it
./.venv/bin/python share.py --search "rene"
./.venv/bin/python share.py --current
./.venv/bin/python share.py --send --user <id> --until 2027-08-31

# Reader (a collection's intro HTML): view, then set
./.venv/bin/python reader.py --project <dir>
./.venv/bin/python reader.py --project <dir> --set --from intro.html
```

A review attaches to the evidence's latest version, and the recipient must
already be shared with (the script refuses other ids). Updating the reader
replaces the whole description field, so it is confirmed.

## Layout

| Path | Purpose |
| --- | --- |
| `canvas.py` | Orchestrator: builds the plan and applies it. |
| `lib/lti/launcher_pw.py` | Active launcher: Playwright ADFS login plus LTI 1.3 launch; captures Bearer, CSRF, and portfolio id, and exports a `requests.Session`. |
| `lib/lti/launcher13.py` | Earlier pure-requests launch attempt, kept for reference; it does not complete the launch without a browser. |
| `lib/portflow.py` | Portflow `/api/v1` client: read structure and evidence, upload (direct-upload to S3 to evidence), link to goals, request feedback. |
| `lib/sso/`, `lib/http/` | Vendored unchanged from fontys-canvas-bypass: ADFS login and transport. |
| `lib/config.py` | Credentials and the Canvas/Portflow constants. |
| `review.py`, `share.py`, `reader.py` | The confirmed outward-facing commands. |

## Fragility

Everything under `portfolio.drieam.app/api/v1` was reverse-engineered from the
web UI. Drieam can change it at any time; the client raises `PortflowError` with
the status and body so failures are loud, not silent. The launch handshake and
any new Fontys MFA enforcement are the most likely break points. When the login
breaks, re-sync `lib/sso/` and `lib/http/` from fontys-canvas-bypass.

## Disclaimer

A personal automation tool for one student's own Fontys account and portfolio.
It uses your own credentials to do what you can already do by hand in the
browser. Use it only on an account you own.
