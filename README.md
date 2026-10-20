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

### Rebuild All Tracks
Forces regeneration of all crate HTML guides in `catalog.json` regardless of their `published` status:

```bash
python publisher.py --rebuild-all
```

### Local Dry Run (Skip Git & IndexNow)
To generate and inspect static files locally without running `git commit`/`git push` or calling the IndexNow endpoint:

```bash
python publisher.py --no-git
python publisher.py --rebuild-all --no-git
```
