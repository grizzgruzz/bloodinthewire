# BLOODINTHEWIRE — Voice Post Workflow

Version: v1
Status: Canon

---

## Ownership Principle (Read This First)

**All narrative voice text is owned by `wirevoice-core`.**

The utility scripts (`build_voice_request.py`, `generate_post.py`) are
mechanical tools.  They assemble inputs, parse outputs, and format files.
They do NOT write, improve, reword, or fill in any prose.

If the body text of a post needs to change, that change goes back to
`wirevoice-core` — not to a script edit.

---

## End-to-End Flow

```
 ┌──────────────────────────────────────────────────────────────────┐
 │  1. INCOMING ASSET                                               │
 │     Drop image into assets/incoming/  (or let library supply it) │
 └────────────────────────┬─────────────────────────────────────────┘
                          │
                          ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  2. SELECT ASSET                                                 │
 │     scripts/select_asset.py                                      │
 │     • Picks from incoming/ first, then library/                  │
 │     • Archives to published/ with timestamp                      │
 │     • Strips metadata → outputs web-ready path in assets/web/    │
 │     $ path=$(python scripts/select_asset.py)                     │
 └────────────────────────┬─────────────────────────────────────────┘
                          │  web-ready image path
                          ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  3. BUILD VOICE REQUEST                                          │
 │     scripts/build_voice_request.py                               │
 │     • Loads VOICE_BIBLE.md + GENERATOR_PROMPT.md                 │
 │     • Reads latest published note sidecar (if present)           │
 │     • Assembles a single self-contained prompt file              │
 │     • Writes to voice/requests/request-<timestamp>.txt           │
 │     $ req=$(python scripts/build_voice_request.py \              │
 │               --image-web-path "$path")                          │
 └────────────────────────┬─────────────────────────────────────────┘
                          │  prompt file path
                          ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  4. WIREVOICE-CORE GENERATION                                    │
 │     Agent: wirevoice-core                                        │
 │     • Receives the request bundle (prompt file content)          │
 │     • Generates post draft in strict GENERATOR_PROMPT format     │
 │     • Returns ONLY the strict block — no preamble, no extras     │
 │                                                                  │
 │     VOICE TEXT OWNERSHIP: wirevoice-core is sole author.         │
 │     Utility scripts do not touch or alter the narrative prose.   │
 │                                                                  │
 │     Strict output format:                                        │
 │       TITLE: ...                                                 │
 │       TIMESTAMP: ...                                             │
 │       BODY:                                                      │
 │       ...                                                        │
 │       EVIDENCE_LINE: ...                                         │
 │       TAGS: ...                                                  │
 │                                                                  │
 │     Save the output to a file, e.g.:                             │
 │       voice/requests/draft-<timestamp>.txt                       │
 └────────────────────────┬─────────────────────────────────────────┘
                          │  strict-format draft file
                          ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  5. GENERATE POST                                                │
 │     scripts/generate_post.py                                     │
 │     • Parses strict-format draft (validates all required fields) │
 │     • Produces:                                                  │
 │         content/drafts/<slug>.md          (markdown draft)       │
 │         content/drafts/<slug>.html.frag   (HTML fragment draft)  │
 │     • Emits next-step publish instructions                       │
 │     $ python scripts/generate_post.py \                          │
 │         --voice-draft-file "$draft" \                            │
 │         --image-web-path "$path"                                 │
 └────────────────────────┬─────────────────────────────────────────┘
                          │  draft files
                          ▼
 ┌──────────────────────────────────────────────────────────────────┐
 │  6. REVIEW + PUBLISH                                             │
 │     • Review content/drafts/<slug>.md for tone and accuracy      │
 │     • If prose needs changing → return to wirevoice-core (step 4)│
 │     • Copy HTML fragment to fragments/ and wire into index.html  │
 │     • Move markdown to content/entries/ and mark published       │
 │     • git add new files; review; commit when satisfied           │
 └──────────────────────────────────────────────────────────────────┘
```

---

## Quick-Start Commands

```bash
# From project root (bloodinthewire/)
cd project

# 1-2. Select asset (handles pipeline automatically)
path=$(python scripts/select_asset.py)

# 3. Build the wirevoice-core request
req=$(python scripts/build_voice_request.py --image-web-path "$path")

# 4. Feed $req to wirevoice-core; save output to:
draft=voice/requests/draft-$(date +%Y%m%d-%H%M%S).txt
# (paste or pipe wirevoice-core response into $draft)

# 5. Generate draft artifacts
python scripts/generate_post.py \
    --voice-draft-file "$draft" \
    --image-web-path "$path"
```

---

## Agent Reference

| Agent           | Role                                                            |
|-----------------|----------------------------------------------------------------|
| `main`          | Orchestrates workflow, delegates to wirevoice-core             |
| `wirevoice-core`| Sole author of narrative voice text                            |
| `coder`         | Utility work: scripts, config, infrastructure                  |

---

## Key Files

| File                                     | Purpose                              |
|------------------------------------------|--------------------------------------|
| `voice/VOICE_BIBLE.md`                  | Canonical voice rules (read-only)    |
| `voice/GENERATOR_PROMPT.md`             | Output format + execution rules      |
| `voice/WORKFLOW.md`                     | This file                            |
| `voice/requests/request-*.txt`          | Assembled prompts for wirevoice-core |
| `voice/requests/draft-*.txt`            | Raw wirevoice-core output            |
| `scripts/select_asset.py`               | Asset pipeline (select, strip, archive) |
| `scripts/build_voice_request.py`        | Assemble wirevoice-core prompt bundle |
| `scripts/generate_post.py`              | Parse draft → markdown + HTML frag   |
| `content/drafts/`                       | Staging area for generated artifacts |
| `content/entries/`                      | Published post records               |
| `assets/web/`                           | Web-ready, metadata-stripped images  |

---

## Notes on Tone and Continuity

Consult `VOICE_BIBLE.md` for full guidance. Key reminders:

- No em dashes. No emojis.
- First-person narrator is certain. No hedging on observed events.
- Escalation is non-linear. Vary motifs across posts.
- recurring names and references should persist across sessions.

Build continuity by noting recurring elements in `--seed-context` when
calling `build_voice_request.py`.
