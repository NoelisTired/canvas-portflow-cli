<p align="center">
  <img src="docs/banner.png" alt="FontysCanvasCLI" width="100%">
</p>

<h1 align="center">FontysCanvasCLI</h1>

<p align="center">
  Sync a folder of evidence into your Instructure Canvas / Drieam Portflow portfolio, idempotently, with clickable @references in the reader.
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white">
  <img alt="Playwright" src="https://img.shields.io/badge/login-Playwright-2ead33?logo=microsoft&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-blue">
</p>

---

FontysCanvasCLI turns a folder of documents into portfolio evidence on
Portflow (`portfolio.drieam.app`), reached through Instructure Canvas. It reads
your existing portfolio and syncs a local folder into it: new files are added,
changed files get a new version, unchanged files are left alone, so re-running
is safe. It can also build the collection reader (the intro text) into a story
that links to your documents, and request reviews or share the portfolio, which
always ask first.

Defaults target Fontys, but every school-specific setting is configurable, so it
works for any Instructure Canvas + Drieam Portflow setup.

## No password required

The recommended login stores no password anywhere. You sign in once yourself in
a visible browser window (you type your own password, the tool never sees it),
and the browser profile remembers the session for later headless runs:

```bash
python canvas.py --login
```

If you prefer fully headless runs on your own machine, you can optionally put
credentials in a secrets file or env vars (see Credentials below). The tool only
ever uses them to fill the login form; it never intercepts, captures, or writes
a password, and never prints one.

## How a project is laid out

Point the tool at a project directory. Inside it:

```
my-portfolio/
  reader.txt                     # the reader/intro, with @references (optional)
  <Learning Outcome 1>/          # one folder per learning outcome
    Some document.pdf
    Another document.pdf
  <Learning Outcome 2>/
    ...
```

- Each **sub-folder is a learning outcome**; its files become evidence linked to
  that goal (mapping configured in `goal-map.json`).
- The optional **`reader.txt`** (or `reader.md`) is the collection intro. Write
  plain prose and reference a document with `@[Document name.pdf]`; the tool
  turns each reference into a clickable link to that evidence. This lets the
  reader be a chronological story that links to the documents it mentions.
- If a `output/` sub-folder exists it is used as the source; otherwise the
  project directory itself.

## Install

```bash
git clone https://github.com/NoelisTired/FontysCanvasCLI.git
cd FontysCanvasCLI
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium
cp canvas.config.example.json canvas.config.json   # then edit for your school
./.venv/bin/python canvas.py --login               # sign in once
```

## Configure (for a school other than Fontys)

Copy `canvas.config.example.json` to `canvas.config.json` and fill in your
values. Resolution order is env var, then config file, then the Fontys default.

| Setting | Env var | What it is |
| --- | --- | --- |
| `canvas_url` | `CANVAS_URL` | Your Canvas base URL, e.g. `https://<school>.instructure.com/` |
| `portflow_host` | `PORTFLOW_HOST` | The Portflow host (usually `portfolio.drieam.app`) |
| `portflow_launch_url` | `PORTFLOW_LAUNCH_URL` | The Canvas external-tool launch URL for the Portfolio nav item |
| `ntfy_topic` | `CANVAS_NTFY_TOPIC` | Optional ntfy.sh topic for a post-sync push; empty disables it |
| `feedpulse_course` / `feedpulse_tool` | `CANVAS_FEEDPULSE_COURSE` / `_TOOL` | Optional, only for the feedpulse command |

The config file holds no secrets. The browser profile (`.pw-profile/`) and any
secrets file stay out of git via `.gitignore`.

## Usage

Always dry-run first, read the plan, then apply.

```bash
# Sanity check: login, launch, print the portfolio structure
./.venv/bin/python canvas.py --whoami

# Plan (dry-run): source and collection auto-detected from the project dir
./.venv/bin/python canvas.py --project /path/to/my-portfolio

# Apply, only after you have read and approved the plan
./.venv/bin/python canvas.py --project /path/to/my-portfolio --apply

# Build the reader from reader.txt (with @references), replacing the current one
./.venv/bin/python reader.py --project /path/to/my-portfolio --set
./.venv/bin/python reader.py --project /path/to/my-portfolio --mentionable  # list @names
```

Overrides: `--source <dir>`, `--collection "<name>"`. `--project` defaults to the
current working directory. `--reseed` realigns the local change-detection
manifest without uploading; `--ntfy` pushes a post-apply summary.

### The plan (dedupe logic)

| Mark | Meaning |
| --- | --- |
| `＋ ADD` | No evidence with this file name exists, so create new evidence. |
| `↻ VERSION` | Same name exists but the content changed, so add a new version. |
| `· SKIP` | Same name, same content, already up to date. |

Change detection hashes PDF text with per-page footers (build date, page number)
stripped, so a plain rebuild does not look changed. The header shows
`Laatst geupdatet: <date> > nu` so you can see what is being replaced.

## Reviews, sharing, reader: always confirmed

These touch other people or replace content, so they are separate, explicit
steps that never happen automatically.

```bash
./.venv/bin/python review.py --evidence <id>                 # show recipients
./.venv/bin/python review.py --evidence <id> --send --recipient <uid> --message "..."
./.venv/bin/python share.py --search "name"                  # find a user
./.venv/bin/python share.py --send --user <id> --until 2027-08-31
```

## Why it needs a browser

Portflow's LTI 1.3 launch cannot be done with plain HTTP: the handshake routes
through `sso.canvaslms.com` and uses LTI Platform Storage (postMessage, so
JavaScript), and the API authorizes with an in-memory Bearer token the app never
stores in a cookie. So a headless Chromium does the login and launch, and hands a
plain `requests.Session` to the API client for the fast work. The SSO login and
transport helpers are vendored from
[fontys-canvas-bypass](https://github.com/NoelisTired/fontys-canvas-bypass).

## Fragility

Everything under `portfolio.drieam.app/api/v1` was reverse-engineered from the
web UI; Drieam can change it, and the client raises `PortflowError` with the
status and body so failures are loud. The launch handshake and any new MFA
enforcement are the most likely break points. Re-run `--login` if the saved
session expires.

## Disclaimer

A personal automation tool for your own Canvas/Portflow account. It uses your own
login to do what you can already do by hand in the browser. Use it only on an
account you own.
