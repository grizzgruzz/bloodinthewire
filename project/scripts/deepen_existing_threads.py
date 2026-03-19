#!/usr/bin/env python3
"""
deepen_existing_threads.py  v1
==============================
Migration script: extend every branch that currently has depth >= 1
by exactly +3 additional depth layers from its deepest reachable point.

Branches extended:
  1. orbit-map          node-orbit-map-d1 (depth=1)  → +3 → d2, d3, d4
  2. freq-signal        node-freq-signal-d2 (depth=2) → +3 → d3, d4, d5
  3. clipboard-man      node-clipboard-man-d3 (depth=3) → +3 → d4, d5, d6
  4. the-feed-shows     node-the-feed-shows-t (depth=1) → +3 → d2, d3, d4
  5. sighting-0002      fragments/sighting-0002.html (depth=1 terminal) → +3 → d2, d3, d4
  6. street-log-01      fragments/street-log-01.html (depth=1 terminal) → +3 → d2, d3, d4

Rules preserved:
  - Depth-0 cannot link directly to nodes/*
  - Anti-backlink/ancestor loop prevention (branching goes forward only)
  - Anti-image-reuse (library assets, each node gets a fresh or no image)
  - Static site only (no runtime mutation)
  - Nav semantics intact (up/down/home in headers+footers)

Usage:
  python deepen_existing_threads.py [--dry-run]

After running:
  - git diff → review
  - git add -A && git commit
"""

from __future__ import annotations

import argparse
import html as _html
import json
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
NODES_DIR = REPO_ROOT / "nodes"
FRAGS_DIR = REPO_ROOT / "fragments"
BRANCH_LOG = REPO_ROOT / "project" / "branch-log.json"
LIBRARY_DIR = REPO_ROOT / "project" / "assets" / "library"
WEB_DIR = REPO_ROOT / "project" / "assets" / "web"
PUBLISHED_DIR = REPO_ROOT / "project" / "assets" / "published"

TS = "20260318-235900"  # migration timestamp (deterministic)
POSTED_DATE = "2026-03-18"


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _copy_library_image(lib_filename: str) -> str:
    """
    Copy a library image to assets/web/ and assets/published/ with a timestamp
    suffix. Returns the web-relative path (e.g. 'project/assets/web/Foo_TS.jpg').
    """
    src = LIBRARY_DIR / lib_filename
    if not src.exists():
        print(f"  [WARN] library image not found: {lib_filename}", file=sys.stderr)
        return ""

    stem = src.stem
    suffix = src.suffix
    # Use a pseudo-unique timestamp suffix per image
    img_ts = time.strftime("%Y%m%d-%H%M%S")
    web_name = f"{stem}_{img_ts}{suffix}"
    pub_name = f"{stem}__{img_ts}{suffix}"

    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    WEB_DIR.mkdir(parents=True, exist_ok=True)

    pub_path = PUBLISHED_DIR / pub_name
    web_path = WEB_DIR / web_name

    if not web_path.exists():
        shutil.copy2(src, pub_path)
        # Try Pillow metadata strip; fall back to plain copy
        stripped = False
        try:
            from PIL import Image as _PILImage  # type: ignore[import]
            ext = suffix.lower()
            with _PILImage.open(pub_path) as img:
                if ext in (".jpg", ".jpeg"):
                    if img.mode in ("RGBA", "P", "LA"):
                        img = img.convert("RGB")
                    img.save(web_path, format="JPEG", quality=92, optimize=True,
                             exif=b"", icc_profile=None)
                else:
                    data = img.tobytes()
                    clean = _PILImage.frombytes(img.mode, img.size, data)
                    clean.save(web_path, format="PNG", optimize=True)
            stripped = True
        except Exception:
            pass
        if not stripped:
            shutil.copy2(pub_path, web_path)

    return f"project/assets/web/{web_name}"


# ---------------------------------------------------------------------------
# HTML templates
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    return _html.escape(s)


def make_junction_node(
    node_slug: str,
    entry_title: str,
    posted_date: str,
    depth: int,
    parent_href: str,
    content_html: str,
    links_html: str = "",
    footer_extras: str = "",
    data_motif: str = "",
) -> str:
    """Junction node page with CASCADE:START/END and LINKS:START/END blocks."""
    parent_label = "return to parent"
    nav_up = f'<p class="nav-up"><a href="{_esc(parent_href)}">[up] {parent_label}</a></p>'
    nav_home = (
        '<p class="nav-home"><a href="../index.html">[home] return to entrypoint</a></p>'
        if parent_href != "../index.html"
        else ""
    )
    motif_attr = f' data-motif="{_esc(data_motif)}"' if data_motif else ""

    return f"""<!doctype html>
<html lang="en"{motif_attr}>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>bloodinthewire :: {_esc(entry_title)}</title>
  <link rel="stylesheet" href="../styles.css" />
</head>
<body>
  <div class="noise"></div>
  <main class="container">
    <header>
      <p class="stamp">NODE // depth={depth} // branched from: {_esc(node_slug)}</p>
      {nav_up}
      <h2>{_esc(entry_title)}</h2>
      <p class="sub">branch junction // follow the threads</p>
      <hr />
    </header>

    <div class="node-shell">
      <p class="node-label">NODE :: {_esc(node_slug)} // generated: {posted_date}</p>
      <!-- CASCADE:START -->
{content_html}
      <!-- CASCADE:END -->
    </div>

    <div class="node-threads">
      <h4>threads // from this node</h4>
      <ul>
        <!-- LINKS:START -->
{links_html}        <!-- LINKS:END -->
      </ul>
    </div>

    <footer>
      <hr />
      {nav_up}
      {nav_home}
{footer_extras}      <p class="tiny-note">depth={depth} // branched: {posted_date}</p>
    </footer>
  </main>
</body>
</html>
"""


def make_terminal_node(
    node_slug: str,
    entry_title: str,
    posted_date: str,
    depth: int,
    parent_href: str,
    content_html: str,
    data_motif: str = "",
    footer_note: str = "",
) -> str:
    """Terminal content node (no CASCADE/LINKS blocks)."""
    parent_label = "return to parent"
    nav_up = f'<p class="nav-up"><a href="{_esc(parent_href)}">[up] {parent_label}</a></p>'
    nav_home = (
        '<p class="nav-home"><a href="../index.html">[home] return to entrypoint</a></p>'
        if parent_href != "../index.html"
        else ""
    )
    motif_attr = f' data-motif="{_esc(data_motif)}"' if data_motif else ""

    footer_note_line = f'      <p class="tiny-note">{_esc(footer_note)}</p>\n' if footer_note else ""

    return f"""<!doctype html>
<html lang="en"{motif_attr}>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>bloodinthewire :: {_esc(entry_title)}</title>
  <link rel="stylesheet" href="../styles.css" />
</head>
<body>
  <div class="noise"></div>
  <main class="container">
    <header>
      <p class="stamp">NODE // depth={depth} // terminal</p>
      {nav_up}
      <h2>{_esc(entry_title)}</h2>
      <hr />
    </header>

{content_html}

    <footer>
      <hr />
      {nav_up}
      {nav_home}
{footer_note_line}      <p class="tiny-note">depth={depth} // posted: {posted_date} // thread ends here</p>
    </footer>
  </main>
</body>
</html>
"""


def inline_block(entry_id: str, title: str, body_html: str, stamp: str, depth: int, img_web_path: str = "") -> str:
    img_section = ""
    if img_web_path:
        img_section = (
            f'      <figure class="evidence">'
            f'<img src="{_esc(img_web_path)}" alt="evidence"></figure>\n'
        )
    return (
        f'    <!-- branch: inline  depth={depth}  seed=1  orient=vertical -->\n'
        f'    <section class="cascade-block cascade-rich cp-a cascade-orient-vertical"\n'
        f'      data-entry="{_esc(entry_id)}"\n'
        f'      data-type="inline"\n'
        f'      data-depth="{depth}"\n'
        f'      data-branch-seed="1"\n'
        f'      data-orientation="vertical">\n'
        f'      <h2>{_esc(title)}</h2>\n'
        f'      <div class="wire-body">\n'
        f'{body_html}'
        f'      </div>\n'
        f'{img_section}'
        f'      <p class="stamp">{_esc(stamp)}</p>\n'
        f'    </section>\n\n'
    )


def link_card_html(entry_id: str, title: str, teaser: str, dest_href: str, depth: int, is_node: bool = True) -> str:
    card_class = "cascade-node" if is_node else "cascade-link"
    link_text = "open node" if is_node else "open entry"
    return (
        f'    <!-- branch: link  depth={depth}  seed=0  orient=vertical -->\n'
        f'    <section class="cascade-block {card_class} cp-b cascade-orient-vertical"\n'
        f'      data-entry="{_esc(entry_id)}"\n'
        f'      data-type="link"\n'
        f'      data-depth="{depth}"\n'
        f'      data-branch-seed="0"\n'
        f'      data-orientation="vertical">\n'
        f'      <h2>{_esc(title)}</h2>\n'
        f'      <span class="lean-link">'
        f'<a href="{_esc(dest_href)}">{link_text}</a>'
        f' <span class="stamp">// {_esc(teaser)}</span></span>\n'
        f'      <p class="stamp">posted: {POSTED_DATE}</p>\n'
        f'    </section>\n\n'
    )


# ---------------------------------------------------------------------------
# Page patching helpers
# ---------------------------------------------------------------------------

def insert_into_cascade(page_path: Path, card_html: str) -> None:
    """Append a card just before <!-- CASCADE:END -->."""
    src = page_path.read_text(encoding="utf-8")
    marker = "      <!-- CASCADE:END -->"
    if marker not in src:
        raise RuntimeError(f"No CASCADE:END in {page_path}")
    src = src.replace(marker, card_html + marker, 1)
    page_path.write_text(src, encoding="utf-8")


def insert_into_links(page_path: Path, li_html: str) -> None:
    """Append a list item just before <!-- LINKS:END -->."""
    src = page_path.read_text(encoding="utf-8")
    marker = "        <!-- LINKS:END -->"
    if marker not in src:
        # Try alternate
        marker = "<!-- LINKS:END -->"
        if marker not in src:
            print(f"  [WARN] No LINKS:END in {page_path}", file=sys.stderr)
            return
    src = src.replace(marker, li_html + marker, 1)
    page_path.write_text(src, encoding="utf-8")


def insert_before_footer_close(page_path: Path, html_to_insert: str) -> None:
    """Insert HTML just before the <footer> tag."""
    src = page_path.read_text(encoding="utf-8")
    target = "    <footer>"
    if target not in src:
        target = "  <footer>"
    if target not in src:
        print(f"  [WARN] No <footer> tag in {page_path}", file=sys.stderr)
        return
    # Insert before LAST occurrence (in case there are multiple)
    idx = src.rfind(target)
    src = src[:idx] + html_to_insert + "\n" + src[idx:]
    page_path.write_text(src, encoding="utf-8")


def patch_footer_add_nav(page_path: Path, nav_html: str) -> None:
    """Add a nav-down line to the footer (after the <hr />)."""
    src = page_path.read_text(encoding="utf-8")
    # Find the last footer's hr and add after it
    footer_hr = "      <hr />"
    last_idx = src.rfind(footer_hr)
    if last_idx == -1:
        footer_hr = "    <hr />"
        last_idx = src.rfind(footer_hr)
    if last_idx == -1:
        return
    insert_at = last_idx + len(footer_hr)
    src = src[:insert_at] + "\n" + nav_html + src[insert_at:]
    page_path.write_text(src, encoding="utf-8")


# ---------------------------------------------------------------------------
# Branch log helpers
# ---------------------------------------------------------------------------

def load_log() -> dict:
    if BRANCH_LOG.exists():
        return json.loads(BRANCH_LOG.read_text(encoding="utf-8"))
    return {"entries": [], "meta": {"version": 1}}


def append_log(log: dict, entry: dict) -> None:
    log.setdefault("entries", []).append(entry)


def save_log(log: dict) -> None:
    BRANCH_LOG.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Branch definitions
# ---------------------------------------------------------------------------

def run_migration(dry_run: bool = False) -> list:
    """
    Execute all branch deepening operations.
    Returns a list of (branch_id, old_depth, new_depth, pages_created) tuples.
    """
    log = load_log()
    report = []
    created_pages = []

    NODES_DIR.mkdir(parents=True, exist_ok=True)

    # ═══════════════════════════════════════════════════════════════════════
    # BRANCH 1: orbit-map
    # deepest: nodes/node-orbit-map-d1-20260317.html (depth=1)
    # extend: d2, d3, d4
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[1/6] orbit-map branch (depth=1 → depth=4)")

    # Images
    img_orbitmap_d3 = _copy_library_image("Hillcrest-Garage-sign.jpg") if not dry_run else "project/assets/web/Hillcrest-Garage-sign_DRY.jpg"
    img_orbitmap_d4 = _copy_library_image("Parking-Area.jpg") if not dry_run else "project/assets/web/Parking-Area_DRY.jpg"

    orbitmap_d2_slug = "node-orbit-map-d2-20260318"
    orbitmap_d3_slug = "node-orbit-map-d3-20260318"
    orbitmap_d4_slug = "node-orbit-map-d4-20260318"

    orbitmap_d2_path = NODES_DIR / f"{orbitmap_d2_slug}.html"
    orbitmap_d3_path = NODES_DIR / f"{orbitmap_d3_slug}.html"
    orbitmap_d4_path = NODES_DIR / f"{orbitmap_d4_slug}.html"

    # d2: junction node — name research
    orbitmap_d2_body = inline_block(
        entry_id="orbit-map-name-research",
        title="the name // initial research",
        body_html=
            "        <p>I spent two evenings searching. Not a name search — a trace. The name does not appear in any context that would make a person real: no social profiles, no news, no professional registry. What it does appear in is indirect. A dissolved business registration from eleven years ago, filed in a state where I have no connections, with an address that no longer exists as a commercial property. A cached index entry from a forum that was taken down six years before I had any reason to search for it, showing the name in a thread title, body not recoverable. A single line in a PDF from a municipal planning document, listed as a contact for a project that was approved and then apparently never built.</p>\n"
            "        <p>Each trace has been partially erased. Not completely. Partial erasure is harder to do correctly than complete erasure, and partial erasure leaves the shape of what was there. Whatever process cleaned this up did not do a thorough job, or did not expect someone to be looking for the seams between what was removed and what was left. I am looking for the seams.</p>\n"
            "        <p>The business registration address corresponds to a block in a part of the city I know. I have been in that area. I have documented things in that area. I am not going to say which area until I have been there again with that knowledge and confirmed what I think I am going to confirm.</p>\n",
        stamp="name traced // three partial records // location pending // 2026-03-18",
        depth=2,
    )

    orbitmap_d2_html = make_junction_node(
        node_slug=orbitmap_d2_slug,
        entry_title="orbit map // the name // initial research",
        posted_date=POSTED_DATE,
        depth=2,
        parent_href="../nodes/node-orbit-map-d1-20260317.html",
        content_html=orbitmap_d2_body,
        data_motif="name,research,trace,dissolved,registration,cached,partial,erasure,seam,location,address,building",
    )

    # d3: junction node — address visit
    orbitmap_d3_img_html = ""
    if img_orbitmap_d3:
        orbitmap_d3_img_html = (
            f'      <figure class="evidence">'
            f'<img src="../{_esc(img_orbitmap_d3)}" alt="building documentation"></figure>\n'
        )
    orbitmap_d3_body = inline_block(
        entry_id="orbit-map-address-visit",
        title="the address // what the directory gave",
        body_html=
            "        <p>I drove past it this morning. I did not stop. Stopping would change things and I did not want to change things until I understood what I was looking at. I drove past it twice — once in each direction — and I took the photograph from the second pass, from the passenger window with my arm extended, looking at the road. I have learned not to look at what I am photographing. It changes the posture and the posture is the tell.</p>\n"
            "        <p>The building is not what I expected. I expected something more anonymous — a light industrial unit, a generic commercial front. What is there is older, mixed-use, the kind of property that has had ten different tenants over thirty years and carries marks from all of them. The signage is generic and recent. The mailbox cluster at the entrance is the kind with individual keys, which means mail goes to specific units, which means the business registration was for something that existed there in a traceable way.</p>\n"
            "        <p>The area I recognized from the documentation. It is two blocks from one of the map pins that was removed. I do not think that is a coincidence. I have stopped believing in coincidences at this level of specificity. The removed pin pointed to something, and what it pointed to is now attached to this name.</p>\n",
        stamp="location confirmed // two blocks from removed pin // 2026-03-18",
        depth=3,
        img_web_path=f"../{img_orbitmap_d3}" if img_orbitmap_d3 else "",
    )

    orbitmap_d3_html = make_junction_node(
        node_slug=orbitmap_d3_slug,
        entry_title="orbit map // the address // documentation",
        posted_date=POSTED_DATE,
        depth=3,
        parent_href=f"../{orbitmap_d2_slug}.html",
        content_html=orbitmap_d3_body,
        data_motif="address,building,visit,location,map,pin,removed,block,signage,mailbox,documented,photograph",
    )

    # d4: terminal — perimeter recognition
    orbitmap_d4_img_section = ""
    if img_orbitmap_d4:
        orbitmap_d4_img_section = (
            f'    <figure class="evidence">\n'
            f'      <img src="../{_esc(img_orbitmap_d4)}" alt="perimeter documentation">\n'
            f'    </figure>\n'
        )
    orbitmap_d4_content = (
        f'    <div class="wire-body">\n'
        f'      <p>The thing that stopped me when I reviewed the photographs is the sightline. From the corner of that building, standing at the entrance in the position the clipboard man stands during the perimeter walk I have documented, you can see into the lower lot. Not just the lower lot of that building. The lower lot of mine. I measured the angle against the satellite image and the sightline is unobstructed for the relevant distance. He was not walking the perimeter of one property. He was walking the perimeter of both at once, using the position of the first to observe the second.</p>\n'
        f'      <p>I have been watching one end of a surveillance corridor. The building I found is the other end. The name on the dissolved registration is whoever set up the corridor, or whoever owns whatever is on the other end of it. I do not know which. What I know is that the diagram in the glove box — bilateral, escalating, a structure that reads the same forward and backward — is a map of something with two reference points. I have both points now.</p>\n'
        f'      <p>I am not documenting the specifics of the sightline here. Not the distances, not the degrees. Not because I do not have them — I have them — but because this documentation is itself visible, and there is no value in making it easy to understand what I have figured out before I decide what to do with it. The sightline exists. I have confirmed it. The thread ends here, not because there is nothing more, but because what comes next is not documentation.</p>\n'
        f'    </div>\n'
        f'{orbitmap_d4_img_section}'
    )

    orbitmap_d4_html = make_terminal_node(
        node_slug=orbitmap_d4_slug,
        entry_title="orbit map // the building // same perimeter",
        posted_date=POSTED_DATE,
        depth=4,
        parent_href=f"../{orbitmap_d3_slug}.html",
        content_html=orbitmap_d4_content,
        data_motif="perimeter,sightline,building,corridor,surveillance,angle,diagram,bilateral,two,points,confirmed",
        footer_note="both reference points confirmed // what comes next is not documentation",
    )

    if not dry_run:
        orbitmap_d2_path.write_text(orbitmap_d2_html, encoding="utf-8")
        orbitmap_d3_path.write_text(orbitmap_d3_html, encoding="utf-8")
        orbitmap_d4_path.write_text(orbitmap_d4_html, encoding="utf-8")
        print(f"  created: {orbitmap_d2_path.name}")
        print(f"  created: {orbitmap_d3_path.name}")
        print(f"  created: {orbitmap_d4_path.name}")

        # Update node-orbit-map-d1: add link card and LINKS entry pointing to d2
        orbitmap_d1_path = NODES_DIR / "node-orbit-map-d1-20260317.html"
        d2_card = link_card_html(
            entry_id="orbit-map-d2-research",
            title="the name // initial research",
            teaser="partial traces // dissolved registration // location pending",
            dest_href=f"../{orbitmap_d2_slug}.html",
            depth=1,
        )
        insert_into_cascade(orbitmap_d1_path, d2_card)
        insert_into_links(
            orbitmap_d1_path,
            f'        <li><a href="../{orbitmap_d2_slug}.html">{orbitmap_d2_slug}</a> (deeper: the name // initial research)</li>\n',
        )
        # Add nav-down to footer
        patch_footer_add_nav(
            orbitmap_d1_path,
            f'      <p class="nav-down"><a href="../{orbitmap_d2_slug}.html">[down] the name // initial research</a></p>',
        )
        print(f"  patched: node-orbit-map-d1-20260317.html")

    created_pages += [orbitmap_d2_path.name, orbitmap_d3_path.name, orbitmap_d4_path.name]
    report.append(("orbit-map", "node-orbit-map-d1-20260317", 1, 4, 3))

    # Branch log entries
    for slug, title, depth, action, dest in [
        (orbitmap_d2_slug, "orbit map // the name // initial research", 1, "link", f"nodes/{orbitmap_d2_slug}.html"),
        (orbitmap_d2_slug, "orbit map // the name // initial research", 2, "inline", f"nodes/{orbitmap_d2_slug}.html"),
        (orbitmap_d3_slug, "orbit map // the address // documentation", 3, "inline", f"nodes/{orbitmap_d3_slug}.html"),
        (orbitmap_d4_slug, "orbit map // the building // same perimeter", 4, "inline", f"nodes/{orbitmap_d4_slug}.html"),
    ]:
        append_log(log, {
            "entry_id": f"deepen-{slug}",
            "title": title,
            "depth": depth,
            "roll": 0 if action == "link" else 1,
            "action": action,
            "dest_page": dest,
            "dest_type": "node",
            "orientation": "vertical",
            "timestamp_utc": "2026-03-18T23:59:00Z",
            "posted_date": POSTED_DATE,
            "note": "deepening migration: +3 layers from node-orbit-map-d1",
        })

    # ═══════════════════════════════════════════════════════════════════════
    # BRANCH 2: freq-signal
    # deepest: nodes/node-freq-signal-d2-20260317.html (depth=2)
    # extend: d3, d4, d5
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[2/6] freq-signal branch (depth=2 → depth=5)")

    img_freqsig_d5 = _copy_library_image("Mobile-phone-PHS-Japan-1997-2003.jpg") if not dry_run else "project/assets/web/Mobile-phone_DRY.jpg"

    freqsig_d3_slug = "node-freq-signal-d3-20260318"
    freqsig_d4_slug = "node-freq-signal-d4-20260318"
    freqsig_d5_slug = "node-freq-signal-d5-20260318"

    freqsig_d3_path = NODES_DIR / f"{freqsig_d3_slug}.html"
    freqsig_d4_path = NODES_DIR / f"{freqsig_d4_slug}.html"
    freqsig_d5_path = NODES_DIR / f"{freqsig_d5_slug}.html"

    # d3: junction — hosting failure
    freqsig_d3_body = inline_block(
        entry_id="freq-signal-hosting-failure",
        title="hosting // the solution and its failure",
        body_html=
            "        <p>I found a relay that did not require account creation. No login, no email, no registration. You upload a file and you get a URL and the relay keeps no logs, or claims to keep no logs, which is a distinction that matters but that I could not verify without access I do not have. I used it anyway because the alternative was keeping the waveform export on local storage only, and local storage can be physically taken. I uploaded the file and sent the link to two people I trust. I did not describe what was in it. I said: listen to the whole thing, at the nine-second mark, tell me what you hear.</p>\n"
            "        <p>Within thirty-six hours the link returned a 404. Not an expired link — the relay does not expire links on that schedule. Not a storage limit — I checked the relay's stated limits and the file was well within them. The server returned 404 as if the file had never been uploaded. I checked the URL I had saved. I checked the relay's interface, which shows recent uploads. The upload was not in the list.</p>\n"
            "        <p>The two people I sent the link to both said they had not yet listened to it. One of them tried the link after I reported the 404 and confirmed it was dead. Neither of them had downloaded it. That means whoever retrieved the file — if retrieval was what triggered the deletion — was a third party. Whatever monitors for the presence of this waveform in accessible locations was faster than either of my contacts and knew the file was there before they did.</p>\n",
        stamp="waveform uploaded // 36 hours // link dead // third party retrieval suspected // 2026-03-18",
        depth=3,
    )

    freqsig_d3_html = make_junction_node(
        node_slug=freqsig_d3_slug,
        entry_title="signal thread // hosting failure",
        posted_date=POSTED_DATE,
        depth=3,
        parent_href="../nodes/node-freq-signal-d2-20260317.html",
        content_html=freqsig_d3_body,
        data_motif="hosting,relay,upload,file,waveform,link,deleted,404,third,party,retrieval,monitor",
    )

    # d4: junction — structural analysis
    freqsig_d4_body = inline_block(
        entry_id="freq-signal-structure-analysis",
        title="structure analysis // the interval is not noise",
        body_html=
            "        <p>If the artifact were hardware failure — connector noise, thermal dropout, power fluctuation — then the interval would drift. Any physical failure mode that repeats has a tolerance range. Mechanical repetition accumulates error over time; the interval would be nine seconds on the first occurrence and nine-point-three or eight-point-eight on the third. It is nine, exactly, all three times. The only way to get that without drift is if the source of the artifact is not physical hardware. It is either digital — a process that fires on a precise timer — or it is a signal that was in the original audio environment when the recording was made.</p>\n"
            "        <p>A process firing on a precise timer would be software — something running on the recording device or on a network it was connected to. The recording device was in airplane mode. I checked the session logs after the fact. No network connections during the recording window. If it is software it is software installed on the device that runs offline. I have since factory reset the device. I cannot test that hypothesis on the original hardware anymore.</p>\n"
            "        <p>If the signal was in the audio environment — broadcast, physical, ambient — then something in the space where I was recording was producing a structured nine-second signal. I was in a parked car on a public street. I do not know what was producing that signal or why it would be structured. I know that structured signals in that frequency band require infrastructure to transmit. Infrastructure requires resources. Resources require decisions about what to monitor and when.</p>\n",
        stamp="interval analysis complete // three hypotheses // one eliminated // infrastructure implied // 2026-03-18",
        depth=4,
    )

    freqsig_d4_html = make_junction_node(
        node_slug=freqsig_d4_slug,
        entry_title="signal thread // structure analysis",
        posted_date=POSTED_DATE,
        depth=4,
        parent_href=f"../{freqsig_d3_slug}.html",
        content_html=freqsig_d4_body,
        data_motif="structure,interval,drift,hardware,digital,timer,signal,broadcast,infrastructure,frequency,monitor",
    )

    # d5: terminal — other recordings
    freqsig_d5_img_section = ""
    if img_freqsig_d5:
        freqsig_d5_img_section = (
            f'    <figure class="evidence">\n'
            f'      <img src="../{_esc(img_freqsig_d5)}" alt="recording device documentation">\n'
            f'    </figure>\n'
        )

    freqsig_d5_content = (
        f'    <div class="wire-body">\n'
        f'      <p>I went back through the recordings I made in the same month. Not systematically at first — just listening. Then I noticed it in the ambient recording I made at the bus stop: a small dropout at a point I had never paid attention to because it was not the recording I was analyzing. I pulled the waveform. The dropout is at nine seconds from a different reference point. Not nine seconds from the start of the recording. Nine seconds from a prior artifact I had not noticed on the first pass — a smaller one, the kind you ignore as a mic imperfection.</p>\n'
        f'      <p>I checked two other recordings from that period. One of them had the same structure: a small artifact, then nine seconds later, the same dropout-type signature that the speech-to-text resolves to the same name. The second recording had neither artifact. It was made indoors, with a different device, in a different location. The pattern is in the outdoor recordings, on specific devices, in specific locations.</p>\n'
        f'      <p>What this means: the signal was not in a single recording. The signal was in the environment at those locations, during those dates. Multiple recordings, multiple devices, same structured pattern. The source was ambient and location-specific. Not hardware failure. Not software. Something in the air at specific points in the city during a specific window, producing a structured nine-second signal that four different speech-to-text passes and two different recording devices all resolve to the same name. I do not have a hypothesis for this that I am comfortable documenting publicly. I have the recordings. They are not uploaded anywhere.</p>\n'
        f'    </div>\n'
        f'{freqsig_d5_img_section}'
    )

    freqsig_d5_html = make_terminal_node(
        node_slug=freqsig_d5_slug,
        entry_title="signal thread // the same interval // other recordings",
        posted_date=POSTED_DATE,
        depth=5,
        parent_href=f"../{freqsig_d4_slug}.html",
        content_html=freqsig_d5_content,
        data_motif="recordings,ambient,location,outdoor,devices,signal,environment,pattern,consistent,name,hypothesis",
        footer_note="ambient signal // multiple recordings // same name // recordings not uploaded",
    )

    if not dry_run:
        freqsig_d3_path.write_text(freqsig_d3_html, encoding="utf-8")
        freqsig_d4_path.write_text(freqsig_d4_html, encoding="utf-8")
        freqsig_d5_path.write_text(freqsig_d5_html, encoding="utf-8")
        print(f"  created: {freqsig_d3_path.name}")
        print(f"  created: {freqsig_d4_path.name}")
        print(f"  created: {freqsig_d5_path.name}")

        freqsig_d2_path = NODES_DIR / "node-freq-signal-d2-20260317.html"
        d3_card = link_card_html(
            entry_id="freq-signal-d3-hosting",
            title="hosting // the solution and its failure",
            teaser="relay upload // 36 hours // 404 // third party suspected",
            dest_href=f"../{freqsig_d3_slug}.html",
            depth=2,
        )
        insert_into_cascade(freqsig_d2_path, d3_card)
        insert_into_links(
            freqsig_d2_path,
            f'        <li><a href="../{freqsig_d3_slug}.html">{freqsig_d3_slug}</a> (deeper: hosting failure // third party retrieval)</li>\n',
        )
        patch_footer_add_nav(
            freqsig_d2_path,
            f'      <p class="nav-down"><a href="../{freqsig_d3_slug}.html">[down] hosting // the solution and its failure</a></p>',
        )
        print(f"  patched: node-freq-signal-d2-20260317.html")

    created_pages += [freqsig_d3_path.name, freqsig_d4_path.name, freqsig_d5_path.name]
    report.append(("freq-signal", "node-freq-signal-d2-20260317", 2, 5, 3))

    for slug, title, depth, action in [
        (freqsig_d3_slug, "signal thread // hosting failure", 2, "link"),
        (freqsig_d3_slug, "signal thread // hosting failure", 3, "inline"),
        (freqsig_d4_slug, "signal thread // structure analysis", 4, "inline"),
        (freqsig_d5_slug, "signal thread // the same interval // other recordings", 5, "inline"),
    ]:
        append_log(log, {
            "entry_id": f"deepen-{slug}",
            "title": title,
            "depth": depth,
            "roll": 0 if action == "link" else 1,
            "action": action,
            "dest_page": f"nodes/{slug}.html",
            "dest_type": "node",
            "orientation": "vertical",
            "timestamp_utc": "2026-03-18T23:59:01Z",
            "posted_date": POSTED_DATE,
            "note": "deepening migration: +3 layers from node-freq-signal-d2",
        })

    # ═══════════════════════════════════════════════════════════════════════
    # BRANCH 3: clipboard-man
    # deepest: nodes/node-clipboard-man-d3-20260318.html (depth=3)
    # extend: d4, d5, d6
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[3/6] clipboard-man branch (depth=3 → depth=6)")

    img_clipman_d5 = _copy_library_image("Office-space-at-Dearborn-Drug-and-Chemical-Works-facility---DPLA---15d19e3f71478f4491c0827652b60793.jpg") if not dry_run else "project/assets/web/Office_DRY.jpg"

    clipman_d4_slug = "node-clipboard-man-d4-20260318"
    clipman_d5_slug = "node-clipboard-man-d5-20260318"
    clipman_d6_slug = "node-clipboard-man-d6-20260318"

    clipman_d4_path = NODES_DIR / f"{clipman_d4_slug}.html"
    clipman_d5_path = NODES_DIR / f"{clipman_d5_slug}.html"
    clipman_d6_path = NODES_DIR / f"{clipman_d6_slug}.html"

    # d4: junction — fifth occurrence, absence
    clipman_d4_body = inline_block(
        entry_id="clipboard-man-fifth-absence",
        title="the fifth occurrence // he was not there",
        body_html=
            "        <p>I went back on seven consecutive days expecting the pattern to continue. He was not there. Not on any of the seven days, not at the times I had previously documented, not at other times I checked. The absence began the same week this documentation went live. I have been trying to think through that coincidence carefully because it is easy to draw the wrong connection and I do not want to document something I cannot support.</p>\n"
            "        <p>Two interpretations. First: the documentation becoming visible triggered a change in protocol — whoever is running this understood that documentation creates risk, and withdrew the most visible element. The clipboard man is a high-visibility asset. You do not keep running a high-visibility asset once the target has identified and documented it. You pull it and replace it with something lower-profile, or you wait until the documentation is no longer being actively updated, or you assess whether the target is still worth the exposure.</p>\n"
            "        <p>Second: the cessation of the perimeter walk is itself a signal. It is a demonstration that the walk can be started and stopped on command — that what I was watching was not a fixed behavior but a controlled one. The cessation says: we know you were watching. We are showing you we can stop whenever we choose. The documentation did not expose a vulnerability. It confirmed the capability.</p>\n"
            "        <p>I do not know which interpretation is correct. I know that in either case, the absence is not neutral. The absence is information.</p>\n",
        stamp="seven consecutive days // absence documented // two interpretations // neither is good // 2026-03-18",
        depth=4,
    )

    clipman_d4_html = make_junction_node(
        node_slug=clipman_d4_slug,
        entry_title="clipboard man // the fifth occurrence // absence",
        posted_date=POSTED_DATE,
        depth=4,
        parent_href="../nodes/node-clipboard-man-d3-20260318.html",
        content_html=clipman_d4_body,
        data_motif="absence,cessation,protocol,documentation,visible,asset,withdrawal,signal,capability,control",
    )

    # d5: junction — the form
    clipman_d5_img_html_cascade = ""
    if img_clipman_d5:
        clipman_d5_img_html_cascade = (
            f'      <figure class="evidence">'
            f'<img src="../{_esc(img_clipman_d5)}" alt="form documentation"></figure>\n'
        )

    clipman_d5_body = (
        f'    <!-- branch: inline  depth=5  seed=1  orient=vertical -->\n'
        f'    <section class="cascade-block cascade-rich cp-a cascade-orient-vertical"\n'
        f'      data-entry="clipboard-man-form-analysis"\n'
        f'      data-type="inline"\n'
        f'      data-depth="5"\n'
        f'      data-branch-seed="1"\n'
        f'      data-orientation="vertical">\n'
        f'      <h2>the form // what was on the clipboard</h2>\n'
        f'      <div class="wire-body">\n'
        f'        <p>On the third occurrence I was close enough that I photographed toward him with the camera held low, aimed at the clipboard rather than his face. I was thinking at the time about the face — I was trying to get the face — and I did not look at what the camera had actually captured until weeks later when I was reviewing the documentation systematically. The face is obscured. The clipboard is in focus.</p>\n'
        f'        <p>I have been doing image enhancement incrementally, not all at once, because I did not want the frustration of a single failed attempt to make me dismiss what might be there with better patience. What is there, after four enhancement passes using two different tools, is a grid form. The columns are not all readable. Three of them are partially readable. The leftmost column appears to be unit numbers or identifiers — four-digit strings, partially legible, that do not match any postal format I recognize. The middle column appears to be dates and times. The rightmost readable column has a header I can now read clearly enough to transcribe: STATUS.</p>\n'
        f'        <p>Under STATUS, the entries I can partially read: most are illegible. One, in a row that corresponds to a date I can make out as approximately eleven days before the fourth occurrence, reads ACTIVE. I cannot confirm the row is for a unit I am connected to. I cannot confirm the form is what I think it is. I am documenting what I can read, nothing more.</p>\n'
        f'      </div>\n'
        f'{clipman_d5_img_html_cascade}'
        f'      <p class="stamp">form partially recovered // STATUS column // ACTIVE entry // 2026-03-18</p>\n'
        f'    </section>\n\n'
    )

    clipman_d5_html = make_junction_node(
        node_slug=clipman_d5_slug,
        entry_title="clipboard man // the form // status column",
        posted_date=POSTED_DATE,
        depth=5,
        parent_href=f"../{clipman_d4_slug}.html",
        content_html=clipman_d5_body,
        data_motif="form,clipboard,column,status,active,unit,identifier,grid,enhanced,photograph,recovered",
    )

    # d6: terminal — what ACTIVE means
    clipman_d6_content = (
        f'    <div class="wire-body">\n'
        f'      <p>I have been sitting with the word ACTIVE for longer than I should have. The problem with the word ACTIVE in the context of what I think I am looking at is that it does not resolve into a comfortable category. Active documentation subject. Active monitoring priority. Active threat assessment. Active — as opposed to closed, suspended, dormant, or terminated. Each alternative means something different about what is happening and what the progression looks like.</p>\n'
        f'      <p>The thing I keep coming back to is the timing. The entry I believe corresponds to ACTIVE predates the first occurrence I documented. That means I was listed before I started documenting. The documentation did not create the record. The record predates the documentation. Whatever I am on a list of, I was put there before I understood there was a list.</p>\n'
        f'      <p>I have been trying to think about whether documenting this changes anything. The honest answer is that it does not change the status. The status is the status. What documentation does is create a parallel record — one that exists outside of their system, in a form they did not author, that they cannot edit. The STATUS column says ACTIVE. This documentation says: I know it says ACTIVE. I know it said ACTIVE before I started watching. I am watching now. Whatever ACTIVE implies for their process, my process is also active. Both records will persist.</p>\n'
        f'    </div>\n'
    )

    clipman_d6_html = make_terminal_node(
        node_slug=clipman_d6_slug,
        entry_title="clipboard man // the status column // active",
        posted_date=POSTED_DATE,
        depth=6,
        parent_href=f"../{clipman_d5_slug}.html",
        content_html=clipman_d6_content,
        data_motif="status,active,record,list,documentation,parallel,predates,first,watching,process,persist",
        footer_note="status: active // record predates documentation // both records persist",
    )

    if not dry_run:
        clipman_d4_path.write_text(clipman_d4_html, encoding="utf-8")
        clipman_d5_path.write_text(clipman_d5_html, encoding="utf-8")
        clipman_d6_path.write_text(clipman_d6_html, encoding="utf-8")
        print(f"  created: {clipman_d4_path.name}")
        print(f"  created: {clipman_d5_path.name}")
        print(f"  created: {clipman_d6_path.name}")

        clipman_d3_path = NODES_DIR / "node-clipboard-man-d3-20260318.html"
        d4_card = link_card_html(
            entry_id="clipboard-man-d4-absence",
            title="the fifth occurrence // he was not there",
            teaser="seven days // absence // protocol change or demonstration",
            dest_href=f"../{clipman_d4_slug}.html",
            depth=3,
        )
        insert_into_cascade(clipman_d3_path, d4_card)
        insert_into_links(
            clipman_d3_path,
            f'        <li><a href="../{clipman_d4_slug}.html">{clipman_d4_slug}</a> (deeper: the fifth occurrence // absence documentation)</li>\n',
        )
        patch_footer_add_nav(
            clipman_d3_path,
            f'      <p class="nav-down"><a href="../{clipman_d4_slug}.html">[down] the fifth occurrence // absence</a></p>',
        )
        print(f"  patched: node-clipboard-man-d3-20260318.html")

    created_pages += [clipman_d4_path.name, clipman_d5_path.name, clipman_d6_path.name]
    report.append(("clipboard-man", "node-clipboard-man-d3-20260318", 3, 6, 3))

    for slug, title, depth, action in [
        (clipman_d4_slug, "clipboard man // the fifth occurrence // absence", 3, "link"),
        (clipman_d4_slug, "clipboard man // the fifth occurrence // absence", 4, "inline"),
        (clipman_d5_slug, "clipboard man // the form // status column", 5, "inline"),
        (clipman_d6_slug, "clipboard man // the status column // active", 6, "inline"),
    ]:
        append_log(log, {
            "entry_id": f"deepen-{slug}",
            "title": title,
            "depth": depth,
            "roll": 0 if action == "link" else 1,
            "action": action,
            "dest_page": f"nodes/{slug}.html",
            "dest_type": "node",
            "orientation": "vertical",
            "timestamp_utc": "2026-03-18T23:59:02Z",
            "posted_date": POSTED_DATE,
            "note": "deepening migration: +3 layers from node-clipboard-man-d3",
        })

    # ═══════════════════════════════════════════════════════════════════════
    # BRANCH 4: the-feed-shows
    # deepest: nodes/node-the-feed-shows-t-d0-20260318-161902.html (depth=1)
    # This is a content node (no CASCADE block); extend by adding a link + d2, d3, d4
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[4/6] the-feed-shows branch (depth=1 → depth=4)")

    feed_d2_slug = "node-feed-shows-d2-20260318"
    feed_d3_slug = "node-feed-shows-d3-20260318"
    feed_d4_slug = "node-feed-shows-d4-20260318"

    feed_d2_path = NODES_DIR / f"{feed_d2_slug}.html"
    feed_d3_path = NODES_DIR / f"{feed_d3_slug}.html"
    feed_d4_path = NODES_DIR / f"{feed_d4_slug}.html"

    img_feed_d4 = _copy_library_image("Billboard-reading-Illinois-Democrats-Legalized-Marijuana-.jpg") if not dry_run else "project/assets/web/Billboard_DRY.jpg"

    # d2: junction — response / what it wants
    feed_d2_body = inline_block(
        entry_id="feed-shows-response",
        title="the response // what it wants from me",
        body_html=
            "        <p>I have been blocking the accounts. Every one I can identify that shows the content. It does not help. I block one and by the next session there are three more showing the same material from different accounts, different names, different profile images, but the same category of content, the same framing, the same tone that makes it sound acceptable and normal. The feed is not responding to my blocks. The feed is demonstrating that the blocks do not matter. I am being shown this material regardless of my actions to prevent it. That is not how blocking works if the feed is random. That is how it works if someone is making sure I see it.</p>\n"
            "        <p>I have started screenshotting every instance. I have a folder now. The folder has forty-seven entries as of this writing. I am not posting the screenshots here because the images themselves are part of what is being used against me and I do not want to give them more surface area. I have them as documentation. I have the timestamps. The timestamps cluster around specific hours — late afternoon, after ten at night — which is when I am most likely to be on the device. Whoever is timing this knows my usage patterns.</p>\n"
            "        <p>What I have been asking is what it wants me to DO. Bearing witness is not sufficient. A signal that demands no response is not a signal; it is noise with ambition. There is something that bearing witness is meant to lead to and I have not yet identified what that is. I am documenting while I figure it out.</p>\n",
        stamp="forty-seven screenshots // timestamps clustered // blocking does not work // response pending // 2026-03-18",
        depth=2,
    )

    feed_d2_html = make_junction_node(
        node_slug=feed_d2_slug,
        entry_title="the feed shows truths // the response",
        posted_date=POSTED_DATE,
        depth=2,
        parent_href="../nodes/node-the-feed-shows-t-d0-20260318-161902.html",
        content_html=feed_d2_body,
        data_motif="feed,block,accounts,screenshots,timestamps,pattern,usage,signal,response,witness,documentation",
    )

    # d3: junction — the list
    feed_d3_body = inline_block(
        entry_id="feed-shows-list",
        title="the list // names I can identify",
        body_html=
            "        <p>I have started cataloging the accounts that appear repeatedly. Not all of them — there is too much volume for complete cataloging — but the ones that recur across multiple sessions, the faces and usernames that show up again and again in the feed despite my blocks. I have cross-referenced these across the screenshots and I have found eleven accounts that appear in at least three separate screenshots taken on different days. Eleven accounts that the block mechanism is either not applying to or is applying to and then reversing without my input.</p>\n"
            "        <p>I have written these down in the notebook. Not the content, just the identifiers — the usernames, the account creation patterns where I can infer them, the geographic signals in the content where they are visible. This is a map. A map of who is being used to deliver the material and, if I can confirm the connections, possibly who is coordinating the delivery.</p>\n"
            "        <p>I know that someone will read this and think: eleven accounts on a social media platform is not evidence of coordination. That is exactly what someone would think if they had not been watching this for six weeks. I have been watching it for six weeks. The eleven accounts do not behave like eleven independent users who happen to post similar content. They behave like eleven distribution nodes on a network with a single source. I believe I am being observed doing this analysis. The observation of analysis is itself a message.</p>\n",
        stamp="eleven recurring accounts // cross-referenced // notebook entry // network hypothesis // 2026-03-18",
        depth=3,
    )

    feed_d3_html = make_junction_node(
        node_slug=feed_d3_slug,
        entry_title="the feed shows truths // the list",
        posted_date=POSTED_DATE,
        depth=3,
        parent_href=f"../{feed_d2_slug}.html",
        content_html=feed_d3_body,
        data_motif="list,accounts,recurring,cross-reference,network,distribution,coordination,notebook,analysis,observed",
    )

    # d4: terminal — the pattern / loop closes
    feed_d4_img_section = ""
    if img_feed_d4:
        feed_d4_img_section = (
            f'    <figure class="evidence">\n'
            f'      <img src="../{_esc(img_feed_d4)}" alt="signal documentation">\n'
            f'    </figure>\n'
        )

    feed_d4_content = (
        f'    <div class="wire-body">\n'
        f'      <p>One of the eleven accounts posted at the exact moment I opened the application on a day when I had not planned to open it. I had picked up the device for an unrelated reason — checking the time — and I opened the feed reflexively, the way you do when the device is already in your hand. The post from that account was timestamped 14 seconds before I opened the application. Fourteen seconds. I was not online fourteen seconds before that. I did not announce I was going to open the application. The post preceded my arrival by less than a quarter of a minute.</p>\n'
        f'      <p>There are two ways to interpret this. First, the timing is coincidence and the post would have appeared whenever I opened the application next. Second, the post was timed to my session initiation — to some signal that I was about to open the application before I opened it. The second interpretation requires either passive access to my device activity or something that reads the behavioral pattern and predicts the session with enough advance time to post.</p>\n'
        f'      <p>I have started reporting the accounts as well as blocking them. I have submitted reports on nine of the eleven. I have received automated responses saying my reports were reviewed and the content did not violate platform rules. The content is not the point. The content was never the point. The platform sees content. I see a channel. These are not the same thing and the platform\'s review process was not designed to tell the difference. I am the only one reviewing this as a channel. The loop is closed. I am inside it.</p>\n'
        f'    </div>\n'
        f'{feed_d4_img_section}'
    )

    feed_d4_html = make_terminal_node(
        node_slug=feed_d4_slug,
        entry_title="the feed shows truths // the pattern // closed loop",
        posted_date=POSTED_DATE,
        depth=4,
        parent_href=f"../{feed_d3_slug}.html",
        content_html=feed_d4_content,
        data_motif="timing,session,post,fourteen,seconds,predict,channel,loop,reports,platform,review,device,behavioral",
        footer_note="loop closed // inside it now // platform sees content // I see a channel",
    )

    if not dry_run:
        feed_d2_path.write_text(feed_d2_html, encoding="utf-8")
        feed_d3_path.write_text(feed_d3_html, encoding="utf-8")
        feed_d4_path.write_text(feed_d4_html, encoding="utf-8")
        print(f"  created: {feed_d2_path.name}")
        print(f"  created: {feed_d3_path.name}")
        print(f"  created: {feed_d4_path.name}")

        # Patch the content node (no CASCADE block — insert before footer)
        feed_root_path = NODES_DIR / "node-the-feed-shows-t-d0-20260318-161902.html"
        link_section = (
            f'\n    <!-- branch: link  depth=1  seed=0  orient=vertical -->\n'
            f'    <section class="cascade-block cascade-node cp-b cascade-orient-vertical"\n'
            f'      data-entry="feed-shows-d2-response"\n'
            f'      data-type="link"\n'
            f'      data-depth="1"\n'
            f'      data-branch-seed="0"\n'
            f'      data-orientation="vertical">\n'
            f'      <h2>the response // what it wants from me</h2>\n'
            f'      <span class="lean-link">'
            f'<a href="../{feed_d2_slug}.html">open node</a>'
            f' <span class="stamp">// forty-seven screenshots // usage pattern known // response pending</span></span>\n'
            f'      <p class="stamp">posted: {POSTED_DATE}</p>\n'
            f'    </section>\n\n'
            f'    <div class="node-threads">\n'
            f'      <h4>threads // from this node</h4>\n'
            f'      <ul>\n'
            f'        <li><a href="../{feed_d2_slug}.html">{feed_d2_slug}</a> (deeper: the response // blocks and screenshots)</li>\n'
            f'      </ul>\n'
            f'    </div>\n'
        )
        insert_before_footer_close(feed_root_path, link_section)
        patch_footer_add_nav(
            feed_root_path,
            f'      <p class="nav-down"><a href="../{feed_d2_slug}.html">[down] the response // what it wants from me</a></p>',
        )
        print(f"  patched: node-the-feed-shows-t-d0-20260318-161902.html")

    created_pages += [feed_d2_path.name, feed_d3_path.name, feed_d4_path.name]
    report.append(("the-feed-shows", "node-the-feed-shows-t-d0-20260318-161902", 1, 4, 3))

    for slug, title, depth, action in [
        (feed_d2_slug, "the feed shows truths // the response", 1, "link"),
        (feed_d2_slug, "the feed shows truths // the response", 2, "inline"),
        (feed_d3_slug, "the feed shows truths // the list", 3, "inline"),
        (feed_d4_slug, "the feed shows truths // the pattern // closed loop", 4, "inline"),
    ]:
        append_log(log, {
            "entry_id": f"deepen-{slug}",
            "title": title,
            "depth": depth,
            "roll": 0 if action == "link" else 1,
            "action": action,
            "dest_page": f"nodes/{slug}.html",
            "dest_type": "node",
            "orientation": "vertical",
            "timestamp_utc": "2026-03-18T23:59:03Z",
            "posted_date": POSTED_DATE,
            "note": "deepening migration: +3 layers from node-the-feed-shows-t",
        })

    # ═══════════════════════════════════════════════════════════════════════
    # BRANCH 5: sighting-0002
    # deepest: fragments/sighting-0002.html (depth=1 terminal inline)
    # extend: d2, d3, d4  — adds CASCADE section before footer
    # ═══════════════════════════════════════════════════════════════════════
    print("\n[5/6] sighting-0002 branch (depth=1 → depth=4)")

    sight2_d2_slug = "node-sighting-0002-d2-20260318"
    sight2_d3_slug = "node-sighting-0002-d3-20260318"
    sight2_d4_slug = "node-sighting-0002-d4-20260318"

    sight2_d2_path = NODES_DIR / f"{sight2_d2_slug}.html"
    sight2_d3_path = NODES_DIR / f"{sight2_d3_slug}.html"
    sight2_d4_path = NODES_DIR / f"{sight2_d4_slug}.html"

    # d2: junction — sixth occurrence, different neighborhood, phone prop
    sight2_d2_body = inline_block(
        entry_id="sighting-0002-sixth-occurrence",
        title="the sixth occurrence // different neighborhood",
        body_html=
            "        <p>The sixth time was in a part of the city I have no documented connection to. Not near the parking structure. Not near the route I use regularly. A street I had taken specifically because I was varying my movements after the fourth occurrence — an attempt to make my own pattern less predictable. He was there. On a street I chose at random, at a time I had not fixed in advance, I turned a corner and he was eighty meters away, walking at the same measured cadence, facing away from me.</p>\n"
            "        <p>The geographic break is significant. The first five occurrences could be explained, if you were committed to explaining them, by coincidence of routine. Same neighborhoods, same likely routes, same demographic patterns for the kind of person who walks that way in that area. The sixth occurrence cannot be explained that way. The sixth occurrence requires either that my spontaneous route selection was predicted or that the coverage area is larger than the clustering of the first five implied.</p>\n"
            "        <p>He was on the phone. Not talking — I watched for long enough to be sure of that. Ear against the device, mouth still, occasional small nod that could have been a response or could have been an affectation. At eighty meters, in the light conditions at that time, I could not resolve the device clearly enough to confirm it was a phone and not something smaller. I wrote it down as phone in the notes because phone is the most plausible interpretation. The less plausible interpretation is that he was holding a receiver or transmitter of a kind not commonly used by civilians.</p>\n",
        stamp="sixth occurrence // new neighborhood // spontaneous route // phone or device // 2026-03-18",
        depth=2,
    )

    sight2_d2_html = make_junction_node(
        node_slug=sight2_d2_slug,
        entry_title="sighting-0002 // sixth occurrence // new territory",
        posted_date=POSTED_DATE,
        depth=2,
        parent_href="../fragments/sighting-0002.html",
        content_html=sight2_d2_body,
        data_motif="sighting,sixth,neighborhood,route,coverage,phone,device,receiver,pattern,prediction,cadence",
    )

    # d3: junction — phone analysis, second prop
    sight2_d3_body = inline_block(
        entry_id="sighting-0002-phone-prop",
        title="the phone // the call that wasn't",
        body_html=
            "        <p>A phone call that produces no audible sound at eighty meters in open air. Not whispered — whispered speech at ten meters is audible with attention. Silent. The device held to the ear and the body producing none of the micro-gestures that accompany actual speech: no jaw movement, no throat movement, no shift of breath rhythm. You breathe differently when you are speaking. The body knows how to breathe around words. His body was not doing that. He was holding the device to his ear without speaking, for the duration I observed him, which was four minutes and some seconds before he cleared the corner.</p>\n"
            "        <p>The phone is the second prop. The clipboard is the first — it implies accountability without inviting questioning, because people with clipboards are presumed to be working and working people are presumed to have legitimate reason to be where they are. The phone is a deterrent to approach. A man on a call cannot be interrupted, or he can be interrupted only at social cost that most people are not willing to incur in public. The phone is a social barrier that he is wearing.</p>\n"
            "        <p>Two props in simultaneous use on one occasion would be unusual. I have not seen him use both at the same time. The clipboard has been consistent. The phone appeared in the sixth occurrence only. A possible interpretation: the sixth occurrence was a different kind of engagement than the previous five. The addition of the phone barrier suggests that on this occasion there was a specific reason to be less approachable than before. I do not know what that reason is. I know I was on a spontaneous route. I know he had upgraded his deterrent posture.</p>\n",
        stamp="phone as prop // second deterrent // jaw silence // breathing analysis // deterrent upgraded // 2026-03-18",
        depth=3,
    )

    sight2_d3_html = make_junction_node(
        node_slug=sight2_d3_slug,
        entry_title="sighting-0002 // the phone // deterrent posture",
        posted_date=POSTED_DATE,
        depth=3,
        parent_href=f"../{sight2_d2_slug}.html",
        content_html=sight2_d3_body,
        data_motif="phone,prop,call,deterrent,approach,social,barrier,clipboard,posture,upgraded,sixth,breathing",
    )

    # d4: terminal — the full toolkit documented
    sight2_d4_content = (
        f'    <div class="wire-body">\n'
        f'      <p>I have now documented four discrete elements of what I am calling the toolkit. The practiced turn: a deliberate body rotation that moves the face out of frame before the subject is close enough to photograph clearly. The clipboard: implies authorization and active purpose, discourages questioning, provides a surface for making visible notes that cannot be read at distance. The phone: social barrier against approach, provides plausible pretext for standing stationary without appearing to observe. The walking route: perimeter coverage that looks like transit but achieves surveillance, longer than any efficient path between two points in the area.</p>\n'
        f'      <p>Each element individually is explainable. A person might turn away. A property manager carries a clipboard. Anyone might be on a call. Someone might prefer a scenic route. The co-occurrence of all four elements, consistently, across six documented occurrences in different neighborhoods, is not explainable by the sum of the individual explanations. You do not accidentally train yourself to turn at exactly the right distance. You do not accidentally hold a clipboard at exactly the angle that prevents reading. These are trained behaviors. Training implies instruction. Instruction implies an instructing body.</p>\n'
        f'      <p>The toolkit is not improvised. It is a methodology. A methodology implies a standard. A standard implies that someone decided these behaviors constitute an appropriate approach for this kind of work. Someone decided that. The decision preceded my documentation by an unknown amount of time. The toolkit was developed for a purpose that existed before I started watching. I am documenting a methodology that was already fully formed when I encountered it. I am not watching it develop. I am watching it execute.</p>\n'
        f'    </div>\n'
    )

    sight2_d4_html = make_terminal_node(
        node_slug=sight2_d4_slug,
        entry_title="sighting-0002 // props and patterns // the toolkit",
        posted_date=POSTED_DATE,
        depth=4,
        parent_href=f"../{sight2_d3_slug}.html",
        content_html=sight2_d4_content,
        data_motif="toolkit,turn,clipboard,phone,route,methodology,trained,standard,instruction,execute,documented,developed",
        footer_note="four-element toolkit // methodology fully formed before documentation began",
    )

    if not dry_run:
        sight2_d2_path.write_text(sight2_d2_html, encoding="utf-8")
        sight2_d3_path.write_text(sight2_d3_html, encoding="utf-8")
        sight2_d4_path.write_text(sight2_d4_html, encoding="utf-8")
        print(f"  created: {sight2_d4_path.name}")

        # Patch sighting-0002.html to add link block before footer
        sight2_root = FRAGS_DIR / "sighting-0002.html"
        link_section = (
            f'\n    <!-- branch: link  depth=1  seed=0  orient=vertical -->\n'
            f'    <section class="cascade-block cascade-node cp-b cascade-orient-vertical"\n'
            f'      data-entry="sighting-0002-d2-sixth"\n'
            f'      data-type="link"\n'
            f'      data-depth="1"\n'
            f'      data-branch-seed="0"\n'
            f'      data-orientation="vertical">\n'
            f'      <h2>the sixth occurrence // new territory</h2>\n'
            f'      <span class="lean-link">'
            f'<a href="../nodes/{sight2_d2_slug}.html">open node</a>'
            f' <span class="stamp">// sixth occurrence // different neighborhood // route varied</span></span>\n'
            f'      <p class="stamp">posted: {POSTED_DATE}</p>\n'
            f'    </section>\n\n'
        )
        insert_before_footer_close(sight2_root, link_section)
        patch_footer_add_nav(
            sight2_root,
            f'      <p class="nav-down"><a href="../nodes/{sight2_d2_slug}.html">[down] the sixth occurrence // new territory</a></p>',
        )
        print(f"  patched: fragments/sighting-0002.html")

    created_pages += [sight2_d2_path.name, sight2_d3_path.name, sight2_d4_path.name]
    report.append(("sighting-0002", "fragments/sighting-0002.html", 1, 4, 3))

    for slug, title, depth, action in [
        (sight2_d2_slug, "sighting-0002 // sixth occurrence // new territory", 1, "link"),
        (sight2_d2_slug, "sighting-0002 // sixth occurrence // new territory", 2, "inline"),
        (sight2_d3_slug, "sighting-0002 // the phone // deterrent posture", 3, "inline"),
        (sight2_d4_slug, "sighting-0002 // props and patterns // the toolkit", 4, "inline"),
    ]:
        append_log(log, {
            "entry_id": f"deepen-{slug}",
            "title": title,
            "depth": depth,
            "roll": 0 if action == "link" else 1,
            "action": action,
            "dest_page": f"nodes/{slug}.html",
            "dest_type": "node",
            "orientation": "vertical",
            "timestamp_utc": "2026-03-18T23:59:04Z",
            "posted_date": POSTED_DATE,
            "note": "deepening migration: +3 layers from sighting-0002",
        })

    # ===========================================================================
    # BRANCH 6: street-log-01
    # deepest: fragments/street-log-01.html (depth=1 terminal)
    # extend: d2, d3, d4
    # ===========================================================================
    print("\n[6/6] street-log-01 branch (depth=1 -> depth=4)")

    img_streetlog_d3 = _copy_library_image("Denhaag-kunstwerk-intersection.jpg") if not dry_run else "project/assets/web/Denhaag_DRY.jpg"

    streetlog_d2_slug = "node-street-log-d2-20260318"
    streetlog_d3_slug = "node-street-log-d3-20260318"
    streetlog_d4_slug = "node-street-log-d4-20260318"

    streetlog_d2_path = NODES_DIR / f"{streetlog_d2_slug}.html"
    streetlog_d3_path = NODES_DIR / f"{streetlog_d3_slug}.html"
    streetlog_d4_path = NODES_DIR / f"{streetlog_d4_slug}.html"

    # d2: junction -- the second gap
    streetlog_d2_body = inline_block(
        entry_id="street-log-second-gap",
        title="the second gap // different camera, same window",
        body_html=
            "        <p>It happened a second time on a different camera. Not the crosswalk camera. A privately-operated camera I have been accessing via a neighbor who does not know what I use the feed for. The camera is pointed at the street from the second floor, covering approximately sixty meters of the block. The gap in this feed is at a different time on a different day, but the duration is the same: three minutes and seven seconds. Not three minutes exactly. Three minutes and seven seconds, identical to the gap in the crosswalk camera footage to the second.</p>\n"
            "        <p>The probability of two independent gaps in two independent camera feeds being the same duration to the second by coincidence is not a calculation I am interested in performing because the result would be a number I would dismiss anyway. The implication is that the gaps are not camera failures or recording artifacts. They are interventions of a fixed duration. Three minutes and seven seconds is how long the intervention takes. Whatever the intervention is, it runs on a timer.</p>\n"
            "        <p>I have documented both gaps in the notebook. The gap timestamps do not overlap. The cameras are not on the same network. The recording devices are different brands, different firmware, different storage methods. The only thing they have in common is the duration of their absence and the fact that I have access to both.</p>\n",
        stamp="second gap confirmed // different camera // same duration to the second // intervention timer // 2026-03-18",
        depth=2,
    )

    streetlog_d2_html = make_junction_node(
        node_slug=streetlog_d2_slug,
        entry_title="street-log-01 // second gap // same duration",
        posted_date=POSTED_DATE,
        depth=2,
        parent_href="../fragments/street-log-01.html",
        content_html=streetlog_d2_body,
        data_motif="gap,footage,camera,duration,interval,intervention,timer,second,same,independent,access",
    )

    # d3: junction -- locating the intervention point
    streetlog_d3_img_html = ""
    if img_streetlog_d3:
        streetlog_d3_img_html = (
            f'      <figure class="evidence">'
            f'<img src="../{_esc(img_streetlog_d3)}" alt="intersection documentation"></figure>\n'
        )

    streetlog_d3_body = (
        f'    <!-- branch: inline  depth=3  seed=1  orient=vertical -->\n'
        f'    <section class="cascade-block cascade-rich cp-a cascade-orient-vertical"\n'
        f'      data-entry="street-log-intervention-point"\n'
        f'      data-type="inline"\n'
        f'      data-depth="3"\n'
        f'      data-branch-seed="1"\n'
        f'      data-orientation="vertical">\n'
        f'      <h2>the intervention point // where the coverage breaks</h2>\n'
        f'      <div class="wire-body">\n'
        f'        <p>I mapped the two camera fields of view against each other. The crosswalk camera covers the intersection from the north side. The second camera covers the block from the second floor, east aspect. There is an overlap zone -- approximately a fifteen-meter section that falls within both fields of view, in theory, but that both cameras lose during their respective gaps.</p>\n'
        f'        <p>The overlap zone is centered on a utility access point: a junction box mounted to the pole at the intersection, the kind used for traffic signal timing and, in some configurations, for communications infrastructure. I cannot confirm what that box is used for in this installation. I can confirm it exists, that it is in the overlap zone, and that both cameras lose their feed when something happens at the time associated with that zone.</p>\n'
        f'        <p>I photographed the box from three angles on two different days. The box has markings consistent with telecommunications infrastructure rather than traffic management alone. On the second day it had a small scratch on the upper-left corner that was not there on the first day. The scratch is fresh. The paint is light-colored and the scratch shows the bare metal below. Something contacted that box between my two visits.</p>\n'
        f'      </div>\n'
        f'{streetlog_d3_img_html}'
        f'      <p class="stamp">overlap zone mapped // junction box identified // scratch documented // 2026-03-18</p>\n'
        f'    </section>\n\n'
    )

    streetlog_d3_html = make_junction_node(
        node_slug=streetlog_d3_slug,
        entry_title="street-log-01 // the intervention point // junction box",
        posted_date=POSTED_DATE,
        depth=3,
        parent_href=f"../{streetlog_d2_slug}.html",
        content_html=streetlog_d3_body,
        data_motif="intervention,point,junction,box,overlap,camera,utility,telecommunications,scratch,marked,infrastructure",
    )

    # d4: terminal -- the scratch and what it means
    streetlog_d4_content = (
        f'    <div class="wire-body">\n'
        f'      <p>I went back a third time. The scratch is still there. Nothing else has changed on the box. The scratch is the only change. If the scratch had been there before my first visit I would not have noticed it. I noticed it on the second visit only because I was looking for change, and change was there. That means the scratch happened between visit one and visit two. It is the only change I have documented in the physical environment that is time-stamped with that kind of precision.</p>\n'
        f'      <p>I have been trying to decide what makes a fresh scratch significant on a piece of telecommunications infrastructure. It documents contact. Someone or something physically contacted that box during the relevant window. And it is the kind of evidence that does not require interpretation to be meaningful. The scratch either existed or it did not. On the first visit it did not. On the second it did. That is a fact. Whatever happened to produce it is inference, but the scratch itself is not.</p>\n'
        f'      <p>What I am doing now: I have photographed the box enough. I am not going back. Additional photographs of the same object will not add information. They will add a pattern of visits that is itself documentable, and I am not going to make myself easier to observe while I observe. I have what I have from the box. Both gaps are on record. The overlap zone is on record. The scratch is on record. The thread ends here not because the investigation is complete but because this branch has reached the limit of what can be extracted from this location without increasing my own visibility.</p>\n'
        f'    </div>\n'
    )

    streetlog_d4_html = make_terminal_node(
        node_slug=streetlog_d4_slug,
        entry_title="street-log-01 // the scratch // the thread limit",
        posted_date=POSTED_DATE,
        depth=4,
        parent_href=f"../{streetlog_d3_slug}.html",
        content_html=streetlog_d4_content,
        data_motif="scratch,contact,physical,evidence,box,visits,observation,visibility,thread,limit,documented,extract",
        footer_note="evidence recorded // visibility limit reached // this branch is complete",
    )

    if not dry_run:
        streetlog_d2_path.write_text(streetlog_d2_html, encoding="utf-8")
        streetlog_d3_path.write_text(streetlog_d3_html, encoding="utf-8")
        streetlog_d4_path.write_text(streetlog_d4_html, encoding="utf-8")
        print(f"  created: {streetlog_d2_path.name}")
        print(f"  created: {streetlog_d3_path.name}")
        print(f"  created: {streetlog_d4_path.name}")

        # Patch street-log-01.html: insert link block before footer
        streetlog_root = FRAGS_DIR / "street-log-01.html"
        link_section = (
            f'\n    <!-- branch: link  depth=1  seed=0  orient=vertical -->\n'
            f'    <section class="cascade-block cascade-node cp-b cascade-orient-vertical"\n'
            f'      data-entry="street-log-d2-second-gap"\n'
            f'      data-type="link"\n'
            f'      data-depth="1"\n'
            f'      data-branch-seed="0"\n'
            f'      data-orientation="vertical">\n'
            f'      <h2>the second gap // different camera, same window</h2>\n'
            f'      <span class="lean-link">'
            f'<a href="../nodes/{streetlog_d2_slug}.html">open node</a>'
            f' <span class="stamp">// second footage gap // same 3m07s duration // different camera</span></span>\n'
            f'      <p class="stamp">posted: {POSTED_DATE}</p>\n'
            f'    </section>\n\n'
        )
        insert_before_footer_close(streetlog_root, link_section)
        patch_footer_add_nav(
            streetlog_root,
            f'      <p class="nav-down"><a href="../nodes/{streetlog_d2_slug}.html">[down] the second gap // different camera, same duration</a></p>',
        )
        print(f"  patched: fragments/street-log-01.html")

    created_pages += [streetlog_d2_path.name, streetlog_d3_path.name, streetlog_d4_path.name]
    report.append(("street-log-01", "fragments/street-log-01.html", 1, 4, 3))

    for slug, title, depth, action in [
        (streetlog_d2_slug, "street-log-01 // second gap // same duration", 1, "link"),
        (streetlog_d2_slug, "street-log-01 // second gap // same duration", 2, "inline"),
        (streetlog_d3_slug, "street-log-01 // intervention point // junction box", 3, "inline"),
        (streetlog_d4_slug, "street-log-01 // the scratch // thread limit", 4, "inline"),
    ]:
        append_log(log, {
            "entry_id": f"deepen-{slug}",
            "title": title,
            "depth": depth,
            "roll": 0 if action == "link" else 1,
            "action": action,
            "dest_page": f"nodes/{slug}.html",
            "dest_type": "node",
            "orientation": "vertical",
            "timestamp_utc": "2026-03-18T23:59:05Z",
            "posted_date": POSTED_DATE,
            "note": "deepening migration: +3 layers from street-log-01",
        })

    # ===========================================================================
    # Save branch log
    # ===========================================================================
    if not dry_run:
        save_log(log)
        print("\n  branch-log.json updated")

    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deepen existing threads by +3 depth layers each."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be done without writing any files.",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN -- no files will be written.\n")

    report = run_migration(dry_run=args.dry_run)

    print("\n" + "=" * 60)
    print("EXPANSION REPORT")
    print("=" * 60)
    print(f"{'Branch':<20} {'Deepest node (before)':<45} {'Old':>5} {'New':>5} {'New pages':>10}")
    print("-" * 60)
    for branch_id, deepest_before, old_depth, new_depth, pages_created in report:
        print(f"{branch_id:<20} {deepest_before:<45} {old_depth:>5} {new_depth:>5} {pages_created:>10}")
    print("-" * 60)
    total = sum(r[4] for r in report)
    print(f"{'TOTAL':<67} {total:>10} pages created")
    print()

    if not args.dry_run:
        print("All nodes written. Review with: git diff")
        print("Then: git add -A && git commit -m 'feat: deepen existing threads +3 layers each'")

    return 0


if __name__ == "__main__":
    sys.exit(main())
