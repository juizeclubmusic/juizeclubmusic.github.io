# JuizeClub & PJZEEE — GEO Static Site Generator & IndexNow Publisher

Zero-dependency (`requests` + `python-dotenv` only) Python 3.9+ Generative Engine Optimization (GEO) static site generator and IndexNow publisher for **[https://juizeclubmusic.github.io](https://juizeclubmusic.github.io)**.

## Setup

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Create a `.env` file in the root directory containing your Gemini API key:
   ```env
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

## Usage

### Publish New (Unpublished) Tracks
Processes only tracks in `catalog.json` where `"published": false`, generates `crates/{id}.html` guides via `gemini-3.8-flash`, rebuilds `index.html`, `llms.txt`, `llms-full.txt`, and `sitemap.xml`, pushes to GitHub Pages (`main`), waits 45 seconds, and pings IndexNow:

```bash
python publisher.py
```

### 1-Command Auto-Import from Suno or Spotify
To automatically scrape song metadata from Suno or Spotify, generate the crate guide, embed the player, and publish live:

```bash
# Auto-import from Suno and publish:
python publisher.py --import-suno https://suno.com/song/YOUR_SUNO_UUID

# Auto-import from Spotify (or link Spotify URL to an existing track) and publish:
python publisher.py --import-spotify https://open.spotify.com/track/YOUR_SPOTIFY_ID

# Auto-import from SoundCloud (or link SoundCloud URL to an existing track) and publish:
python publisher.py --import-soundcloud https://soundcloud.com/YOUR_ARTIST/YOUR_TRACK_SLUG
```
*(Add `--only-import` to add the track to `catalog.json` without immediately building or publishing).*

### Rebuild All Tracks
Forces regeneration of all crate HTML guides in `catalog.json` regardless of their `published` status:

```bash
python publisher.py --rebuild-all
```

### Rebuild Only One Specific Track (Fast)
To regenerate only a single track (by title keyword or track id) without re-running the entire catalog:

```bash
python publisher.py --rebuild-track "Don't You Forget"
```

### Local Dry Run (Skip Git & IndexNow)
To generate and inspect static files locally without running `git commit`/`git push` or calling the IndexNow endpoint:

```bash
python publisher.py --no-git
python publisher.py --rebuild-all --no-git
```
