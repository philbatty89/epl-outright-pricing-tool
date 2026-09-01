# EPL Outright Pricing Comparison Tool

Queries SLOT's OQS (OBP Query Server) to evaluate pricing for EPL outright market combinations.

## Quick Start

```bash
# Run default SODA-1179 test scenarios
python pricing_tool.py

# Run your own test matrix
python pricing_tool.py --matrix test_matrix.json

# Interactive mode - type combinations and get instant prices
python pricing_tool.py --interactive

# Output as CSV
python pricing_tool.py --csv
```

## Test Matrix Format

Create a JSON file with scenarios:

```json
{
  "scenarios": [
    {
      "name": "My test scenario",
      "selections": [
        {"market": "Top 3 Finish", "team": "Man City"},
        {"market": "To Be Relegated", "team": "Ipswich"}
      ]
    }
  ]
}
```

## Available Markets

| Market | What it means |
|--------|---------------|
| Outright Winner | Finishes 1st |
| To Finish 2nd | Finishes exactly 2nd |
| To Finish 3rd | Finishes exactly 3rd |
| To Finish 4th | Finishes exactly 4th |
| Top 2 Finish | Finishes 1st or 2nd |
| Top 3 Finish | Finishes 1st, 2nd, or 3rd |
| Top 4 Finish | Finishes 1st-4th |
| Top 5 Finish | Finishes 1st-5th |
| Top 6 Finish | Finishes 1st-6th |
| Top 7 Finish | Finishes 1st-7th |
| Top 8 Finish | Finishes 1st-8th |
| Top Half | Finishes 1st-10th |
| Bottom Half | Finishes 11th-20th |
| Avoid Relegation | Finishes 1st-17th |
| To Be Relegated | Finishes 18th-20th |
| To Finish Bottom | Finishes 20th |
| Outside Top 4 | Finishes 5th or worse |
| Outside Top 6 | Finishes 7th or worse |
| Top London Club | Highest-finishing London club |
| Top North West Club | Highest-finishing NW club |
| Top North East Club | Highest-finishing NE club |
| Top Yorkshire Club | Highest-finishing Yorkshire club |
| Top Midlands Club | Highest-finishing Midlands club |
| Top Northern Club | Highest-finishing Northern club |
| Top Promoted Club | Highest-finishing promoted club |

## Available Teams (2026/27)

Arsenal, Aston Villa, Bournemouth, Brentford, Brighton, Chelsea, Coventry,
Crystal Palace, Everton, Fulham, Hull, Ipswich, Leeds, Liverpool, Man City,
Man Utd, Newcastle, Nottm Forest, Sunderland, Tottenham

## Interactive Mode Example

```
>>> Top 3 Finish | Man City
  PQL: @Rank_Group_Team_15 <= 3
  💰 OQS Price: 1.4900

>>> Top 3 Finish | Man City + To Be Relegated | Ipswich
  PQL: @Rank_Group_Team_15 <= 3 && @Rank_Group_Team_12 >= 18
  💰 OQS Price: 2.7856

>>> To Be Relegated | Man City + Top 3 Finish | Man City
  PQL: @Rank_Group_Team_15 >= 18 && @Rank_Group_Team_15 <= 3
  🚫 IMPOSSIBLE (no simulations satisfy this combination)
```

## How It Works

- **OQS** = SLOT's OBP Query Server. Evaluates PQL expressions against the outcomes matrix (40,000 simulated EPL seasons).
- **PES** ≈ OQS (both read the same matrix). Not directly queryable from CLI.
- **Quants** = SOCOUTR's separate Monte Carlo simulation. Different model, similar results. Not queryable via API.

## Configuration

Default: NXT environment, EPL 2026/27 (gameId=40680329)

To use a different fixture:
```bash
python pricing_tool.py --game-id 12345678
```
