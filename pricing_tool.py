#!/usr/bin/env python3
"""
EPL Outright Pricing Comparison Tool
=====================================
Auto-discovers teams and matrix columns from SLOT's OQS endpoint.
Queries PQL expressions against the EPL outrights outcomes matrix.

Usage:
    python pricing_tool.py                    # Run default test scenarios
    python pricing_tool.py --matrix test.json # Run scenarios from a JSON file
    python pricing_tool.py --interactive      # Interactive mode
    python pricing_tool.py --columns          # Show available matrix columns
    python pricing_tool.py --teams            # Show team mapping

OQS ≈ PES (both read the same outcomes matrix from SLOT).
Quants prices come from SOCOUTR's separate simulation — not queryable via API.
"""

import json
import re
import sys
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional

# ─── Configuration ──────────────────────────────────────────────────────────

try:
    import config as _cfg
    SLOT_BASE_URL = _cfg.SLOT_BASE_URL
    DEFAULT_GAME_ID = getattr(_cfg, "DEFAULT_GAME_ID", "40680329")
except ImportError:
    raise SystemExit("config.py not found — copy config.example.py to config.py and set hostnames")


# ─── Data Classes ───────────────────────────────────────────────────────────

@dataclass
class TeamInfo:
    team_id: str
    name: str
    group: str


@dataclass
class MatrixContext:
    """Holds all auto-discovered info about the current matrix."""
    game_id: str
    teams: dict = field(default_factory=dict)           # name -> team_id
    teams_by_id: dict = field(default_factory=dict)     # team_id -> name
    columns: list = field(default_factory=list)         # all column names
    rank_columns: list = field(default_factory=list)    # Rank_Group_Team_N columns
    points_columns: list = field(default_factory=list)  # Points_Team_N columns
    goals_columns: list = field(default_factory=list)   # Goals_Group_Team_N columns
    conceded_columns: list = field(default_factory=list)  # GoalsConceded_Group_Team_N columns
    regional_groups: dict = field(default_factory=dict) # group_name -> [team_names]


@dataclass
class PriceResult:
    scenario_name: str
    pql: str
    oqs_price: Optional[float]
    is_impossible: bool
    error: Optional[str] = None


# ─── Regional Groupings (configurable) ─────────────────────────────────────
# These define which teams belong to each regional/group market.
# Update this when promoted/relegated clubs change.

REGIONAL_GROUPS = {
    "london": ["Arsenal", "Chelsea", "Tottenham", "Crystal Palace", "Fulham", "Brentford"],
    "north west": ["Liverpool", "Man City", "Man Utd", "Everton"],
    "north east": ["Newcastle", "Sunderland"],
    "yorkshire": ["Leeds", "Hull"],
    "midlands": ["Aston Villa", "Coventry", "Nottm Forest"],
    "northern": ["Liverpool", "Man City", "Man Utd", "Everton", "Newcastle", "Sunderland", "Leeds", "Hull"],
    "promoted": ["Leeds", "Coventry", "Hull"],
}


# ─── Auto-Discovery ────────────────────────────────────────────────────────

def discover_matrix(game_id: str) -> MatrixContext:
    """Auto-discover teams and columns from SLOT for the given game."""
    ctx = MatrixContext(game_id=game_id)

    # Fetch metadata (teams)
    try:
        url = f"{SLOT_BASE_URL}/api/ObpQueryServer/GetMetaData?gameId={game_id}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            meta_str = data.get(game_id, "{}")
            meta = json.loads(meta_str) if isinstance(meta_str, str) else meta_str
            team_info = meta.get("TeamInfo", {})
            for tid, info in team_info.items():
                name = info.get("Name", f"Team_{tid}")
                ctx.teams[name] = tid
                ctx.teams_by_id[tid] = name
    except Exception as e:
        print(f"⚠️  Could not fetch metadata: {e}", file=sys.stderr)

    # Fetch columns
    try:
        url = f"{SLOT_BASE_URL}/api/ObpQueryServer/GetColumns?gameId={game_id}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            cols_str = data.get(game_id, "[]")
            ctx.columns = json.loads(cols_str) if isinstance(cols_str, str) else cols_str
            ctx.rank_columns = [c for c in ctx.columns if c.startswith("Rank_Group_Team_")]
            ctx.points_columns = [c for c in ctx.columns if c.startswith("Points_Team_")]
            ctx.goals_columns = [c for c in ctx.columns if c.startswith("Goals_Group_Team_")]
            ctx.conceded_columns = [c for c in ctx.columns if c.startswith("GoalsConceded_Group_Team_")]
    except Exception as e:
        print(f"⚠️  Could not fetch columns: {e}", file=sys.stderr)

    # Build regional groups filtered to actual teams in the matrix
    available_names = set(ctx.teams.keys())
    for group_name, group_teams in REGIONAL_GROUPS.items():
        ctx.regional_groups[group_name] = [t for t in group_teams if t in available_names]

    return ctx


# ─── Team Resolution ───────────────────────────────────────────────────────

def resolve_team(ctx: MatrixContext, team_input: str) -> tuple:
    """Resolve a team name (fuzzy match) to (name, team_id). Returns (name, id)."""
    team_lower = team_input.lower().strip()

    # Exact match first
    for name, tid in ctx.teams.items():
        if name.lower() == team_lower:
            return name, tid

    # Partial match
    for name, tid in ctx.teams.items():
        if team_lower in name.lower() or name.lower() in team_lower:
            return name, tid

    # Try common abbreviations
    abbrevs = {
        "city": "Man City", "united": "Man Utd", "utd": "Man Utd",
        "spurs": "Tottenham", "palace": "Crystal Palace", "villa": "Aston Villa",
        "forest": "Nottm Forest", "brighton": "Brighton", "saints": "Southampton",
    }
    if team_lower in abbrevs:
        resolved_name = abbrevs[team_lower]
        if resolved_name in ctx.teams:
            return resolved_name, ctx.teams[resolved_name]

    available = ", ".join(sorted(ctx.teams.keys()))
    raise ValueError(f"Unknown team: '{team_input}'. Available: {available}")


def rank_col(ctx: MatrixContext, team_name: str) -> str:
    """Get the rank column for a team."""
    _, tid = resolve_team(ctx, team_name)
    return f"Rank_Group_Team_{tid}"


# ─── PQL Builders ───────────────────────────────────────────────────────────

# Market patterns — ordered from most specific to least specific
MARKET_PATTERNS = [
    # Exclusion/negative markets (check first)
    (r"outside top (\d+)", lambda ctx, team, m: f"@{rank_col(ctx, team)} >= {int(m.group(1)) + 1}"),
    (r"bottom half", lambda ctx, team, m: f"@{rank_col(ctx, team)} >= 11"),
    (r"(to be )?relegated", lambda ctx, team, m: f"@{rank_col(ctx, team)} >= 18"),
    (r"(to )?finish bottom", lambda ctx, team, m: f"@{rank_col(ctx, team)} == 20"),
    (r"avoid relegation", lambda ctx, team, m: f"@{rank_col(ctx, team)} <= 17"),

    # Exact position markets
    (r"(outright )?winner", lambda ctx, team, m: f"@{rank_col(ctx, team)} == 1"),
    (r"(to )?finish 2nd", lambda ctx, team, m: f"@{rank_col(ctx, team)} == 2"),
    (r"(to )?finish 3rd", lambda ctx, team, m: f"@{rank_col(ctx, team)} == 3"),
    (r"(to )?finish 4th", lambda ctx, team, m: f"@{rank_col(ctx, team)} == 4"),

    # Top N markets (extract the number dynamically)
    (r"top (\d+) finish|top (\d+)(?! *(london|north|york|midland|promoted|any))",
     lambda ctx, team, m: f"@{rank_col(ctx, team)} <= {int(m.group(1) or m.group(2))}"),

    # Top half
    (r"top half", lambda ctx, team, m: f"@{rank_col(ctx, team)} <= 10"),

    # Regional/group markets (dynamically resolved)
    (r"top london", lambda ctx, team, m: _build_regional_pql(ctx, team, "london")),
    (r"top north west", lambda ctx, team, m: _build_regional_pql(ctx, team, "north west")),
    (r"top north east", lambda ctx, team, m: _build_regional_pql(ctx, team, "north east")),
    (r"top yorkshire", lambda ctx, team, m: _build_regional_pql(ctx, team, "yorkshire")),
    (r"top midlands", lambda ctx, team, m: _build_regional_pql(ctx, team, "midlands")),
    (r"top northern", lambda ctx, team, m: _build_regional_pql(ctx, team, "northern")),
    (r"top promoted", lambda ctx, team, m: _build_regional_pql(ctx, team, "promoted")),

    # Points/Goals markets (uses different columns)
    (r"most points", lambda ctx, team, m: _build_most_pql(ctx, team, "Points_Team_")),
    (r"most goals", lambda ctx, team, m: _build_most_pql(ctx, team, "Goals_Group_Team_")),
    (r"fewest goals conceded", lambda ctx, team, m: _build_fewest_pql(ctx, team, "GoalsConceded_Group_Team_")),
]


def _build_regional_pql(ctx: MatrixContext, team_name: str, group_name: str) -> str:
    """Build PQL for 'top X club' — team must finish higher than all rivals in the group."""
    resolved_name, _ = resolve_team(ctx, team_name)
    group_teams = ctx.regional_groups.get(group_name, [])

    if not group_teams:
        raise ValueError(f"No teams configured for regional group '{group_name}'")
    if resolved_name not in group_teams:
        raise ValueError(f"'{resolved_name}' is not in the '{group_name}' group. Members: {group_teams}")

    rivals = [t for t in group_teams if t != resolved_name]
    if not rivals:
        # Only team in the group — always true if they exist
        return f"@{rank_col(ctx, resolved_name)} >= 1"

    col = rank_col(ctx, resolved_name)
    conditions = [f"@{col} < @{rank_col(ctx, r)}" for r in rivals]
    return " && ".join(conditions)


def _build_most_pql(ctx: MatrixContext, team_name: str, col_prefix: str) -> str:
    """Build PQL for 'most X' — team's value must be highest."""
    resolved_name, tid = resolve_team(ctx, team_name)
    team_col = f"{col_prefix}{tid}"
    other_cols = [c for c in ctx.columns if c.startswith(col_prefix) and c != team_col]
    conditions = [f"@{team_col} > @{c}" for c in other_cols]
    return " && ".join(conditions)


def _build_fewest_pql(ctx: MatrixContext, team_name: str, col_prefix: str) -> str:
    """Build PQL for 'fewest X' — team's value must be lowest."""
    resolved_name, tid = resolve_team(ctx, team_name)
    team_col = f"{col_prefix}{tid}"
    other_cols = [c for c in ctx.columns if c.startswith(col_prefix) and c != team_col]
    conditions = [f"@{team_col} < @{c}" for c in other_cols]
    return " && ".join(conditions)


def build_pql_single(ctx: MatrixContext, market_type: str, team_name: str) -> str:
    """Build PQL for a single market selection using pattern matching."""
    market_lower = market_type.lower().strip()

    for pattern, builder in MARKET_PATTERNS:
        match = re.search(pattern, market_lower)
        if match:
            return builder(ctx, team_name, match)

    raise ValueError(
        f"Unknown market type: '{market_type}'. "
        f"Supported patterns: positional (Top N, Winner, Relegated, etc.), "
        f"regional (Top London/NW/NE/Yorkshire/Midlands/Northern/Promoted Club), "
        f"stats (Most Points, Most Goals, Fewest Goals Conceded)"
    )


def build_pql_combo(ctx: MatrixContext, selections: list) -> str:
    """Build combined PQL for multiple selections."""
    pqls = []
    for sel in selections:
        pql = build_pql_single(ctx, sel["market"], sel["team"])
        pqls.append(f"({pql})" if "&&" in pql else pql)
    return " && ".join(pqls)


# ─── API Client ─────────────────────────────────────────────────────────────

def query_oqs_price(game_id: str, pql: str) -> PriceResult:
    """Query SLOT's OQS endpoint for a price."""
    url = f"{SLOT_BASE_URL}/api/ObpQueryServer/EvaluatePqlPrice?gameId={game_id}"
    try:
        data = json.dumps(pql).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = resp.read().decode().strip()
            if result == 'null' or not result:
                return PriceResult("", pql, None, True)
            price = float(result)
            is_impossible = price >= 9999
            return PriceResult("", pql, price if not is_impossible else None, is_impossible)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return PriceResult("", pql, None, False, error=f"HTTP {e.code}: {body[:100]}")
    except Exception as e:
        return PriceResult("", pql, None, False, error=str(e))


# ─── Test Matrix ────────────────────────────────────────────────────────────

def load_test_matrix(filepath: str) -> list:
    """Load test scenarios from a JSON file."""
    with open(filepath) as f:
        data = json.load(f)
    return data.get("scenarios", data)


def get_default_scenarios() -> list:
    """Return the default test scenarios matching the SODA-1179 test plan."""
    return [
        {"name": "Top 3 Finish SGx - Man City", "selections": [{"market": "Top 3 Finish", "team": "Man City"}]},
        {"name": "Top London Club SGx - Arsenal", "selections": [{"market": "Top London Club", "team": "Arsenal"}]},
        {"name": "Top Half Finish - Leeds", "selections": [{"market": "Top Half", "team": "Leeds"}]},
        {"name": "Bottom Half Finish SGx - Leeds", "selections": [{"market": "Bottom Half", "team": "Leeds"}]},
        {"name": "Top 4 (Newcastle) + Top NE Club (Newcastle)", "selections": [
            {"market": "Top 4 Finish", "team": "Newcastle"},
            {"market": "Top North East Club", "team": "Newcastle"}
        ]},
        {"name": "Winner (Liverpool) + Top NW Club (Liverpool)", "selections": [
            {"market": "Outright Winner", "team": "Liverpool"},
            {"market": "Top North West Club", "team": "Liverpool"}
        ]},
        {"name": "Top Yorkshire (Leeds) + Avoid Relegation (Leeds)", "selections": [
            {"market": "Top Yorkshire Club", "team": "Leeds"},
            {"market": "Avoid Relegation", "team": "Leeds"}
        ]},
        {"name": "Top 3 (Man City) + Relegated (Ipswich)", "selections": [
            {"market": "Top 3 Finish", "team": "Man City"},
            {"market": "To Be Relegated", "team": "Ipswich"}
        ]},
        {"name": "IMPOSSIBLE: Relegated + Top 3 (Man City)", "selections": [
            {"market": "To Be Relegated", "team": "Man City"},
            {"market": "Top 3 Finish", "team": "Man City"}
        ]},
        {"name": "IMPOSSIBLE: Outside Top 4 + Top 3 (Arsenal)", "selections": [
            {"market": "Outside Top 4", "team": "Arsenal"},
            {"market": "Top 3 Finish", "team": "Arsenal"}
        ]},
        {"name": "IMPOSSIBLE: Top London (Arsenal) + Top London (Chelsea)", "selections": [
            {"market": "Top London Club", "team": "Arsenal"},
            {"market": "Top London Club", "team": "Chelsea"}
        ]},
        {"name": "IMPOSSIBLE: 4 teams in Top 3", "selections": [
            {"market": "Top 3 Finish", "team": "Arsenal"},
            {"market": "Top 3 Finish", "team": "Chelsea"},
            {"market": "Top 3 Finish", "team": "Man City"},
            {"market": "Top 3 Finish", "team": "Liverpool"}
        ]},
    ]


# ─── Output ─────────────────────────────────────────────────────────────────

def print_header(ctx: MatrixContext):
    """Print discovery info."""
    print(f"\n🏟️  EPL Outright Pricing Tool")
    print(f"   Game ID: {ctx.game_id}")
    print(f"   Teams: {len(ctx.teams)} discovered")
    print(f"   Matrix columns: {len(ctx.columns)} ({len(ctx.rank_columns)} rank, {len(ctx.points_columns)} points, {len(ctx.goals_columns)} goals)")
    print()


def print_results(results: list):
    """Print results as a formatted table."""
    print("=" * 90)
    print(f"{'#':<3} {'Scenario':<55} {'OQS Price':<12} {'Status'}")
    print("-" * 90)

    for i, r in enumerate(results, 1):
        if r.error:
            status = "❌ ERROR"
            price_str = "-"
        elif r.is_impossible:
            status = "🚫 IMPOSSIBLE"
            price_str = "NULL"
        else:
            status = "✅ OK"
            price_str = f"{r.oqs_price:.4f}"

        print(f"{i:<3} {r.scenario_name:<55} {price_str:<12} {status}")

    print("=" * 90)
    print()
    print("Notes:")
    print("  • OQS Price = SLOT OBP Query Server (evaluates PQL against outcomes matrix)")
    print("  • PES Price ≈ OQS Price (both read the same SLOT matrix)")
    print("  • Quants Price = SOCOUTR's separate simulation (not queryable via API)")
    print("  • NULL/IMPOSSIBLE = 0 simulations satisfy all conditions")
    print()


def print_results_csv(results: list):
    """Print results as CSV."""
    print("Scenario,OQS Price,Status,PQL")
    for r in results:
        if r.error:
            price = "ERROR"
            status = "error"
        elif r.is_impossible:
            price = "NULL"
            status = "impossible"
        else:
            price = f"{r.oqs_price:.4f}"
            status = "valid"
        pql_escaped = r.pql.replace('"', '""')
        print(f'"{r.scenario_name}",{price},{status},"{pql_escaped}"')


# ─── Main Modes ─────────────────────────────────────────────────────────────

def run_scenarios(ctx: MatrixContext, scenarios: list, output_format: str = "table") -> list:
    """Run all scenarios and return results."""
    results = []
    for scenario in scenarios:
        name = scenario["name"]
        selections = scenario["selections"]
        try:
            pql = build_pql_combo(ctx, selections)
            result = query_oqs_price(ctx.game_id, pql)
            result.scenario_name = name
            results.append(result)
        except ValueError as e:
            results.append(PriceResult(name, "", None, False, error=str(e)))

    if output_format == "csv":
        print_results_csv(results)
    else:
        print_results(results)
    return results


def interactive_mode(ctx: MatrixContext):
    """Interactive mode for building and testing scenarios."""
    print_header(ctx)
    print(f"Available teams: {', '.join(sorted(ctx.teams.keys()))}")
    print()
    print("Available markets:")
    print("  Positional: Winner, Top 2, Top 3 Finish, Top 4, ..., Top 8, Top Half")
    print("              Bottom Half, Avoid Relegation, Relegated, To Finish Bottom")
    print("              To Finish 2nd/3rd/4th, Outside Top 4, Outside Top 6")
    print("  Regional:   Top London/North West/North East/Yorkshire/Midlands/Northern/Promoted Club")
    print("  Stats:      Most Points, Most Goals, Fewest Goals Conceded")
    print()
    print("Enter: <market> | <team>  (combine with +)")
    print("Example: Top 3 Finish | Man City + Relegated | Ipswich")
    print("Type 'quit' to exit, 'teams' to list teams, 'columns' to list columns\n")

    while True:
        try:
            user_input = input(">>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in ('quit', 'exit', 'q'):
            break
        if user_input.lower() == 'teams':
            for name in sorted(ctx.teams.keys()):
                print(f"  {ctx.teams[name]:>3}: {name}")
            print()
            continue
        if user_input.lower() == 'columns':
            for col in sorted(ctx.columns):
                print(f"  {col}")
            print()
            continue
        if user_input.lower() == 'groups':
            for gname, members in ctx.regional_groups.items():
                print(f"  {gname}: {', '.join(members)}")
            print()
            continue

        # Parse selections
        try:
            legs = [leg.strip() for leg in user_input.split("+")]
            selections = []
            for leg in legs:
                parts = leg.split("|")
                if len(parts) != 2:
                    print(f"  ❌ Invalid format: '{leg}'. Use: <market> | <team>")
                    break
                selections.append({"market": parts[0].strip(), "team": parts[1].strip()})
            else:
                pql = build_pql_combo(ctx, selections)
                print(f"  PQL: {pql}")
                result = query_oqs_price(ctx.game_id, pql)
                if result.error:
                    print(f"  ❌ Error: {result.error}")
                elif result.is_impossible:
                    print(f"  🚫 IMPOSSIBLE (no simulations satisfy this combination)")
                else:
                    print(f"  💰 OQS Price: {result.oqs_price:.4f} (implied prob: {1/result.oqs_price:.2%})")
                print()
        except ValueError as e:
            print(f"  ❌ {e}\n")


def show_columns(ctx: MatrixContext):
    """Show all available columns grouped by type."""
    print_header(ctx)
    print("Rank columns (final league position):")
    for col in sorted(ctx.rank_columns):
        tid = col.replace("Rank_Group_Team_", "")
        name = ctx.teams_by_id.get(tid, "?")
        print(f"  {col:30} → {name}")
    print()
    print("Points columns:")
    for col in sorted(ctx.points_columns):
        tid = col.replace("Points_Team_", "")
        name = ctx.teams_by_id.get(tid, "?")
        print(f"  {col:30} → {name}")
    print()
    print("Goals columns:")
    for col in sorted(ctx.goals_columns):
        tid = col.replace("Goals_Group_Team_", "")
        name = ctx.teams_by_id.get(tid, "?")
        print(f"  {col:30} → {name}")
    print()
    print("Goals Conceded columns:")
    for col in sorted(ctx.conceded_columns):
        tid = col.replace("GoalsConceded_Group_Team_", "")
        name = ctx.teams_by_id.get(tid, "?")
        print(f"  {col:30} → {name}")


def show_teams(ctx: MatrixContext):
    """Show team mapping and regional groups."""
    print_header(ctx)
    print("Teams in matrix:")
    for name in sorted(ctx.teams.keys()):
        print(f"  {ctx.teams[name]:>3}: {name}")
    print()
    print("Regional groups:")
    for gname, members in sorted(ctx.regional_groups.items()):
        print(f"  {gname:12}: {', '.join(members)}")


# ─── Entry Point ────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="EPL Outright Pricing Tool (auto-discovering)")
    parser.add_argument("--matrix", "-m", help="Path to test matrix JSON file")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--csv", action="store_true", help="Output as CSV")
    parser.add_argument("--columns", action="store_true", help="Show available matrix columns")
    parser.add_argument("--teams", action="store_true", help="Show team mapping and groups")
    parser.add_argument("--game-id", "-g", default=DEFAULT_GAME_ID, help=f"SLOT game/fixture ID (default: {DEFAULT_GAME_ID})")
    parser.add_argument("--raw-pql", help="Evaluate a raw PQL expression directly")
    args = parser.parse_args()

    # Auto-discover matrix context
    ctx = discover_matrix(args.game_id)

    if not ctx.teams:
        print("❌ Could not discover teams. Check network connectivity to SLOT NXT.", file=sys.stderr)
        sys.exit(1)

    if args.columns:
        show_columns(ctx)
    elif args.teams:
        show_teams(ctx)
    elif args.raw_pql:
        result = query_oqs_price(ctx.game_id, args.raw_pql)
        if result.error:
            print(f"❌ {result.error}")
        elif result.is_impossible:
            print("🚫 IMPOSSIBLE")
        else:
            print(f"💰 {result.oqs_price:.4f}")
    elif args.interactive:
        interactive_mode(ctx)
    elif args.matrix:
        scenarios = load_test_matrix(args.matrix)
        print_header(ctx)
        run_scenarios(ctx, scenarios, "csv" if args.csv else "table")
    else:
        print_header(ctx)
        scenarios = get_default_scenarios()
        run_scenarios(ctx, scenarios, "csv" if args.csv else "table")


if __name__ == "__main__":
    main()
