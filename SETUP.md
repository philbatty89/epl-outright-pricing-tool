# EPL Outright Pricing Tool — Setup

A local tool for testing EPL outright market pricing (OQS, PES, SOCOQS).

## Requirements

- **Python 3** (already installed on every Mac — check with `python3 --version`)
- **Corporate network access** — the tool connects to internal services
  (SLOT, SOCOQS, PES). You must be on the VPN / office network, and have
  `config.py` set up with the correct internal hostnames.
- No other dependencies — it uses only the Python standard library.

## How to run

1. Clone the repo (one time):
   ```bash
   git clone https://github.com/philbatty89/epl-outright-pricing-tool.git
   cd epl-outright-pricing-tool
   ```
2. Set up the config (one time) — copy the template and fill in the internal hostnames:
   ```bash
   cp config.example.py config.py
   # edit config.py with the real hostnames (ask a colleague / check the runbook)
   ```
3. Start the app:
   ```bash
   python3 server.py
   ```
4. Open your browser at **http://localhost:8080**

To stop it, press `Ctrl+C` in the Terminal.

## Getting updates

The app shows its version in the top-right. When a newer version is available,
a green banner appears. To update:

```bash
git pull
# then restart: Ctrl+C and run `python3 server.py` again
```

## If port 8080 is busy

Run it on a different port:
```bash
python3 server.py --port 8081
```
Then open http://localhost:8081

## What it does

- **Results** — build outright scenarios, get OQS / PES / SOCOQS prices, spot impossible combos
- **Quick Singles** — all teams' prices for a market at once
- **Batch Runner** — upload a JSON/CSV of scenarios, run them all, export results
- **Auto-Scan** — test every 2/3/4-leg combo for a team, filter valid vs impossible
- **PETS Round-Trip** — verify a translated expression matches the original
- **Team Stats** — distribution analysis (mean, median, percentiles) per team
- **History** — logs all queries; select + export to Confluence

Decimal/Fraction toggle and dark/light theme in the top-right.

## Notes

- If SLOT is down, the app automatically falls back to SOCOQS for pricing (EPL only).
- PES needs SLOT to be up (it uses selection RAMP IDs).
