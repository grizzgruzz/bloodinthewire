# BLOODINTHEWIRE — Branching Publish Model

Version: v3
Status: Canon

---

## Overview

Every new post is placed in the web through a coin-flip (0/1) branching
decision. Over time this produces an organic spiderweb of pages — some
content surfaces inline on the homepage, others require following links
one or more levels deep.

The site must remain readable. The chaos must feel intentional.

---

## The Model in Plain English

When a new post is published, the engine rolls a single random bit.

**Roll = 1 → INLINE**
The post lands directly on the current page as a rich cascade card.
The reader finds it without navigating.

**Roll = 0 → LINK**
A lean link card is inserted on the current page.
The engine then determines what the link points to (see CONVERGENCE RULE).

---

## CONVERGENCE RULE (v3 — Hard Rule)

> **A generated link may point to a new contextual page OR an already
> existing contextual page. If it points to an existing page, BRANCHING
> ENDS THERE.**

When roll=0, the engine runs this decision in order:

1. **Scan existing pages** (all `fragments/*.html` and content-type
   `nodes/*.html`) for contextual relevance to the new post.

2. **Score by motif-word overlap**: extract normalized keyword tokens
   (length ≥ 4 chars, no stop words) from the new post's title+teaser.
   Compare against each existing page's motif words (derived from filename
   slug, `<title>`, first `<h2>`, and optional `data-motif` attribute).

3. **Select the best match**: highest intersection score wins. Ties
   resolve alphabetically (deterministic, not random).

4. **If score ≥ convergence_threshold (default: 1)**:
   - Insert a lean link card pointing to the existing page.
   - **NO new page is created. NO recursion continues.**
   - This is logged as `action: "link-existing"` in branch-log.json.

5. **If no existing page meets the threshold**:
   - Proceed as v2: roll for destination type (content vs. junction node),
     create the new page, and recurse as needed.

### Why This Rule Exists

- Prevents runaway node sprawl
- Keeps links contextually coherent — a sighting post links back to
  sighting evidence, not a random audio anomaly page
- Mirrors how real personal-web sites accumulate cross-links over time
- Makes the site feel like an actual investigative record, not a random
  tree generator

### Relevance Priority

Matching prefers (in order of weight):
1. **Shared motif words** — "sighting", "frequency", "map", "street", etc.
2. **Evidence class** — same type of anomaly (visual / audio / location)
3. **Tone proximity** — paranoid surveillance vs. cold signal analysis
4. **Semantic fit** — nearby subject matter even without exact keyword match

The motif-word overlap score captures (1) and (4) mechanically.
Points (2) and (3) are handled by careful `data-motif` tagging on
existing pages — add tags that reflect evidence class and tone,
not just literal words.

### Tuning Convergence

- **`--convergence-threshold N`** (default: 1): raise to require stricter
  matching before reusing an existing page. Use 2+ when the site has many
  existing pages and you want to limit accidental convergence.
- **`--no-convergence`**: disable reuse entirely (for testing / forced
  growth passes when you deliberately want new structure). Not for
  normal publishes.

---

## Full Decision Tree

```
publish(post)
├── roll = 1  →  INLINE on current page
│                  (end)
└── roll = 0
    ├── relevant existing page found (score ≥ threshold)?
    │   YES  →  LINK to existing page
    │              (branching ends — no new pages, no recursion)
    └── NO   →  roll dest_type
                ├── dest_type = content  →  create new content page
                │                           insert LINK card
                │                           (end — content page is terminal)
                └── dest_type = node    →  create new junction node
                                           insert LINK card
                                           recurse into node
                                           (continues until roll=1 or depth_cap)
```

---

## Depth Cap

Default: **5 levels**

At depth cap, the engine always treats the roll as 1 (inline), so the
content always lands somewhere readable regardless of luck. The cap is
a soft safeguard, not a creative boundary.

Set a different cap with `--depth-cap N` when calling `branch_publish.py`.

---

## On-Disk Representation

```
bloodinthewire/
  index.html                   ← root page (depth 0)
  fragments/<slug>.html        ← full fragment pages (public content)
  nodes/
    node-<slug>-d1-<ts>.html   ← depth-1 junction or content page
    node-<slug>-d2-<ts>.html   ← depth-2 junction or content page
    ...
  project/
    branch-log.json            ← permanent record of all rolls + decisions
```

**Junction node** (`dest_type: node`):
- Has `CASCADE:START`/`CASCADE:END` markers so future posts can land inside it.
- Has `LINKS:START`/`LINKS:END` for its own related-threads list.
- Looks like a mini version of the homepage.

**Content node** (`dest_type: content`):
- Renders the full post content inline.
- Terminal: not a landing zone for future posts.
- Eligible for convergence reuse by future posts.

---

## Branch Log

Every branching decision is recorded in `project/branch-log.json`.

v3 adds `link-existing` as a valid `action` value:

```json
{
  "entry_id": "entry-0007-something",
  "title": "entry_0007 :: something",
  "depth": 0,
  "roll": 0,
  "action": "link-existing",
  "dest_page": "fragments/sighting-0002.html",
  "dest_type": "existing-content",
  "convergence": true,
  "convergence_score": 3,
  "convergence_threshold": 1,
  "target_page": "index.html",
  "orientation": "vertical",
  "insertion_index": 2,
  "n_existing_at_publish": 4,
  "timestamp_utc": "2026-03-19T10:00:00Z",
  "posted_date": "2026-03-19"
}
```

All other action types (`inline`, `link`) remain as documented in v2.

---

## CSS Card Types

| Class                      | Meaning                                       |
|----------------------------|-----------------------------------------------|
| `cascade-rich`             | Inline post — full teaser/body visible        |
| `cascade-link`             | Lean link card — minimal, points elsewhere    |
| `cascade-converge`         | Lean link card pointing to an existing page   |
| `cascade-node`             | Lean card pointing to a junction node         |
| `cp-a` … `cp-g`            | Stagger positions, cycle based on post count  |
| `cascade-orient-vertical`  | Stacked layout (image below text, default)    |
| `cascade-orient-horizontal`| Side-by-side layout (image left, text right)  |

The `cascade-converge` class is added alongside `cascade-link` when the
link points to an existing page (convergence action). It can be used for
subtle styling to differentiate "back-link" cards if desired — or ignored
for a fully uniform appearance.

---

## v2 Additions: Orientation + Insertion Index

**Orientation roll** (stored as `orientation` in branch-log.json):
- `vertical`   → stacked layout (default)
- `horizontal` → image left, text right, side-by-side

Rolled once at root publish time. Propagated to all recursive levels.
Never re-derived from the log — it is stored permanently.

**Insertion index** (stored as `insertion_index` in branch-log.json):
- Controls WHERE in the CASCADE stack the new card lands.
- `0` = before the first block (top of stack).
- `N` = after the Nth existing cascade block.
- Rolled uniformly over `[0, count_existing_blocks]`.
- Stored with `n_existing_at_publish` for full reproducibility.

Both values appear in `branch-log.json` on every entry from v2 onward.

---

## Publishing a New Post

See WORKFLOW.md Step 6. Use `branch_publish.py` for all new posts:

```bash
python project/scripts/branch_publish.py \
  --title "entry_0007 :: something" \
  --teaser "one-line teaser text" \
  --posted-date 2026-03-18 \
  --fragment-href "fragments/entry-0007.html" \
  --timestamp "14:22" \
  --image-web-path "project/assets/web/img.jpg" \
  --links-note "short note for related list"
```

The script handles the rest. Review `project/branch-log.json` and the
modified HTML files before git-adding.

**Convergence controls** (rarely needed):

```bash
# Raise threshold — only reuse if 2+ shared motif words
--convergence-threshold 2

# Disable convergence entirely (forced new-branch pass)
--no-convergence
```

---

## Tagging Existing Pages for Relevance

Fragment pages and content nodes should carry a `data-motif` attribute on
their root `<html>` element to aid relevance matching:

```html
<html lang="en" data-motif="sighting,face,pattern,repeat,street,surveillance">
```

Motif tags should reflect:
- **Topic words**: sighting, frequency, map, signal, audio, street, camera
- **Evidence class**: visual, audio, location, document, digital
- **Tone words**: paranoid, cold, distant, static, absent, deliberate

The engine automatically extracts motif words from the filename slug,
`<title>`, and `<h2>` — but `data-motif` lets you add semantic context
that isn't visible in those sources. Use it for new pages created by
hand or by the voice pipeline.

---

## Targeting a Specific Node

To deliberately plant a new post inside an existing node (e.g., to
extend a thread), use `--target-page`:

```bash
python project/scripts/branch_publish.py \
  --title "entry_0009 :: follow-up" \
  --teaser "it happened again" \
  --posted-date 2026-03-19 \
  --target-page "nodes/node-entry-0007-d1-20260318-143200.html" \
  ...
```

This is valid and intentional — it thickens existing branches rather
than always growing new ones from root. Convergence check still runs
at the node level; relevant existing pages reachable from there will
attract link-existing actions.

---

## Existing Posts (Pre-Branching)

The three posts that existed before this model was introduced were given
deterministic manual assignments on 2026-03-17:

| Entry     | Assignment  | Position | Rationale                         |
|-----------|-------------|----------|-----------------------------------|
| entry_0003 | inline (rich) | cp-a  | Most recent, most visible         |
| entry_0002 | link         | cp-d   | Already a short sighting entry    |
| entry_0001 | inline (rich) | cp-c  | Long field-notes, warrants body   |

These are frozen — `data-branch-seed` is recorded in the HTML and will
not be re-evaluated.

---

## Design Principles

- **Readable first.** A link card is still clean, not broken.
- **Organic, not random.** Rolled decisions are stored; structure is stable.
- **Contextually coherent.** Links reuse existing pages only when semantically
  relevant. The site accumulates connections that make sense.
- **Convergent by nature.** The web grows but also folds back on itself,
  like memory. A sighting links to a prior sighting. A signal links to
  frequency evidence. Patterns emerge.
- **Automatable.** No human decides inline vs. link — the engine does.
- **Extendable.** Node pages accumulate threads over time.
- **No runaway nesting.** Depth cap prevents infinite recursion.
  Convergence rule prevents runaway node sprawl.
