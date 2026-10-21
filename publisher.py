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
import urllib.parse
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

    title = ""
    artist = "PJZEE"
    release_date = datetime.date.today().isoformat()
    display_tags = "Soulful House, Nu-Disco"
    style_tags = ""

    # 1. Primary: Extract title from OpenGraph meta tag (guaranteed clean song title on Suno)
    og_title_match = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']', page_html, re.IGNORECASE)
    if not og_title_match:
        og_title_match = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:title["\']', page_html, re.IGNORECASE)
    if og_title_match:
        cand_title = html.unescape(og_title_match.group(1)).strip()
        if cand_title and not cand_title.lower().startswith("get the full app") and cand_title.lower() != "suno":
            title = cand_title

    # 2. Extract artist and fallback title from <title> tag ("Song Title by Artist | Suno")
    title_tag_match = re.search(r'<title>(.*?)\s+by\s+(.*?)\s*\|\s*Suno</title>', page_html, re.IGNORECASE)
    if title_tag_match:
        if not title:
            cand_title = html.unescape(title_tag_match.group(1)).strip()
            if not cand_title.lower().startswith("get the full app"):
                title = cand_title
        cand_artist = html.unescape(title_tag_match.group(2)).strip()
        if cand_artist and cand_artist not in ["V6", "V5.5", "V5", "V4", "V3", "V2"]:
            artist = cand_artist

    # 3. Extract artist from meta description ("... by Artist (@handle)...")
    desc_match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'].*?\s+by\s+(.*?)\s+\(@', page_html, re.IGNORECASE)
    if desc_match:
        cand_artist = html.unescape(desc_match.group(1)).strip()
        if cand_artist and cand_artist not in ["V6", "V5.5", "V5", "V4", "V3", "V2"]:
            artist = cand_artist

    # 4. Fallback to Suno user JSON (strictly requiring user_id context to avoid model_badges display_name="V6")
    if not artist or artist in ["V6", "V5.5", "V5", "V4", "V3", "V2"]:
        user_match = re.search(r'\\"user_id\\":\\"[^"\\]+\\",\\"display_name\\":\\"([^"\\]+)\\"', page_html)
        if user_match:
            cand_artist = user_match.group(1)
            if cand_artist not in ["V6", "V5.5", "V5", "V4", "V3", "V2"]:
                artist = cand_artist

    # Fallback to clip JSON title if still empty
    if not title:
        clip_title_match = re.search(r'\\"state\\":\{\\"data\\":\{[^}]*?\\"title\\":\\"([^"\\]+)\\"', page_html)
        if clip_title_match:
            cand_title = clip_title_match.group(1)
            if not cand_title.lower().startswith("get the full app"):
                title = cand_title

    if not title:
        title = "Untitled Track"

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


def import_spotify_song(spotify_url: str, catalog: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetches public metadata from Spotify via oEmbed and OpenGraph tags,
    extracts track title, artist, album, and release date, and either binds
    the spotify_url to an existing track with the same title or creates a new track.
    """
    # Clean up tracking query parameters (e.g. ?si=...)
    clean_url = re.sub(r"\?.*$", "", spotify_url.strip())
    print(f"[Spotify] Fetching metadata from {clean_url} ...")

    title = "Spotify Track"
    artist = "PJZEE"
    release_date = datetime.date.today().isoformat()
    genres = ["Soulful House", "Nu-Disco"]

    # 1. Fetch oEmbed for guaranteed clean title
    try:
        oembed_resp = requests.get(
            f"https://open.spotify.com/oembed?url={clean_url}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        if oembed_resp.status_code == 200:
            oembed_data = oembed_resp.json()
            if oembed_data.get("title"):
                title = oembed_data["title"].strip()
    except Exception as e:
        print(f"[Spotify] oEmbed notice: {e}")

    # 2. Fetch page HTML for og:description (Artist · Album · Song · Year)
    try:
        page_resp = requests.get(
            clean_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=15,
        )
        if page_resp.status_code == 200:
            og_desc = re.search(r'<meta property="og:description" content="([^"]+)"', page_resp.text)
            if og_desc:
                desc_text = html.unescape(og_desc.group(1))
                parts = [p.strip() for p in desc_text.split("·") if p.strip()]
                if parts:
                    artist = parts[0]
                year_match = re.search(r"\b(19\d\d|20\d\d)\b", desc_text)
                if year_match:
                    release_date = f"{year_match.group(1)}-01-01"
    except Exception as e:
        print(f"[Spotify] OpenGraph notice: {e}")

    tracks: List[Dict[str, Any]] = catalog.setdefault("tracks", [])

    # Check if this Spotify URL or title matches an existing track
    existing_track = next(
        (
            t
            for t in tracks
            if t.get("spotify_url") == clean_url
            or t.get("title", "").strip().lower() == title.lower()
        ),
        None,
    )

    if existing_track:
        existing_track["spotify_url"] = clean_url
        existing_track["published"] = False  # Mark for rebuild so Spotify player/button is injected
        print(f"[Spotify] Linked Spotify URL to existing track '{existing_track['title']}' in catalog.json")
        save_catalog(catalog)
        return existing_track

    # Otherwise create a new track
    track_id = slugify(f"{artist}-{title}-122-bpm-soulful-house")
    new_track: Dict[str, Any] = {
        "id": track_id,
        "title": title,
        "artist": artist,
        "label": "JuizeClub",
        "genre": genres,
        "bpm": 122,
        "camelot_key": "8A",
        "release_date": release_date,
        "spotify_url": clean_url,
        "soundcloud_url": "",
        "suno_url": "",
        "vibe_notes": f"Soulful house groove by {artist} released on Spotify.",
        "sonic_descriptors": [
            "warm analog bassline",
            "four-on-the-floor kick",
            "lush chord progressions",
            "soulful vocal production",
        ],
        "dj_set_context": "Peak-Time Soulful House & Sunset Terrace DJ Set",
        "anchor_artists": [
            "Purple Disco Machine - Hypnotized",
            "Folamour - Devoted to U",
            "The Shapeshifters - Lola's Theme",
        ],
        "published": False,
    }

    tracks.insert(0, new_track)
    print(f"[Spotify] Added new Spotify track '{title}' by {artist} ({track_id}) to catalog.json")
    save_catalog(catalog)
    return new_track


def import_soundcloud_song(soundcloud_url: str, catalog: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetches public metadata from SoundCloud via public oEmbed, extracts track
    title, artist, and description, and either binds the soundcloud_url to an
    existing track or creates a new track in catalog.json.
    """
    clean_url = re.sub(r"\?.*$", "", soundcloud_url.strip())
    print(f"[SoundCloud] Fetching metadata from {clean_url} ...")

    title = "SoundCloud Track"
    artist = "PJZEE"
    release_date = datetime.date.today().isoformat()
    vibe_notes = ""

    try:
        oembed_url = f"https://soundcloud.com/oembed?format=json&url={urllib.parse.quote(clean_url, safe='')}"
        resp = requests.get(
            oembed_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            raw_title = data.get("title", "").strip()
            author = data.get("author_name", "").strip()
            vibe_notes = html.unescape(re.sub(r"<[^>]+>", " ", data.get("description", ""))).strip()

            if author:
                artist = author
            if raw_title:
                if f" by {author}" in raw_title:
                    title = raw_title.split(f" by {author}")[0].strip()
                elif " - " in raw_title:
                    parts = raw_title.split(" - ", 1)
                    artist = parts[0].strip()
                    title = parts[1].strip()
                else:
                    title = raw_title
    except Exception as e:
        print(f"[SoundCloud] oEmbed notice: {e}")

    tracks: List[Dict[str, Any]] = catalog.setdefault("tracks", [])

    # Check if this SoundCloud URL or title matches an existing track
    existing_track = next(
        (
            t
            for t in tracks
            if t.get("soundcloud_url") == clean_url
            or t.get("title", "").strip().lower() == title.lower()
        ),
        None,
    )

    if existing_track:
        existing_track["soundcloud_url"] = clean_url
        existing_track["published"] = False  # Mark for rebuild so SoundCloud player/button is injected
        print(f"[SoundCloud] Linked SoundCloud URL to existing track '{existing_track['title']}' in catalog.json")
        save_catalog(catalog)
        return existing_track

    track_id = slugify(f"{artist}-{title}-122-bpm-deep-house")
    new_track: Dict[str, Any] = {
        "id": track_id,
        "title": title,
        "artist": artist,
        "label": "JuizeClub",
        "genre": ["Deep House", "Soulful House"],
        "bpm": 122,
        "camelot_key": "8A",
        "release_date": release_date,
        "spotify_url": "",
        "soundcloud_url": clean_url,
        "suno_url": "",
        "vibe_notes": vibe_notes if vibe_notes else f"Deep soulful house track by {artist} on SoundCloud.",
        "sonic_descriptors": [
            "warm analog bassline",
            "crisp club percussion",
            "four-on-the-floor kick",
            "soulful vocal production",
        ],
        "dj_set_context": "Late-Night Beach Club & Sunset Terrace DJ Set",
        "anchor_artists": [
            "Black Coffee - Drive",
            "Keinemusik - Move",
            "Louie Vega - Elements of Life",
        ],
        "published": False,
    }

    tracks.insert(0, new_track)
    print(f"[SoundCloud] Added new SoundCloud track '{title}' by {artist} ({track_id}) to catalog.json")
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


SVG_SUNO = (
    '<svg class="btn-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="4"></circle>'
    '<path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"></path>'
    "</svg>"
)

SVG_SPOTIFY = (
    '<svg class="btn-icon" viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">'
    '<path d="M12 2C6.477 2 2 6.477 2 12s4.477 10 10 10 10-4.477 10-10S17.523 2 12 2zm4.586 14.424c-.18.295-.563.387-.857.207-2.35-1.434-5.305-1.76-8.786-.963-.335.077-.67-.133-.746-.469-.077-.335.132-.67.469-.746 3.809-.87 7.076-.496 9.713 1.115.293.18.386.563.207.856zm1.224-2.722c-.226.367-.706.482-1.072.257-2.687-1.652-6.785-2.131-9.965-1.166-.413.127-.848-.106-.973-.517-.125-.413.108-.848.52-.973 3.632-1.102 8.147-.568 11.234 1.328.366.226.48.707.256 1.071zm.105-2.835C14.692 8.95 9.375 8.775 6.297 9.71c-.493.15-1.016-.129-1.166-.623-.148-.495.13-1.016.625-1.166 3.532-1.073 9.404-.866 13.115 1.337.445.264.59.838.327 1.282-.264.443-.838.59-1.283.327z"></path>'
    "</svg>"
)

SVG_SOUNDCLOUD = (
    '<svg class="btn-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"></path>'
    "</svg>"
)

SVG_CRATE = (
    '<svg class="btn-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<circle cx="12" cy="12" r="10"></circle>'
    '<circle cx="12" cy="12" r="3"></circle>'
    "</svg>"
)


def build_audio_embeds_html(track: Dict[str, Any]) -> str:
    """
    Builds responsive embedded iframe audio players for Suno, Spotify, and
    SoundCloud when the respective URLs are present on a track.
    """
    embeds: List[str] = []
    title_escaped = html.escape(f"{track.get('title', 'Track')} by {track.get('artist', 'PJZEEE')}")

    suno_url = (track.get("suno_url") or "").strip()
    if suno_url:
        suno_match = re.search(r"suno\.com/(?:song|embed)/([a-zA-Z0-9-]+)", suno_url)
        if suno_match:
            suno_id = suno_match.group(1)
            embed_src = f"https://suno.com/embed/{html.escape(suno_id)}"
            embeds.append(
                f'<div class="player-frame">'
                f'<iframe width="100%" height="140" style="border-radius: 12px; border: 1px solid var(--border);" '
                f'frameborder="0" allowfullscreen '
                f'allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" '
                f'loading="lazy" src="{embed_src}" title="Listen to {title_escaped} on Suno"></iframe>'
                f"</div>"
            )

    spotify_url = (track.get("spotify_url") or "").strip()
    if spotify_url:
        sp_match = re.search(r"open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)", spotify_url)
        if sp_match:
            sp_type, sp_id = sp_match.group(1), sp_match.group(2)
            embed_src = f"https://open.spotify.com/embed/{sp_type}/{html.escape(sp_id)}?utm_source=generator&theme=0"
            embeds.append(
                f'<div class="player-frame">'
                f'<iframe width="100%" height="152" style="border-radius: 12px; border: 1px solid var(--border);" '
                f'frameborder="0" allowfullscreen '
                f'allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture" '
                f'loading="lazy" src="{embed_src}" title="Listen to {title_escaped} on Spotify"></iframe>'
                f"</div>"
            )

    soundcloud_url = (track.get("soundcloud_url") or "").strip()
    if soundcloud_url:
        encoded_sc = urllib.parse.quote(soundcloud_url, safe="")
        embed_src = (
            f"https://w.soundcloud.com/player/?url={encoded_sc}"
            "&color=%2310b981&auto_play=false&hide_related=true"
            "&show_comments=false&show_user=true&show_reposts=false&show_teaser=false"
        )
        embeds.append(
            f'<div class="player-frame">'
            f'<iframe width="100%" height="166" style="border-radius: 12px; border: 1px solid var(--border);" '
            f'scrolling="no" frameborder="no" allow="autoplay" '
            f'loading="lazy" src="{embed_src}" title="Listen to {title_escaped} on SoundCloud"></iframe>'
            f"</div>"
        )

    if not embeds:
        return ""
    return '<div class="audio-embeds">\n          ' + "\n          ".join(embeds) + "\n        </div>"


def build_stream_buttons_html(
    track: Dict[str, Any],
    crate_rel_url: Optional[str] = None,
) -> str:
    """
    Renders clean icon buttons for Crate Guide, Suno, Spotify, and SoundCloud.
    """
    buttons: List[str] = []
    if crate_rel_url:
        buttons.append(
            f'<a class="icon-btn btn-primary" href="{html.escape(crate_rel_url)}">'
            f"{SVG_CRATE}<span>DJ Crate &amp; Harmonic Guide</span></a>"
        )
    if track.get("suno_url"):
        buttons.append(
            f'<a class="icon-btn btn-suno" href="{html.escape(track["suno_url"])}" target="_blank" rel="noopener">'
            f"{SVG_SUNO}<span>Listen on Suno</span></a>"
        )
    if track.get("spotify_url"):
        buttons.append(
            f'<a class="icon-btn btn-spotify" href="{html.escape(track["spotify_url"])}" target="_blank" rel="noopener">'
            f"{SVG_SPOTIFY}<span>Spotify</span></a>"
        )
    if track.get("soundcloud_url"):
        buttons.append(
            f'<a class="icon-btn btn-soundcloud" href="{html.escape(track["soundcloud_url"])}" target="_blank" rel="noopener">'
            f"{SVG_SOUNDCLOUD}<span>SoundCloud</span></a>"
        )
    return "\n          ".join(buttons)


def build_sticky_nav_html(is_root: bool = True) -> str:
    """
    Builds a sticky top navigation bar with Catalog, DJ Crates, and About links.
    """
    prefix = "" if is_root else f"{DOMAIN}/"
    return f"""  <header class="sticky-topbar">
    <div class="topbar-inner">
      <a class="brand" href="{DOMAIN}/">
        <span class="brand-dot"></span>
        JUIZE<span>CLUB</span> <span class="brand-sub">// PJZEEE</span>
      </a>
      <nav class="topbar-nav" aria-label="Primary Navigation">
        <a class="nav-link" href="{prefix}#catalog">Catalog</a>
        <a class="nav-link" href="{prefix}#dj-crates">DJ Crates</a>
        <a class="nav-link" href="{prefix}#about">About</a>
        <a class="nav-pill" href="{DOMAIN}/llms.txt" title="AI &amp; RAG Knowledge Index">llms.txt</a>
      </nav>
    </div>
  </header>"""


SHARED_UI_CSS = """
    :root {
      --bg: #0b0f10;
      --surface: #12181a;
      --surface-alt: #161f22;
      --border: #1f2d31;
      --text: #e5e7eb;
      --muted: #9ca3af;
      --accent: #10b981;
      --accent-dim: rgba(16, 185, 129, 0.14);
    }
    html {
      scroll-behavior: smooth;
    }
    * {
      box-sizing: border-box;
    }
    body {
      margin: 0;
      padding: 0;
      background-color: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, Helvetica, Arial, sans-serif;
      line-height: 1.68;
    }
    a {
      color: var(--accent);
      text-decoration: none;
    }
    a:hover {
      text-decoration: underline;
    }
    section[id] {
      scroll-margin-top: 5rem;
    }
    .sticky-topbar {
      position: sticky;
      top: 0;
      z-index: 1000;
      background: rgba(11, 15, 16, 0.88);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border);
    }
    .topbar-inner {
      max-width: 980px;
      margin: 0 auto;
      padding: 0.85rem 1.25rem;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
    }
    .brand {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      font-weight: 800;
      font-size: 1.05rem;
      letter-spacing: 0.04em;
      color: #ffffff;
      text-decoration: none;
    }
    .brand:hover {
      text-decoration: none;
      opacity: 0.92;
    }
    .brand span {
      color: var(--accent);
    }
    .brand .brand-sub {
      color: var(--muted);
      font-weight: 600;
      font-size: 0.92rem;
    }
    .brand-dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 10px var(--accent);
      display: inline-block;
    }
    .topbar-nav {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.35rem;
    }
    .nav-link {
      color: var(--text);
      font-weight: 600;
      font-size: 0.9rem;
      padding: 0.4rem 0.75rem;
      border-radius: 6px;
      transition: background 0.15s ease, color 0.15s ease;
    }
    .nav-link:hover {
      background: var(--surface-alt);
      color: var(--accent);
      text-decoration: none;
    }
    .nav-pill {
      margin-left: 0.35rem;
      background: var(--accent-dim);
      color: var(--accent);
      border: 1px solid var(--accent);
      padding: 0.3rem 0.7rem;
      border-radius: 999px;
      font-size: 0.78rem;
      font-weight: 700;
    }
    .nav-pill:hover {
      background: var(--accent);
      color: #04130e;
      text-decoration: none;
    }
    .audio-embeds {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      margin: 1.1rem 0 1.25rem;
    }
    .player-frame {
      width: 100%;
      overflow: hidden;
      border-radius: 12px;
      background: var(--surface-alt);
    }
    .player-frame iframe {
      display: block;
      width: 100%;
    }
    .card-actions, .stream-links, .profile-links {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.65rem;
    }
    .icon-btn {
      display: inline-flex;
      align-items: center;
      gap: 0.45rem;
      padding: 0.48rem 0.95rem;
      border-radius: 8px;
      font-size: 0.88rem;
      font-weight: 600;
      border: 1px solid var(--border);
      background: var(--surface-alt);
      color: var(--text);
      transition: all 0.16s ease;
      text-decoration: none;
    }
    .icon-btn:hover {
      border-color: var(--accent);
      background: var(--accent-dim);
      color: var(--accent);
      transform: translateY(-1px);
      text-decoration: none;
    }
    .icon-btn.btn-primary {
      background: var(--accent);
      color: #04130e;
      border-color: var(--accent);
      font-weight: 700;
    }
    .icon-btn.btn-primary:hover {
      background: #34d399;
      border-color: #34d399;
      color: #04130e;
    }
    .btn-icon {
      flex-shrink: 0;
    }
"""


def render_crate_page(
    track: Dict[str, Any],
    entity_profile: Dict[str, Any],
    article_body_html: str,
) -> str:
    """
    Wraps the generated article in a clean, dark-mode responsive HTML5 template
    (#0b0f10 background, #10b981 emerald accents) with sticky top navigation,
    embedded audio players, and Schema.org @graph JSON-LD block in <head>.
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

    sticky_nav_html = build_sticky_nav_html(is_root=False)
    audio_embeds_html = build_audio_embeds_html(track)
    stream_btns_html = build_stream_buttons_html(track, crate_rel_url=None)

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
{SHARED_UI_CSS}
    .container {{
      max-width: 920px;
      margin: 0 auto;
      padding: 2rem 1.25rem 4rem;
    }}
    .meta-strip {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.6rem;
      margin-bottom: 1.5rem;
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
      border-radius: 12px;
      padding: 1.35rem 1.5rem;
      margin-bottom: 2.25rem;
    }}
    .track-hero-header {{
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 0.75rem;
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
{sticky_nav_html}
  <div class="container">
    <div class="meta-strip">
      <span class="badge">{html.escape(track['artist'])}</span>
      <span class="badge">{track['bpm']} BPM</span>
      <span class="badge">Camelot {html.escape(track['camelot_key'])}</span>
      <span class="badge">{html.escape(genres_str)}</span>
      <span class="badge">Label: {html.escape(track.get('label', 'JuizeClub'))}</span>
    </div>

    <aside class="track-hero-card">
      <div class="track-hero-header">
        <div>
          <strong>Featured Release:</strong> {html.escape(track['title'])} by {html.escape(track['artist'])}
          <span style="color: var(--muted); font-size: 0.9rem;"> &bull; {track['bpm']} BPM &bull; Key {html.escape(track['camelot_key'])} &bull; {html.escape(track['release_date'])}</span>
        </div>
      </div>
      <div style="color: var(--muted); font-size: 0.93rem; margin-top: 0.4rem;">
        {html.escape(track.get('vibe_notes', ''))}
      </div>
      {audio_embeds_html}
      <div class="stream-links" style="margin-top: 0.85rem;">
        {stream_btns_html}
        <a class="icon-btn" href="{DOMAIN}/#catalog">&larr; Back to Catalog</a>
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
            f"""        <li class="crate-item">
          <div class="crate-item-main">
            <a class="crate-item-title" href="{html.escape(crate_rel_url)}">{SVG_CRATE}<span>{html.escape(crate_title)}</span></a>
            <div class="muted" style="font-size: 0.9rem; margin-top: 0.25rem;">
              Featuring <strong>"{html.escape(track['title'])}"</strong> by {html.escape(track['artist'])} ({track['bpm']} BPM &bull; Camelot {html.escape(track['camelot_key'])})
            </div>
          </div>
          <a class="icon-btn btn-primary" href="{html.escape(crate_rel_url)}">Open Crate Guide &rarr;</a>
        </li>"""
        )

        audio_embeds_html = build_audio_embeds_html(track)
        card_stream_btns_html = build_stream_buttons_html(track, crate_rel_url=crate_rel_url)

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
        {audio_embeds_html}
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

    profile_btns: List[str] = []
    if official_urls.get("suno_profile_url"):
        profile_btns.append(
            f'<a class="icon-btn" href="{html.escape(official_urls["suno_profile_url"])}" target="_blank" rel="noopener">'
            f"{SVG_SUNO}<span>Suno Profile</span></a>"
        )
    if official_urls.get("spotify_artist_url"):
        profile_btns.append(
            f'<a class="icon-btn" href="{html.escape(official_urls["spotify_artist_url"])}" target="_blank" rel="noopener">'
            f"{SVG_SPOTIFY}<span>Spotify Artist</span></a>"
        )
    if official_urls.get("soundcloud_profile_url"):
        profile_btns.append(
            f'<a class="icon-btn" href="{html.escape(official_urls["soundcloud_profile_url"])}" target="_blank" rel="noopener">'
            f"{SVG_SOUNDCLOUD}<span>SoundCloud</span></a>"
        )
    profile_btns.append(
        f'<a class="icon-btn" href="{DOMAIN}/llms.txt"><span>AI Index (/llms.txt)</span></a>'
    )
    profile_btns.append(
        f'<a class="icon-btn" href="{DOMAIN}/llms-full.txt"><span>Full RAG Corpus (/llms-full.txt)</span></a>'
    )
    profile_btns_html = "\n        ".join(profile_btns)
    sticky_nav_html = build_sticky_nav_html(is_root=True)

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
{SHARED_UI_CSS}
    .container {{
      max-width: 980px;
      margin: 0 auto;
      padding: 2rem 1.25rem 4rem;
    }}
    section.hero {{
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
      margin-top: 1.35rem;
    }}
    .crate-list {{
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
    }}
    .crate-item {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 1.15rem 1.35rem;
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: center;
      gap: 1rem;
    }}
    .crate-item-main {{
      flex: 1 1 420px;
    }}
    .crate-item-title {{
      display: inline-flex;
      align-items: center;
      gap: 0.5rem;
      font-weight: 700;
      font-size: 1.02rem;
      color: #ffffff;
    }}
    .crate-item-title svg {{
      color: var(--accent);
    }}
    .crate-item-title:hover {{
      color: var(--accent);
      text-decoration: none;
    }}
    .track-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 12px;
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
      margin: 1rem 0 1.25rem;
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
{sticky_nav_html}
  <div class="container">
    <section class="hero" id="about">
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
        {profile_btns_html}
      </div>
    </section>

    <section id="catalog">
      <h2>Catalog &amp; Interactive Audio Players</h2>
{chr(10).join(track_cards_html)}
    </section>

    <section id="dj-crates">
      <h2>Curated DJ Crate &amp; Harmonic Mixing Guides</h2>
      <ul class="crate-list">
{chr(10).join(crate_links_html)}
      </ul>
    </section>

    <footer>
      <div>&copy; {datetime.date.today().year} JuizeClub Record Label &amp; PJZEEE. All rights reserved.</div>
      <div>
        <a href="#catalog">Catalog</a> &bull;
        <a href="#dj-crates">DJ Crates</a> &bull;
        <a href="#about">About</a> &bull;
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
    nojekyll_file = ROOT_DIR / ".nojekyll"
    if not nojekyll_file.exists():
        nojekyll_file.write_text("# Disable Jekyll for GitHub Pages\n", encoding="utf-8")

    # Remove venv from git tracking if it was accidentally committed
    subprocess.run(["git", "rm", "-r", "--cached", "venv"], cwd=str(ROOT_DIR), capture_output=True)

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

    rebuild_track_query: Optional[str] = None
    if "--rebuild-track" in argv:
        idx = argv.index("--rebuild-track")
        if idx + 1 >= len(argv):
            raise ValueError("Usage: python publisher.py --rebuild-track <track_id_or_keyword>")
        rebuild_track_query = argv[idx + 1].strip().lower()

    if "--import-suno" in argv:
        idx = argv.index("--import-suno")
        if idx + 1 >= len(argv):
            raise ValueError("Usage: python publisher.py --import-suno <suno_song_url>")
        suno_url = argv[idx + 1].strip()
        import_suno_song(suno_url, catalog)
        if "--only-import" in argv:
            return 0

    if "--import-spotify" in argv:
        idx = argv.index("--import-spotify")
        if idx + 1 >= len(argv):
            raise ValueError("Usage: python publisher.py --import-spotify <spotify_track_url>")
        spotify_url = argv[idx + 1].strip()
        import_spotify_song(spotify_url, catalog)
        if "--only-import" in argv:
            return 0

    if "--import-soundcloud" in argv:
        idx = argv.index("--import-soundcloud")
        if idx + 1 >= len(argv):
            raise ValueError("Usage: python publisher.py --import-soundcloud <soundcloud_track_url>")
        soundcloud_url = argv[idx + 1].strip()
        import_soundcloud_song(soundcloud_url, catalog)
        if "--only-import" in argv:
            return 0
    entity_profile = catalog.get("entity_profile", {})
    tracks: List[Dict[str, Any]] = catalog.get("tracks", [])

    if rebuild_track_query:
        tracks_to_process = [
            t for t in tracks
            if rebuild_track_query in t.get("id", "").lower()
            or rebuild_track_query in t.get("title", "").lower()
        ]
        if not tracks_to_process:
            print(f"[Catalog] No tracks matched query '{rebuild_track_query}'. Available tracks:")
            for t in tracks:
                print(f"  - {t.get('id')}: {t.get('title')}")
            return 1
        print(f"[Catalog] Target rebuild for {len(tracks_to_process)} track(s): {[t['title'] for t in tracks_to_process]}")
    elif rebuild_all:
        tracks_to_process = list(tracks)
    else:
        tracks_to_process = [
            t for t in tracks if not bool(t.get("published", False))
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

    # Refresh layout/player/nav wrapper on already-published crate pages without re-calling Gemini
    for track in tracks:
        if track in tracks_to_process:
            continue
        crate_path = CRATES_DIR / f"{track['id']}.html"
        if crate_path.exists():
            existing_html = crate_path.read_text(encoding="utf-8")
            main_match = re.search(r"<main>(.*?)</main>", existing_html, flags=re.DOTALL | re.IGNORECASE)
            if main_match:
                article_body = main_match.group(1).strip()
                refreshed_html = render_crate_page(track, entity_profile, article_body)
                crate_path.write_text(refreshed_html, encoding="utf-8")
                print(f"[Build] Refreshed UI template for crates/{crate_path.name}")

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
