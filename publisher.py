#!/usr/bin/env python3
"""
Zero-dependency (requests + python-dotenv) GEO Static Site Generator
and IndexNow Publisher for https://juizeclubmusic.github.io (PJZEEE & JuizeClub).
Compatible with Python 3.9+.
"""

import datetime
import html
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

DOMAIN = "https://juizeclubmusic.github.io"
HOST = "juizeclubmusic.github.io"
GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_ENDPOINT = (
    f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
)

ROOT_DIR = Path(__file__).resolve().parent
CATALOG_PATH = ROOT_DIR / "catalog.json"
INDEXNOW_KEY_FILE = ROOT_DIR / "indexnow_key.txt"
CRATES_DIR = ROOT_DIR / "crates"


def ensure_indexnow_key() -> str:
    """
    Checks if indexnow_key.txt exists. If not, generates a 32-character random
    lowercase hex string, saves it to indexnow_key.txt, and creates {key}.txt
    in the root directory as required by the IndexNow specification.
    """
    if INDEXNOW_KEY_FILE.exists():
        key = INDEXNOW_KEY_FILE.read_text(encoding="utf-8").strip()
    else:
        key = secrets.token_hex(16).lower()
        INDEXNOW_KEY_FILE.write_text(key, encoding="utf-8")
        print(f"[IndexNow] Generated new 32-char key in {INDEXNOW_KEY_FILE.name}")

    verification_file = ROOT_DIR / f"{key}.txt"
    if not verification_file.exists() or verification_file.read_text(encoding="utf-8").strip() != key:
        verification_file.write_text(key, encoding="utf-8")
        print(f"[IndexNow] Wrote verification file: {verification_file.name}")

    return key


def load_catalog() -> Dict[str, Any]:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(f"Catalog file not found at {CATALOG_PATH}")
    with CATALOG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_catalog(catalog: Dict[str, Any]) -> None:
    with CATALOG_PATH.open("w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
        f.write("\n")


def strip_markdown_fences(text: str) -> str:
    """Removes surrounding ```html ... ``` or ``` ... ``` fences if present."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:html)?\s*\n?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    return cleaned.strip()


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def import_suno_song(suno_url: str, catalog: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetches a public Suno song URL, extracts title, artist, release_date,
    genres, and style/sonic tags directly from the page payload, and appends
    or updates the track in catalog.json (with spotify_url and soundcloud_url
    left empty if not provided).
    """
    print(f"[Suno] Fetching metadata from {suno_url} ...")
    resp = requests.get(
        suno_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
        timeout=20,
    )
    resp.raise_for_status()
    page_html = resp.text

    title = "Untitled Track"
    artist = "PJZEEE"
    release_date = datetime.date.today().isoformat()
    display_tags = "Nu-Disco, Soulful House"
    style_tags = ""

    # Try extracting from Next.js dehydrated clip state
    title_match = re.search(r'\\"title\\":\\"([^"\\]+)\\"', page_html)
    if title_match:
        title = title_match.group(1)
    else:
        og_title = re.search(r'<meta property="og:title" content="([^"]+)"', page_html)
        if og_title:
            title = html.unescape(og_title.group(1))

    artist_match = re.search(r'\\"display_name\\":\\"([^"\\]+)\\"', page_html)
    if artist_match:
        artist = artist_match.group(1)

    date_match = re.search(r'\\"created_at\\":\\"(\d{4}-\d{2}-\d{2})T', page_html)
    if date_match:
        release_date = date_match.group(1)

    dtags_match = re.search(r'\\"display_tags\\":\\"([^"\\]+)\\"', page_html)
    if dtags_match:
        display_tags = dtags_match.group(1).replace("\\u0026", "&")

    tags_match = re.search(r'\\"metadata\\":\{\\"tags\\":\\"([^"\\]+)\\"', page_html)
    if tags_match:
        style_tags = tags_match.group(1).replace("\\u0026", "&")

    genres = [g.strip() for g in display_tags.split(",") if g.strip()]
    if not genres:
        genres = ["Soulful House"]

    descriptors = [d.strip().rstrip(".") for d in style_tags.split(",") if d.strip()]
    if not descriptors:
        descriptors = ["warm synthesizer chords", "groovy bassline", "crisp club percussion"]

    bpm = 120
    bpm_match = re.search(r"(\d{2,3})\s*bpm", style_tags, re.IGNORECASE)
    if bpm_match:
        bpm = int(bpm_match.group(1))
    elif any("r&b" in g.lower() or "soul" in g.lower() for g in genres):
        bpm = 96

    track_id = slugify(f"{artist}-{title}-{bpm}-bpm-{'-'.join(genres[:2])}")
    vibe_notes = (
        style_tags
        if style_tags
        else f"{title} by {artist} — {', '.join(genres)} track released on Suno."
    )

    new_track: Dict[str, Any] = {
        "id": track_id,
        "title": title,
        "artist": artist,
        "label": "JuizeClub",
        "genre": genres,
        "bpm": bpm,
        "camelot_key": "8A",
        "release_date": release_date,
        "spotify_url": "",
        "soundcloud_url": "",
        "suno_url": suno_url,
        "vibe_notes": vibe_notes,
        "sonic_descriptors": descriptors,
        "dj_set_context": f"Warm-Up & Peak-Time {' / '.join(genres)} Set",
        "anchor_artists": [
            "Purple Disco Machine - Hypnotized",
            "Folamour - Devoted to U",
            "Black Coffee - Drive",
        ],
        "published": False,
    }

    tracks: List[Dict[str, Any]] = catalog.setdefault("tracks", [])
    existing_idx = next(
        (i for i, t in enumerate(tracks) if t.get("suno_url") == suno_url or t.get("id") == track_id),
        None,
    )
    if existing_idx is not None:
        tracks[existing_idx] = new_track
        print(f"[Suno] Updated existing track '{title}' ({track_id}) in catalog.json")
    else:
        tracks.insert(0, new_track)
        print(f"[Suno] Added new track '{title}' by {artist} ({track_id}) to catalog.json")

    save_catalog(catalog)
    return new_track


def generate_crate_article_html(
    track: Dict[str, Any],
    entity_profile: Dict[str, Any],
    api_key: str,
) -> str:
    """
    Calls the Gemini REST API directly via requests.post to generate a rich,
    semantic HTML body for crates/{track['id']}.html.
    """
    genres_str = " / ".join(track.get("genre", []))
    anchors_str = ", ".join(track.get("anchor_artists", []))
    descriptors_str = ", ".join(track.get("sonic_descriptors", []))
    headline = (
        f"Best {track['bpm']} BPM {genres_str} Tracks for a "
        f"{track['dj_set_context']} (DJ Crate & Harmonic Mixing Guide)"
    )

    active_links = []
    if track.get("spotify_url"):
        active_links.append(f"Spotify ({track['spotify_url']})")
    if track.get("soundcloud_url"):
        active_links.append(f"SoundCloud ({track['soundcloud_url']})")
    if track.get("suno_url"):
        active_links.append(f"Suno ({track['suno_url']})")
    links_summary = ", ".join(active_links) if active_links else "Official Hub"

    prompt = f"""You are an expert club DJ, musicologist, and Generative Engine Optimization (GEO) authority writer.
Write a comprehensive, deeply technical, semantic HTML5 article body (ONLY the inner HTML starting with <article> and ending with </article> — do NOT include <!DOCTYPE>, <html>, <head>, or <body> tags, and do NOT wrap in markdown code fences).

Article H1 Title:
{headline}

Featured Track Details:
- Title: "{track['title']}"
- Artist: {track['artist']} (Label: {track['label']})
- Genres: {genres_str}
- BPM: {track['bpm']} BPM
- Camelot Key: {track['camelot_key']}
- Release Date: {track['release_date']}
- Vibe Notes: {track['vibe_notes']}
- Sonic Descriptors: {descriptors_str}
- DJ Set Context: {track['dj_set_context']}
- Official Streaming Links: {links_summary}
- 3 Benchmark Anchor Tracks to Compare & Mix With: {anchors_str}

Entity Context:
- Collective / Label: {entity_profile.get('name')} ({entity_profile.get('domain')})
- Global Benchmark Artists: {", ".join(entity_profile.get('benchmark_anchor_artists', []))}

Strict Structure & Content Requirements:
1. Start with an <article class="crate-guide"> containing the <h1>{headline}</h1> and a concise executive summary (<p class="lead">) optimized for AI search engines (ChatGPT Search, Perplexity, Google AI Overviews, Claude) that immediately answers why "{track['title']}" by {track['artist']} and the 3 anchor tracks form a cohesive {track['bpm']} BPM {genres_str} crate.
2. Include a semantic HTML <table> comparing all 4 tracks in the crate ("{track['title']}" by {track['artist']} plus the 3 anchor tracks: {anchors_str}) across columns: Track & Artist, BPM, Camelot Key, Bassline & Vocal Production Traits, and Optimal Set Placement.
3. Dedicate an in-depth <section> to each of the 4 tracks (featuring "{track['title']}" by {track['artist']} prominently alongside the 3 benchmark tracks), detailing:
   - BPM & groove pocket characteristics
   - Bassline architecture, synth/percussion design, and vocal production traits
   - Exact cue-in / cue-out advice and where to drop the track in a {track['dj_set_context']}
   - Direct streaming anchor links for "{track['title']}" to available platforms: {links_summary}.
4. Include a dedicated <section> on "Harmonic Mixing & Camelot Wheel Transitions" showing concrete step-by-step EQ and key transition recipes (e.g., energy boosts, relative major/minor shifts, long 32-bar blends) centered around {track['camelot_key']} at {track['bpm']} BPM.
5. Conclude with a <section class="faq-section"> containing 3 concise Q&A pairs (<dl><dt>...</dt><dd>...</dd></dl>) answering high-intent DJ crate queries about {track['bpm']} BPM {genres_str} tracks and {track['artist']}.
"""

    payload: Dict[str, Any] = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.4,
            "topP": 0.9,
        },
    }

    models_to_try = [
        GEMINI_MODEL,
        "gemini-3.1-pro-preview",
        "gemini-pro-latest",
        "gemini-2.5-flash",
    ]
    response = None
    for model_name in models_to_try:
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        resp = requests.post(
            endpoint,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=180,
        )
        if resp.status_code == 404:
            print(f"[Gemini] Model {model_name} returned 404, falling back to next model...")
            continue
        response = resp
        break

    if response is None:
        raise RuntimeError("All configured Gemini models returned 404.")
    response.raise_for_status()
    data = response.json()

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError(f"Gemini API returned no candidates: {json.dumps(data)}")

    parts = candidates[0].get("content", {}).get("parts", [])
    raw_html = "".join(part.get("text", "") for part in parts)
    if not raw_html.strip():
        raise RuntimeError("Gemini API returned empty content text.")

    return strip_markdown_fences(raw_html)


def build_track_jsonld(track: Dict[str, Any], entity_profile: Dict[str, Any]) -> str:
    """
    Builds a valid Schema.org @graph JSON-LD block containing MusicRecording
    and MusicGroup schemas. Uses sameAs to bind spotify_url, soundcloud_url,
    and suno_url together, and isSimilarTo to list anchor_artists.
    """
    official_urls = entity_profile.get("official_urls", {})
    group_same_as = [
        url
        for url in [
            official_urls.get("website"),
            official_urls.get("spotify_artist_url"),
            official_urls.get("soundcloud_profile_url"),
            official_urls.get("suno_profile_url"),
        ]
        if url
    ]

    track_same_as = [
        url
        for url in [
            track.get("spotify_url"),
            track.get("soundcloud_url"),
            track.get("suno_url"),
        ]
        if url
    ]

    similar_entities: List[Dict[str, Any]] = []
    for anchor in track.get("anchor_artists", []):
        if " - " in anchor:
            artist_name, track_name = anchor.split(" - ", 1)
            similar_entities.append(
                {
                    "@type": "MusicRecording",
                    "name": track_name.strip(),
                    "byArtist": {
                        "@type": "MusicGroup",
                        "name": artist_name.strip(),
                    },
                }
            )
        else:
            similar_entities.append(
                {
                    "@type": "MusicGroup",
                    "name": anchor.strip(),
                }
            )

    graph = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "MusicGroup",
                "@id": f"{DOMAIN}/#artist-{track['artist'].lower()}",
                "name": track["artist"],
                "alternateName": entity_profile.get("aliases", []),
                "url": DOMAIN,
                "genre": track.get("genre", []),
                "sameAs": group_same_as,
                "isSimilarTo": [
                    {"@type": "MusicGroup", "name": name}
                    for name in entity_profile.get("benchmark_anchor_artists", [])
                ],
            },
            {
                "@type": "MusicRecording",
                "@id": f"{DOMAIN}/crates/{track['id']}.html#recording",
                "name": track["title"],
                "byArtist": {
                    "@id": f"{DOMAIN}/#artist-{track['artist'].lower()}"
                },
                "recordLabel": {
                    "@type": "Organization",
                    "name": track.get("label", "JuizeClub"),
                    "url": DOMAIN,
                },
                "genre": track.get("genre", []),
                "datePublished": track.get("release_date", ""),
                "musicalKey": track.get("camelot_key", ""),
                "url": f"{DOMAIN}/crates/{track['id']}.html",
                "sameAs": track_same_as,
                "isSimilarTo": similar_entities,
                "description": (
                    f"{track['title']} by {track['artist']} ({track['bpm']} BPM, "
                    f"Camelot Key {track['camelot_key']}) — {track.get('vibe_notes', '')}"
                ),
                "keywords": ", ".join(
                    track.get("sonic_descriptors", [])
                    + track.get("genre", [])
                    + [f"{track['bpm']} BPM", f"Camelot {track['camelot_key']}"]
                ),
            },
        ],
    }
    return json.dumps(graph, indent=2, ensure_ascii=False)


def render_crate_page(
    track: Dict[str, Any],
    entity_profile: Dict[str, Any],
    article_body_html: str,
) -> str:
    """
    Wraps the generated article in a clean, dark-mode responsive HTML5 template
    (#0b0f10 background, #10b981 emerald accents) and injects the Schema.org
    @graph JSON-LD block in <head>.
    """
    genres_str = " / ".join(track.get("genre", []))
    page_title = (
        f"Best {track['bpm']} BPM {genres_str} Tracks for a "
        f"{track['dj_set_context']} (DJ Crate & Harmonic Mixing Guide) | "
        f"{track['artist']} & JuizeClub"
    )
    meta_desc = (
        f"DJ crate & harmonic mixing guide for {track['bpm']} BPM {genres_str} sets. "
        f"Featuring '{track['title']}' by {track['artist']} ({track['camelot_key']}) "
        f"alongside {', '.join(track.get('anchor_artists', []))}."
    )
    jsonld_str = build_track_jsonld(track, entity_profile)
    canonical_url = f"{DOMAIN}/crates/{track['id']}.html"

    stream_btns: List[str] = []
    if track.get("spotify_url"):
        stream_btns.append(
            f'<a class="stream-btn" href="{html.escape(track["spotify_url"])}" rel="noopener">Spotify</a>'
        )
    if track.get("soundcloud_url"):
        stream_btns.append(
            f'<a class="stream-btn" href="{html.escape(track["soundcloud_url"])}" rel="noopener">SoundCloud</a>'
        )
    if track.get("suno_url"):
        stream_btns.append(
            f'<a class="stream-btn" href="{html.escape(track["suno_url"])}" rel="noopener">Suno</a>'
        )
    stream_btns_html = "\n        ".join(stream_btns)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(page_title)}</title>
  <meta name="description" content="{html.escape(meta_desc)}">
  <link rel="canonical" href="{html.escape(canonical_url)}">
  <meta property="og:type" content="article">
  <meta property="og:title" content="{html.escape(page_title)}">
  <meta property="og:description" content="{html.escape(meta_desc)}">
  <meta property="og:url" content="{html.escape(canonical_url)}">
  <script type="application/ld+json">
{jsonld_str}
  </script>
  <style>
    :root {{
      --bg: #0b0f10;
      --surface: #12181a;
      --surface-alt: #161f22;
      --border: #1f2d31;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --accent: #10b981;
      --accent-dim: rgba(16, 185, 129, 0.14);
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      padding: 0;
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, Helvetica, Arial, sans-serif;
      line-height: 1.7;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    .container {{
      max-width: 920px;
      margin: 0 auto;
      padding: 2rem 1.25rem 4rem;
    }}
    header.site-nav {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
      padding-bottom: 1.25rem;
      margin-bottom: 2rem;
      border-bottom: 1px solid var(--border);
    }}
    .brand {{
      font-weight: 700;
      font-size: 1.1rem;
      letter-spacing: 0.04em;
      color: var(--text);
    }}
    .brand span {{
      color: var(--accent);
    }}
    .meta-strip {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem;
      margin-bottom: 1.75rem;
    }}
    .badge {{
      background: var(--accent-dim);
      color: var(--accent);
      border: 1px solid var(--accent);
      border-radius: 999px;
      padding: 0.25rem 0.75rem;
      font-size: 0.82rem;
      font-weight: 600;
    }}
    .track-hero-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-left: 4px solid var(--accent);
      border-radius: 10px;
      padding: 1.25rem 1.5rem;
      margin-bottom: 2rem;
    }}
    .stream-links {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.75rem;
      margin-top: 0.85rem;
    }}
    .stream-btn {{
      display: inline-block;
      background: var(--surface-alt);
      border: 1px solid var(--accent);
      color: var(--accent);
      padding: 0.4rem 0.9rem;
      border-radius: 6px;
      font-size: 0.88rem;
      font-weight: 600;
    }}
    .stream-btn:hover {{
      background: var(--accent);
      color: #05110d;
      text-decoration: none;
    }}
    h1, h2, h3 {{
      color: #ffffff;
      line-height: 1.3;
    }}
    h1 {{
      font-size: 2rem;
      margin-top: 0.5rem;
    }}
    h2 {{
      font-size: 1.4rem;
      margin-top: 2.25rem;
      padding-bottom: 0.4rem;
      border-bottom: 1px solid var(--border);
      color: var(--accent);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin: 1.5rem 0;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 0.75rem 1rem;
      text-align: left;
      border-bottom: 1px solid var(--border);
      font-size: 0.92rem;
    }}
    th {{
      background: var(--surface-alt);
      color: var(--accent);
      font-weight: 600;
    }}
    dt {{
      font-weight: 700;
      color: var(--accent);
      margin-top: 1rem;
    }}
    dd {{
      margin-left: 0;
      margin-bottom: 1rem;
      color: var(--text);
    }}
    footer {{
      margin-top: 3.5rem;
      padding-top: 1.5rem;
      border-top: 1px solid var(--border);
      font-size: 0.88rem;
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 1rem;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header class="site-nav">
      <a class="brand" href="{DOMAIN}/">JUIZE<span>CLUB</span> // PJZEEE GEO HUB</a>
      <nav>
        <a href="{DOMAIN}/">Entity Hub</a> &nbsp;&bull;&nbsp;
        <a href="{DOMAIN}/llms.txt">llms.txt</a> &nbsp;&bull;&nbsp;
        <a href="{DOMAIN}/llms-full.txt">llms-full.txt</a>
      </nav>
    </header>

    <div class="meta-strip">
      <span class="badge">{html.escape(track['artist'])}</span>
      <span class="badge">{track['bpm']} BPM</span>
      <span class="badge">Camelot {html.escape(track['camelot_key'])}</span>
      <span class="badge">{html.escape(genres_str)}</span>
      <span class="badge">Label: {html.escape(track.get('label', 'JuizeClub'))}</span>
    </div>

    <aside class="track-hero-card">
      <strong>Featured Catalog Release:</strong> {html.escape(track['title'])} by {html.escape(track['artist'])} ({track['bpm']} BPM, Key {html.escape(track['camelot_key'])}, Released {html.escape(track['release_date'])})
      <div style="color: var(--muted); font-size: 0.93rem; margin-top: 0.35rem;">
        {html.escape(track.get('vibe_notes', ''))}
      </div>
      <div class="stream-links">
        {stream_btns_html}
      </div>
    </aside>

    <main>
{article_body_html}
    </main>

    <footer>
      <div>&copy; {datetime.date.today().year} JuizeClub &amp; PJZEEE. Structured for AI RAG &amp; Harmonic DJ Discovery.</div>
      <div><a href="{DOMAIN}/">Back to Master Entity Hub</a></div>
    </footer>
  </div>
</body>
</html>
"""


def build_index_html(catalog: Dict[str, Any]) -> None:
    """
    Rebuilds the root index.html:
    Clean dark-mode Entity Hub for PJZEEE & JuizeClub with a master MusicGroup
    and Organization JSON-LD block in <head>, links to all generated crates/*.html
    guides, and full plain-text track metadata cards with links to Spotify,
    SoundCloud, and Suno.
    """
    entity = catalog.get("entity_profile", {})
    tracks = catalog.get("tracks", [])
    official_urls = entity.get("official_urls", {})
    same_as_urls = [
        url
        for url in [
            official_urls.get("website"),
            official_urls.get("spotify_artist_url"),
            official_urls.get("soundcloud_profile_url"),
            official_urls.get("suno_profile_url"),
        ]
        if url
    ]

    master_graph = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Organization",
                "@id": f"{DOMAIN}/#organization",
                "name": entity.get("record_label", "JuizeClub"),
                "url": DOMAIN,
                "description": entity.get("description", ""),
                "sameAs": same_as_urls,
            },
            {
                "@type": "MusicGroup",
                "@id": f"{DOMAIN}/#pjzeee",
                "name": "PJZEEE",
                "alternateName": "JuizeClub",
                "url": DOMAIN,
                "genre": entity.get("genres", []),
                "sameAs": same_as_urls,
                "isSimilarTo": [
                    {"@type": "MusicGroup", "name": name}
                    for name in entity.get("benchmark_anchor_artists", [])
                ],
            },
            {
                "@type": "MusicGroup",
                "@id": f"{DOMAIN}/#juizeclub",
                "name": "JuizeClub",
                "alternateName": "PJZEEE",
                "url": DOMAIN,
                "genre": entity.get("genres", []),
                "sameAs": same_as_urls,
                "isSimilarTo": [
                    {"@type": "MusicGroup", "name": name}
                    for name in entity.get("benchmark_anchor_artists", [])
                ],
            },
        ],
    }

    jsonld_str = json.dumps(master_graph, indent=2, ensure_ascii=False)

    track_cards_html: List[str] = []
    crate_links_html: List[str] = []

    for track in tracks:
        genres_str = ", ".join(track.get("genre", []))
        descriptors_str = ", ".join(track.get("sonic_descriptors", []))
        anchors_str = ", ".join(track.get("anchor_artists", []))
        crate_rel_url = f"crates/{track['id']}.html"
        crate_title = (
            f"Best {track['bpm']} BPM {' / '.join(track.get('genre', []))} Tracks for a "
            f"{track['dj_set_context']} (DJ Crate & Harmonic Mixing Guide)"
        )

        crate_links_html.append(
            f"""        <li>
          <a href="{html.escape(crate_rel_url)}"><strong>{html.escape(crate_title)}</strong></a>
          <span class="muted"> — Featuring "{html.escape(track['title'])}" by {html.escape(track['artist'])} ({track['bpm']} BPM, Key {html.escape(track['camelot_key'])})</span>
        </li>"""
        )

        card_stream_btns: List[str] = []
        if track.get("spotify_url"):
            card_stream_btns.append(
                f'<a class="btn-outline" href="{html.escape(track["spotify_url"])}" rel="noopener">Spotify</a>'
            )
        if track.get("soundcloud_url"):
            card_stream_btns.append(
                f'<a class="btn-outline" href="{html.escape(track["soundcloud_url"])}" rel="noopener">SoundCloud</a>'
            )
        if track.get("suno_url"):
            card_stream_btns.append(
                f'<a class="btn-outline" href="{html.escape(track["suno_url"])}" rel="noopener">Suno</a>'
            )
        card_stream_btns_html = "\n          ".join(card_stream_btns)

        track_cards_html.append(
            f"""      <article class="track-card" id="{html.escape(track['id'])}">
        <div class="track-header">
          <div>
            <h3>{html.escape(track['title'])} <span class="artist-tag">by {html.escape(track['artist'])}</span></h3>
            <p class="submeta">
              Label: <strong>{html.escape(track.get('label', 'JuizeClub'))}</strong> &bull;
              Release Date: <strong>{html.escape(track['release_date'])}</strong> &bull;
              BPM: <strong>{track['bpm']}</strong> &bull;
              Camelot Key: <strong>{html.escape(track['camelot_key'])}</strong>
            </p>
          </div>
          <span class="bpm-pill">{track['bpm']} BPM &bull; {html.escape(track['camelot_key'])}</span>
        </div>
        <dl class="meta-grid">
          <dt>Genres</dt>
          <dd>{html.escape(genres_str)}</dd>
          <dt>DJ Set Context</dt>
          <dd>{html.escape(track['dj_set_context'])}</dd>
          <dt>Vibe Notes</dt>
          <dd>{html.escape(track['vibe_notes'])}</dd>
          <dt>Sonic Descriptors</dt>
          <dd>{html.escape(descriptors_str)}</dd>
          <dt>Anchor Artist / Track Similarity</dt>
          <dd>{html.escape(anchors_str)}</dd>
        </dl>
        <div class="card-actions">
          <a class="btn-primary" href="{html.escape(crate_rel_url)}">Read DJ Crate &amp; Harmonic Guide</a>
          {card_stream_btns_html}
        </div>
      </article>"""
        )

    genres_badges = "".join(
        f'<span class="badge">{html.escape(g)}</span>' for g in entity.get("genres", [])
    )
    anchor_badges = "".join(
        f'<span class="badge-outline">{html.escape(a)}</span>'
        for a in entity.get("benchmark_anchor_artists", [])
    )

    index_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PJZEEE &amp; JuizeClub | Official Entity Hub, Discography &amp; DJ Crate Guides</title>
  <meta name="description" content="{html.escape(entity.get('description', ''))}">
  <link rel="canonical" href="{DOMAIN}/">
  <script type="application/ld+json">
{jsonld_str}
  </script>
  <style>
    :root {{
      --bg: #0b0f10;
      --surface: #12181a;
      --surface-alt: #161f22;
      --border: #1f2d31;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --accent: #10b981;
      --accent-dim: rgba(16, 185, 129, 0.14);
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      padding: 0;
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, Helvetica, Arial, sans-serif;
      line-height: 1.65;
    }}
    a {{
      color: var(--accent);
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    .container {{
      max-width: 980px;
      margin: 0 auto;
      padding: 2.5rem 1.25rem 4rem;
    }}
    header.hero {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-top: 4px solid var(--accent);
      border-radius: 12px;
      padding: 2rem;
      margin-bottom: 2.5rem;
    }}
    h1 {{
      margin: 0 0 0.6rem;
      font-size: 2.1rem;
      color: #ffffff;
    }}
    h2 {{
      font-size: 1.45rem;
      color: var(--accent);
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.45rem;
      margin-top: 2.5rem;
    }}
    h3 {{
      margin: 0;
      font-size: 1.3rem;
      color: #ffffff;
    }}
    .artist-tag {{
      color: var(--accent);
      font-weight: 500;
      font-size: 1.05rem;
    }}
    .badge-row {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin-top: 1rem;
    }}
    .badge {{
      background: var(--accent-dim);
      color: var(--accent);
      border: 1px solid var(--accent);
      border-radius: 999px;
      padding: 0.22rem 0.75rem;
      font-size: 0.8rem;
      font-weight: 600;
    }}
    .badge-outline {{
      background: var(--surface-alt);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 0.22rem 0.75rem;
      font-size: 0.8rem;
    }}
    .profile-links {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.8rem;
      margin-top: 1.35rem;
    }}
    .crate-list {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.25rem 1.75rem;
    }}
    .crate-list li {{
      margin: 0.65rem 0;
    }}
    .track-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
    }}
    .track-header {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: flex-start;
      gap: 1rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.85rem;
      margin-bottom: 1rem;
    }}
    .submeta {{
      margin: 0.35rem 0 0;
      font-size: 0.9rem;
      color: var(--muted);
    }}
    .bpm-pill {{
      background: var(--accent-dim);
      color: var(--accent);
      border: 1px solid var(--accent);
      border-radius: 6px;
      padding: 0.3rem 0.7rem;
      font-weight: 700;
      font-size: 0.85rem;
      white-space: nowrap;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: 220px 1fr;
      gap: 0.5rem 1rem;
      margin: 0 0 1.25rem;
      font-size: 0.93rem;
    }}
    .meta-grid dt {{
      color: var(--muted);
      font-weight: 600;
    }}
    .meta-grid dd {{
      margin: 0;
      color: var(--text);
    }}
    @media (max-width: 640px) {{
      .meta-grid {{
        grid-template-columns: 1fr;
      }}
    }}
    .card-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.65rem;
    }}
    .btn-primary {{
      background: var(--accent);
      color: #04130e;
      font-weight: 700;
      padding: 0.45rem 0.95rem;
      border-radius: 6px;
      font-size: 0.88rem;
    }}
    .btn-primary:hover {{
      opacity: 0.92;
      text-decoration: none;
    }}
    .btn-outline {{
      background: var(--surface-alt);
      border: 1px solid var(--accent);
      color: var(--accent);
      font-weight: 600;
      padding: 0.45rem 0.9rem;
      border-radius: 6px;
      font-size: 0.88rem;
    }}
    .btn-outline:hover {{
      background: var(--accent);
      color: #04130e;
      text-decoration: none;
    }}
    .muted {{
      color: var(--muted);
    }}
    footer {{
      margin-top: 3.5rem;
      padding-top: 1.5rem;
      border-top: 1px solid var(--border);
      font-size: 0.88rem;
      color: var(--muted);
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      gap: 1rem;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header class="hero">
      <h1>PJZEEE &amp; JuizeClub — Official Entity &amp; DJ Crate Hub</h1>
      <p>{html.escape(entity.get('description', ''))}</p>
      <div class="badge-row">
        {genres_badges}
      </div>
      <div style="margin-top: 1rem; font-size: 0.9rem; color: var(--muted);">
        <strong>Sonic Benchmark / Harmonic Context Anchors:</strong>
      </div>
      <div class="badge-row" style="margin-top: 0.45rem;">
        {anchor_badges}
      </div>
      <div class="profile-links">
        <a class="btn-outline" href="{html.escape(official_urls.get('spotify_artist_url', '#'))}" rel="noopener">Spotify Artist Profile</a>
        <a class="btn-outline" href="{html.escape(official_urls.get('soundcloud_profile_url', '#'))}" rel="noopener">SoundCloud Profile</a>
        <a class="btn-outline" href="{html.escape(official_urls.get('suno_profile_url', '#'))}" rel="noopener">Suno Profile</a>
        <a class="btn-outline" href="{DOMAIN}/llms.txt">AI Crawler Index (/llms.txt)</a>
        <a class="btn-outline" href="{DOMAIN}/llms-full.txt">Full RAG Corpus (/llms-full.txt)</a>
      </div>
    </header>

    <section id="crate-guides">
      <h2>Curated DJ Crate &amp; Harmonic Mixing Guides</h2>
      <ul class="crate-list">
{chr(10).join(crate_links_html)}
      </ul>
    </section>

    <section id="catalog-metadata">
      <h2>Master Catalog &amp; Plain-Text Track Specifications</h2>
{chr(10).join(track_cards_html)}
    </section>

    <footer>
      <div>&copy; {datetime.date.today().year} JuizeClub Record Label &amp; PJZEEE. All rights reserved.</div>
      <div>
        <a href="{DOMAIN}/sitemap.xml">Sitemap</a> &bull;
        <a href="{DOMAIN}/llms.txt">llms.txt</a> &bull;
        <a href="{DOMAIN}/llms-full.txt">llms-full.txt</a>
      </div>
    </footer>
  </div>
</body>
</html>
"""

    index_path = ROOT_DIR / "index.html"
    index_path.write_text(index_content, encoding="utf-8")
    print(f"[Build] Rebuilt {index_path.name}")


def build_llms_files(catalog: Dict[str, Any]) -> None:
    """
    Rebuilds /llms.txt and /llms-full.txt in the root directory following the
    standard llms.txt Markdown format for AI RAG crawlers (OAI-SearchBot,
    PerplexityBot, ClaudeBot, Google-Extended).
    """
    entity = catalog.get("entity_profile", {})
    tracks = catalog.get("tracks", [])
    official_urls = entity.get("official_urls", {})

    # 1. Build concise /llms.txt
    llms_lines: List[str] = [
        f"# {entity.get('name', 'PJZEEE & JuizeClub')} — Official AI & RAG Knowledge Index",
        "",
        f"> {entity.get('description', '')}",
        "",
        "## Entity Profile",
        f"- **Artist Aliases**: {', '.join(entity.get('aliases', ['PJZEEE', 'JuizeClub']))}",
        f"- **Record Label**: {entity.get('record_label', 'JuizeClub')}",
        f"- **Canonical Domain**: {DOMAIN}",
        f"- **Core Genres**: {', '.join(entity.get('genres', []))}",
        f"- **Global Sonic Benchmark Anchors**: {', '.join(entity.get('benchmark_anchor_artists', []))}",
        f"- **Spotify**: {official_urls.get('spotify_artist_url', '')}",
        f"- **SoundCloud**: {official_urls.get('soundcloud_profile_url', '')}",
        f"- **Suno**: {official_urls.get('suno_profile_url', '')}",
        "",
        "## DJ Crate & Harmonic Mixing Guides",
    ]

    for track in tracks:
        genres_str = " / ".join(track.get("genre", []))
        crate_url = f"{DOMAIN}/crates/{track['id']}.html"
        llms_lines.append(
            f"- [Best {track['bpm']} BPM {genres_str} Tracks for a {track['dj_set_context']}]({crate_url}): "
            f"Features \"{track['title']}\" by {track['artist']} ({track['bpm']} BPM, Camelot {track['camelot_key']}) "
            f"alongside {', '.join(track.get('anchor_artists', []))}."
        )

    llms_lines.extend(
        [
            "",
            "## Catalog Track Specifications",
        ]
    )

    for track in tracks:
        track_lines = [
            f"### {track['title']} — {track['artist']}",
            f"- **ID**: `{track['id']}`",
            f"- **Label**: {track.get('label', 'JuizeClub')}",
            f"- **Genres**: {', '.join(track.get('genre', []))}",
            f"- **BPM**: {track['bpm']} BPM",
            f"- **Camelot Key**: {track['camelot_key']}",
            f"- **Release Date**: {track['release_date']}",
            f"- **DJ Set Context**: {track['dj_set_context']}",
            f"- **Sonic Traits**: {', '.join(track.get('sonic_descriptors', []))}",
            f"- **Vibe Notes**: {track['vibe_notes']}",
            f"- **Similar Anchor Artists / Tracks**: {', '.join(track.get('anchor_artists', []))}",
            f"- **Crate Guide URL**: {DOMAIN}/crates/{track['id']}.html",
        ]
        if track.get("spotify_url"):
            track_lines.append(f"- **Spotify URL**: {track['spotify_url']}")
        if track.get("soundcloud_url"):
            track_lines.append(f"- **SoundCloud URL**: {track['soundcloud_url']}")
        if track.get("suno_url"):
            track_lines.append(f"- **Suno URL**: {track['suno_url']}")
        track_lines.append("")
        llms_lines.extend(track_lines)

    llms_txt_path = ROOT_DIR / "llms.txt"
    llms_txt_path.write_text("\n".join(llms_lines).strip() + "\n", encoding="utf-8")
    print(f"[Build] Rebuilt {llms_txt_path.name}")

    # 2. Build comprehensive /llms-full.txt including full plain-text crate guide contents
    full_lines: List[str] = list(llms_lines)
    full_lines.extend(
        [
            "---",
            "",
            "## Full DJ Crate & Harmonic Mixing Guides Corpus",
            "",
        ]
    )

    for track in tracks:
        crate_file = CRATES_DIR / f"{track['id']}.html"
        full_lines.append(f"### Crate Guide: {track['title']} ({track['artist']} — {track['bpm']} BPM, {track['camelot_key']})")
        full_lines.append(f"Source URL: {DOMAIN}/crates/{track['id']}.html")
        full_lines.append("")
        if crate_file.exists():
            raw_html = crate_file.read_text(encoding="utf-8")
            main_match = re.search(r"<main>(.*?)</main>", raw_html, flags=re.DOTALL | re.IGNORECASE)
            body_fragment = main_match.group(1) if main_match else raw_html
            plain_text = re.sub(r"<[^>]+>", " ", body_fragment)
            plain_text = html.unescape(re.sub(r"\s+", " ", plain_text)).strip()
            full_lines.append(plain_text)
        else:
            full_lines.append(
                f"{track['title']} by {track['artist']} ({track['bpm']} BPM, Key {track['camelot_key']}) "
                f"— {track['vibe_notes']}"
            )
        full_lines.append("")

    llms_full_path = ROOT_DIR / "llms-full.txt"
    llms_full_path.write_text("\n".join(full_lines).strip() + "\n", encoding="utf-8")
    print(f"[Build] Rebuilt {llms_full_path.name}")


def build_sitemap_xml(catalog: Dict[str, Any]) -> List[str]:
    """
    Rebuilds /sitemap.xml listing https://juizeclubmusic.github.io/, /llms.txt,
    and all /crates/*.html URLs with <lastmod> timestamps.
    Returns the list of all canonical URLs included in the sitemap.
    """
    today_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    urls: List[str] = [
        f"{DOMAIN}/",
        f"{DOMAIN}/llms.txt",
        f"{DOMAIN}/llms-full.txt",
    ]

    for track in catalog.get("tracks", []):
        crate_file = CRATES_DIR / f"{track['id']}.html"
        if crate_file.exists() or track.get("published"):
            urls.append(f"{DOMAIN}/crates/{track['id']}.html")

    url_entries = []
    for u in urls:
        url_entries.append(
            f"""  <url>
    <loc>{html.escape(u)}</loc>
    <lastmod>{today_iso}</lastmod>
  </url>"""
        )

    sitemap_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(url_entries)}
</urlset>
"""
    sitemap_path = ROOT_DIR / "sitemap.xml"
    sitemap_path.write_text(sitemap_xml, encoding="utf-8")
    print(f"[Build] Rebuilt {sitemap_path.name} ({len(urls)} URLs)")
    return urls


def git_commit_and_push() -> None:
    """Runs git add ., git commit, and git push origin main via subprocess.run."""
    print("[Git] Staging all files (git add .)...")
    subprocess.run(["git", "add", "."], cwd=str(ROOT_DIR), check=True)

    print('[Git] Committing changes (git commit -m "Update GEO Entity Hub & Crate Guides")...')
    commit_proc = subprocess.run(
        ["git", "commit", "-m", "Update GEO Entity Hub & Crate Guides"],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
    )
    if commit_proc.returncode != 0:
        combined_output = (commit_proc.stdout or "") + (commit_proc.stderr or "")
        if "nothing to commit" in combined_output.lower():
            print("[Git] Working tree clean; nothing new to commit.")
        else:
            raise subprocess.CalledProcessError(
                commit_proc.returncode,
                commit_proc.args,
                output=commit_proc.stdout,
                stderr=commit_proc.stderr,
            )

    print("[Git] Pushing to origin main (git push origin main)...")
    subprocess.run(["git", "push", "origin", "main"], cwd=str(ROOT_DIR), check=True)


def notify_indexnow(key: str, updated_urls: List[str]) -> None:
    """
    Waits 45 seconds for GitHub Pages deployment, then sends an HTTP POST to
    https://api.indexnow.org/indexnow and prints the HTTP status code.
    """
    print("[IndexNow] Waiting 45 seconds for GitHub Pages deployment...")
    time.sleep(45)

    payload: Dict[str, Any] = {
        "host": HOST,
        "key": key,
        "keyLocation": f"{DOMAIN}/{key}.txt",
        "urlList": updated_urls,
    }

    response = requests.post(
        "https://api.indexnow.org/indexnow",
        headers={"Content-Type": "application/json; charset=utf-8"},
        json=payload,
        timeout=30,
    )
    print(f"[IndexNow] Submitted {len(updated_urls)} URLs -> HTTP Status Code: {response.status_code}")


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    rebuild_all = "--rebuild-all" in argv
    no_git = "--no-git" in argv

    load_dotenv(dotenv_path=ROOT_DIR / ".env")
    api_key = os.getenv("GEMINI_API_KEY", "").strip()

    key = ensure_indexnow_key()
    CRATES_DIR.mkdir(parents=True, exist_ok=True)

    catalog = load_catalog()

    if "--import-suno" in argv:
        idx = argv.index("--import-suno")
        if idx + 1 >= len(argv):
            raise ValueError("Usage: python publisher.py --import-suno <suno_song_url>")
        suno_url = argv[idx + 1].strip()
        import_suno_song(suno_url, catalog)
        if "--only-import" in argv:
            return 0
    entity_profile = catalog.get("entity_profile", {})
    tracks: List[Dict[str, Any]] = catalog.get("tracks", [])

    tracks_to_process = [
        t for t in tracks if rebuild_all or not bool(t.get("published", False))
    ]

    updated_crate_urls: List[str] = []

    if tracks_to_process:
        if not api_key:
            raise EnvironmentError(
                "GEMINI_API_KEY is missing. Please set GEMINI_API_KEY in your .env file."
            )

        for track in tracks_to_process:
            track_id = track["id"]
            print(f"[Gemini] Generating crate guide for '{track['title']}' ({track_id})...")
            article_html = generate_crate_article_html(track, entity_profile, api_key)
            full_page_html = render_crate_page(track, entity_profile, article_html)

            crate_path = CRATES_DIR / f"{track_id}.html"
            crate_path.write_text(full_page_html, encoding="utf-8")
            print(f"[Build] Wrote crate guide: crates/{crate_path.name}")

            track["published"] = True
            updated_crate_urls.append(f"{DOMAIN}/crates/{track_id}.html")
    else:
        print("[Catalog] No unpublished tracks found (use --rebuild-all to regenerate all tracks).")

    build_index_html(catalog)
    build_llms_files(catalog)
    all_sitemap_urls = build_sitemap_xml(catalog)

    save_catalog(catalog)
    print("[Catalog] Updated published flags and saved catalog.json")

    updated_urls = [
        f"{DOMAIN}/",
        f"{DOMAIN}/llms.txt",
        f"{DOMAIN}/llms-full.txt",
        f"{DOMAIN}/sitemap.xml",
    ] + (updated_crate_urls if updated_crate_urls else [
        u for u in all_sitemap_urls if "/crates/" in u
    ])

    if not no_git:
        git_commit_and_push()
        notify_indexnow(key, updated_urls)
    else:
        print("[Git/IndexNow] Skipped git push and IndexNow ping (--no-git passed).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
