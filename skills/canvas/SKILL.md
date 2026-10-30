---
name: canvas
description: Sync a folder of portfolio documents into a Portflow portfolio (portfolio.drieam.app) on Instructure Canvas, idempotently. Logs into Canvas (Fontys ADFS by default, configurable), does the LTI 1.3 launch into Portflow, reads the existing portfolio and prior submissions, then plans per folder which files to ADD, VERSION, or SKIP. Maps folders to learning-outcome goals, and can build the collection reader into a story with @file links. Reviews, sharing, and reader updates NEVER happen automatically; the skill always confirms first. Use when the user wants to upload or sync portfolio evidence to Portflow, "zet mijn bewijs in portflow", "sync mijn portfolio", update the reader/leeswijzer, or request a review.
metadata:
  type: skill
---

# canvas

Zet een map met documenten om in Portflow-portfoliobewijs, zonder duplicaten, en
kan de leeswijzer en reviews beheren, maar alleen na bevestiging. Standaard op
Fontys afgestemd, verder instelbaar via `canvas.config.json`.

## First use: one-time setup

The Python scripts live at the plugin root (`${CLAUDE_PLUGIN_ROOT}`). They need a
local virtualenv with requests + Playwright, created once. Before the first run,
check for `${CLAUDE_PLUGIN_ROOT}/.venv`; if it is missing, set it up:

```bash
cd "${CLAUDE_PLUGIN_ROOT}"
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium
./.venv/bin/python canvas.py --init     # onboarding: asks where the docs live, then login
```

After that, run the scripts with `${CLAUDE_PLUGIN_ROOT}/.venv/bin/python
${CLAUDE_PLUGIN_ROOT}/canvas.py ...`. The onboarding writes `canvas.config.json`
(no secrets) and the login stores only a browser session, never a password.

## Capabilities

| Commando | Wat het doet | Bevestiging nodig |
| --- | --- | --- |
| `canvas.py --init` | Onboarding-wizard: vraagt projectmap + URLs, schrijft config, biedt login aan | nee (schrijft alleen config) |
| `canvas.py --login` | Eenmalig interactief inloggen; geen wachtwoord opgeslagen | nee |
| `canvas.py --whoami` | Inloggen + portfoliostructuur tonen (read-only) | nee |
| `canvas.py --project <map>` | Proefrun: plan van ADD/VERSION/SKIP | nee (uploadt niets) |
| `canvas.py --project <map> --apply` | Het plan uitvoeren (uploaden + aan goals koppelen) | toon plan, dan uitvoeren |
| `canvas.py --reseed` | Wijzigingsdetectie-manifest herstellen zonder upload | nee |
| `status.py --project <map>` | Read-only rapport: sync-stand, ongekoppelde bewijzen, reviews | nee |
| `reader.py --project <map> --set` | Leeswijzer bouwen uit `reader.txt` met @file-links | ja (vervangt de reader) |
| `review.py --evidence <id> --send` | Een review aanvragen | ja (ontvanger + tekst) |
| `share.py --send --user <id>` | Iemand toegang geven tot het portfolio | ja (wie + tot wanneer) |
| `feedpulse.py` | FeedPulse-reflecties (Fontys-specifiek) | ja bij schrijven |

## When to use

- "zet mijn bewijs / evidence in Portflow", "sync mijn portfolio"
- "update mijn documenten / bewijs in portflow"
- "maak de leeswijzer / reader met links naar mijn documenten"
- "vraag een review aan op <evidence>"

Niet voor: Canvas-cijfers/opdrachten/modules (Canvas-eigen, niet Portflow), of
andere portfoliosystemen. Standaard gericht op `fhict.instructure.com` +
`portfolio.drieam.app`; andere instellingen via `canvas.config.json`.

## Login and credentials

Preferred: **no password stored.** Run `python canvas.py --login` once; a visible
browser opens and the user signs in themselves (the tool never sees the password).
The persistent profile (`.pw-profile/`) then remembers the session for later
headless runs. Re-run `--login` if the session expires.

Optional (fully headless): credentials from env `CANVAS_USER` / `CANVAS_PASS`, or
a secrets file (`~/.config/canvas-portflow/secrets.json`, or `~/.claude/secrets.json`,
key `fontys`/`canvas`/`portflow` with `user`+`pass`). Only used to fill the login
form; never intercepted, written, or printed.

## Configuration (school-specific settings)

All school-specific values resolve env var -> `canvas.config.json` -> Fontys
default. Copy `canvas.config.example.json` to `canvas.config.json` to change them:
`canvas_url`, `portflow_host`, `portflow_launch_url`, `ntfy_topic`,
`feedpulse_course`/`feedpulse_tool`. The config file holds no secrets and is
gitignored along with the profile and manifest.

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
cd ${CLAUDE_PLUGIN_ROOT}
python3 -m venv .venv
./.venv/bin/pip install requests playwright
./.venv/bin/python -m playwright install chromium
```

## How to run it

Always dry-run first, show the plan, then apply.

The skill auto-detects both the **source** and the **target collection** from a
project directory:
- **source**: `<project>/output` if present, else the project dir itself.
- **collection**: name-matched against the user's Portflow collections (e.g. a
  project folder whose name shares a word with a collection name). If the match
  is ambiguous it prints `status: ambiguous_collection` with candidates and exits
  3, after which the model asks the user and re-runs with `--collection "<name>"`.

```bash
cd ${CLAUDE_PLUGIN_ROOT}

# Sanity check: login + launch + portfolio structure
./.venv/bin/python canvas.py --whoami

# Plan (dry-run) from a project dir: source + collection auto-detected
./.venv/bin/python canvas.py --project "/pad/naar/mijn-portfolio"

# Apply, ONLY after the user approved the plan
./.venv/bin/python canvas.py --project "/pad/naar/mijn-portfolio" --apply
```

Overrides: `--source <dir>` and `--collection "<name>"`. `--project` defaults to
the current working directory, so "update my docs in my portfolio" from inside a
project folder just works. After `--apply` the skill prints the uploaded
evidence ids and the `review.py` command to request feedback, never automatic.

### The plan (dedupe logic)

Per sub-folder of `--source` (folder name maps to a goal via the map). The plan
header prints `Laatst geupdatet: <date> > nu`, and each ADD/VERSION line shows
the same, so it is clear what is being replaced:

| Mark | Meaning |
| --- | --- |
| `＋ ADD` | No evidence with this file name exists, so create new evidence (shown as `(nieuw)`). |
| `↻ VERSION` | Same name exists but the content changed, so add a new version, no duplicate (shown as `(laatst <date> > nu)`). |
| `· SKIP` | Same name, same content, already up to date, left alone. |

Change detection hashes the PDF text layer with per-page footers (build date,
page number) stripped, so a plain rebuild does NOT look changed. The hashes live
in `.canvas-manifest.json`. `--force-version` turns every same-name match into a
new VERSION.

**`--reseed`** recomputes and stores the hash of every file that already exists
on Portflow, without uploading anything. Run it after a hashing change or when
the manifest is lost/stale, so unchanged files stop showing up as new versions.
It leaves genuinely-new files as ADD.

**Launch robustness.** The LTI launch is occasionally flaky (the Bearer is
missed). `connect()` retries the browser launch up to 3 times
(`CANVAS_LAUNCH_ATTEMPTS`, default 3) before giving up, so a single miss no
longer fails the whole run.

### Goal mapping

`goal-map.json` (copy from `goal-map.example.json`) maps folder names to goal
nicknames (LO1, LO2, ...) or full names, names the target `collection`, and lists
`skip_folders`. A file whose folder has no mapped goal is still uploaded, but
reported as "geen goal" and left unlinked.

## Reviews, always ask first

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
be someone the evidence is already shared with (`shared_users`); the script
refuses other ids. If they aren't shared yet, share the portfolio first (below).

## Reader (collection intro), confirmed

The "reader"/leeswijzer is the HTML `description` at the top of a collection
(`PUT /portfolios/{pid}/collections/{cid}`). Updating it replaces the whole
field, so it is confirmed. The project's `portfolio.txt` is an ASCII layout for
the PDF, NOT the web reader: do not push it verbatim; pass real HTML or a prose
file.

**Source auto-detect:** with `--set` and no `--from`/`--html`, the tool uses
`<project>/reader.txt` (or `reader.md`) if present. So the convention is a
`reader.txt` next to the learning-outcome folders.

**@references:** in the reader body, `@[Document name.pdf]` (or bare
`@Document name.pdf`) becomes a clickable Portflow mention linking to that
evidence, so the reader can be a chronological story that links to its documents.
A name that matches no evidence is reported and left as plain text (use
`--mentionable` to list the exact names). Mechanism: the span
`<span data-type="mention" data-id=.. data-mention-type="evidence" ...>`, with
`GET /mentions/evidence` as the name->id lookup.

```bash
./.venv/bin/python reader.py --project <dir>                 # show current reader
./.venv/bin/python reader.py --project <dir> --mentionable   # list @-referable names
./.venv/bin/python reader.py --project <dir> --set           # uses reader.txt
./.venv/bin/python reader.py --project <dir> --set --from intro.md
```

## Sharing, always ask first

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
