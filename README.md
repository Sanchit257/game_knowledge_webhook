# MarvelLore CI

MarvelLore CI is a lore integrity audit system for the **Marvel Multiverse Role-Playing Game**. When contributors submit new character stats, lore edits, or house rules via GitHub pull requests, MarvelLore CI runs an automated audit against an official knowledge base and posts a structured report back on the PR—before incorrect canon spreads across your campaign documents.

It’s designed for hackathon speed and demo reliability: a local Flask server receives GitHub webhooks over an ngrok tunnel, builds/loads a knowledge base from Marvel RPG PDFs (with a mock fallback dataset), runs an audit using **Human Delta** (with a deterministic local fallback), persists results to SQLite, and streams everything into a live dashboard.

## Architecture

```
Contributor PR / Push
        |
        |  GitHub Webhook (HMAC-SHA256)
        v
   ngrok public URL
        |
        v
 Flask (/webhook/github)  ----->  GitHub API (PyGithub)
        |                         - fetch changed files
        |                         - post PR comment report
        |
        +--> Auditor
        |     - Human Delta audit (preferred)
        |     - Local rule audit (fallback)
        |
        +--> SQLite (data/marvellore.db)
        |     - audits history
        |     - scrape log + system state (tunnel URL)
        |
        +--> Dashboard (Flask + Jinja + Bootstrap)
              - / (live feed)
              - /audit/<id>
              - /characters
              - /demo (one-click scenarios)
```

## Setup

### 1) Install

```bash
cd marvellore-ci
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure environment

Copy the example env file and fill in values:

```bash
cp .env.example .env
```

Required for full functionality:
- `GITHUB_TOKEN`: token with permissions to open PRs + comment
- `GITHUB_REPO`: `owner/repo` (e.g. `Sanchit257/game_knowledge_webhook`)
- `GITHUB_WEBHOOK_SECRET`: secret used when creating the GitHub webhook
- `NGROK_AUTHTOKEN`: ngrok auth token
- `HUMAN_DELTA_API_KEY`: Human Delta API key (optional; local audit fallback works without it)

### 3) Run

```bash
python main.py
```

The server prints the public ngrok URL plus the webhook URL:
- Dashboard: `/`
- Webhook receiver: `/webhook/github`

## Demo

### One-click demo (recommended)

Open:
- `http://localhost:5000/demo` (or the ngrok URL `/demo`)

Click a scenario card to run an automated branch push + PR creation that triggers the real webhook.

### CLI demo simulator

```bash
python demo/simulate_push.py --scenario 2
```

Scenarios:
1. **Clean submission**: Spider-Man matches official stats
2. **Stat mismatch**: Spider-Man Agility set to 4 (official 7)
3. **Faction conflict**: Iron Man submitted as X-Men (official Avengers)

After pushing, the script prints:
`Now watch your dashboard at {url}`

## How ngrok is used

GitHub requires a public HTTPS endpoint for webhooks. During development, MarvelLore CI runs locally, so we use **ngrok** to expose the Flask server to the internet:

- ngrok creates a stable public URL → forwards traffic to `localhost:<FLASK_PORT>`
- GitHub webhooks are configured to point at: `{ngrok_url}/webhook/github`
- The webhook signature is verified using `GITHUB_WEBHOOK_SECRET`

## How Human Delta is used

Human Delta acts as the knowledge infrastructure layer for audits. MarvelLore CI sends:
- the changed file content
- a targeted subset of the local knowledge base (characters/factions likely impacted)
- strict audit instructions for identifying mismatches and errata violations

If Human Delta is unreachable, MarvelLore CI automatically falls back to a deterministic **local audit mode** so the demo remains reliable.

## Tech Stack

| Area | Tech |
|------|------|
| Web server + dashboard | Flask + Jinja2 |
| Styling | Bootstrap 5 CDN + Marvel-themed CSS |
| PDF parsing | pdfplumber |
| GitHub integration | PyGithub + requests |
| Tunneling | pyngrok |
| Scheduling (wired, disabled for hackathon) | APScheduler |
| Storage | SQLite (sqlite3) |
| AI audit layer | Human Delta (via HTTP) |

## Screenshots

Placeholder: add screenshots of the dashboard, audit detail page, and demo runner.

---
Built at **FullHacks 2026**.

