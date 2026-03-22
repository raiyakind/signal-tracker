# Signal — Congressional Trade Intelligence

A personal trade tracker that monitors STOCK Act disclosures for four
carefully selected pilots and surfaces them as a clean, actionable dashboard.

## Pilots

| Pilot | Party | Why |
|---|---|---|
| Terri Sewell | D-AL | Healthcare equity + clean utilities. +67.9% in 2025 |
| Ro Khanna | D-CA | AI democratist. Most active trader in Congress |
| Lisa Murkowski | R-AK | Senate Energy Chair. Nuclear + grid infrastructure |
| Lisa McClain | R-MI | House Conference Chair. AI infrastructure early mover |

## File Structure

```
/
├── index.html          ← The full frontend app
├── trades.json         ← Written by the poller every 15 min (start: empty)
├── netlify.toml        ← Netlify deployment config
└── .github/
    ├── workflows/
    │   └── poll.yml    ← GitHub Actions cron job
    └── scripts/
        └── poll_trades.py  ← Python poller
```

## Deploy Steps (for Claude Coworker)

1. Create a new GitHub repo (e.g. `signal-tracker`)
2. Push all files in this folder to the repo root
3. Go to netlify.com → New site from Git → connect the repo
4. Netlify will auto-deploy — no build command needed (static site)
5. Go to the GitHub repo Settings → Actions → make sure Actions are enabled
6. The poller runs automatically every 15 min on weekdays during market hours
7. First run will populate trades.json with real disclosures; Netlify
   auto-deploys on every commit so the site updates within ~1 minute

## Data Flow

```
housestockwatcher.com  ──┐
                         ├──► poll_trades.py ──► trades.json ──► GitHub commit
senatestockwatcher.com ──┘                                       ──► Netlify deploy
                                                                  ──► index.html fetch()
```

## Notes

- The frontend fetches trades.json on load and re-fetches every 15 minutes
- If trades.json is empty or missing, sample data is shown automatically
- The "Execute via Schwab" button requires separate Schwab OAuth setup
  (must be done manually by the account holder — cannot be automated)
