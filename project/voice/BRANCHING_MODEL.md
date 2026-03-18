# BLOODINTHEWIRE — Branching Publish Model

Version: v1
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
A new destination page is generated.
The engine then rolls again for that destination:

  - If the destination roll = 1: the destination becomes a full content
    page. Following the link takes you there — a dead end in the best sense.

  - If the destination roll = 0: the destination becomes a junction node
    (an intermediate page that accumulates its own threads). The content
    then recursively re-rolls from that node.

This continues until a 1 is rolled or the depth cap is reached.

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
  fragments/<slug>.html        ← full fragment pages
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

---

## Branch Log

Every branching decision is recorded in `project/branch-log.json`:

```json
{
  "entries": [
    {
      "entry_id": "entry-0007-something",
      "title": "entry_0007 :: something",
      "depth": 0,
      "roll": 0,
      "action": "link",
      "dest_page": "nodes/node-entry-0007-d1-20260318-143200.html",
      "dest_type": "node",
      "dest_roll": 0,
      "target_page": "index.html",
      "timestamp_utc": "2026-03-18T14:32:00Z",
      "posted_date": "2026-03-18"
    },
    {
      "entry_id": "entry-0007-something",
      "title": "entry_0007 :: something",
      "depth": 1,
      "roll": 1,
      "action": "inline",
      "page": "nodes/node-entry-0007-d1-20260318-143200.html",
      "target_page": "nodes/node-entry-0007-d1-20260318-143200.html",
      "timestamp_utc": "2026-03-18T14:32:00Z",
      "posted_date": "2026-03-18"
    }
  ]
}
```

This log is the ground truth. Pages are never re-rolled — the stored
decisions determine page structure permanently.

---

## CSS Card Types

| Class              | Meaning                                      |
|--------------------|----------------------------------------------|
| `cascade-rich`     | Inline post — full teaser/body visible       |
| `cascade-link`     | Lean link card — minimal, points elsewhere   |
| `cascade-node`     | Lean card pointing to a junction node        |
| `cp-a` … `cp-g`    | Stagger positions, cycle based on post count |

Stagger positions are assigned deterministically by counting existing
cascade blocks on the target page (mod 7). This means the visual rhythm
is consistent and not re-derived at render time.

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
than always growing new ones from root.

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
- **Automatable.** No human decides inline vs. link — the engine does.
- **Extendable.** Node pages accumulate threads over time.
- **No runaway nesting.** Depth cap prevents infinite recursion.
