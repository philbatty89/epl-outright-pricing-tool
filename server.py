#!/usr/bin/env python3
"""
EPL Outright Pricing Tool — Web UI
===================================
A self-contained web server with GUI. No dependencies beyond Python 3 stdlib.

Usage:
    python server.py          # Starts on http://localhost:8080
    python server.py --port 9000
"""

import http.server
import json
import os
import sys
import time
import urllib.request
import urllib.error
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

PORT = 8080

# ─── Load config (internal hostnames live in gitignored config.py) ──────────
try:
    import config as _cfg
    SLOT_BASE_URL = _cfg.SLOT_BASE_URL
    PES_BASE_URL = _cfg.PES_BASE_URL
    SPRINGBOX_BASE_URL = _cfg.SPRINGBOX_BASE_URL
    SOCOQS_BASE_URL = _cfg.SOCOQS_BASE_URL
    _CONFIG_DEFAULT_GAME_ID = getattr(_cfg, "DEFAULT_GAME_ID", "40680329")
except ImportError:
    print("\n❌ config.py not found. Copy config.example.py to config.py and set the hostnames:")
    print("     cp config.example.py config.py\n")
    sys.exit(1)

# ─── Version / Update Check ─────────────────────────────────────────────────
def _read_local_version():
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION")) as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"

APP_VERSION = _read_local_version()
# Raw VERSION file on the default branch — used to detect newer releases
VERSION_CHECK_URL = "https://raw.githubusercontent.com/philbatty89/epl-outright-pricing-tool/main/VERSION"


def _version_tuple(v):
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except Exception:
        return (0,)

# Hardcoded team maps for when SLOT metadata is unavailable (fallback).
# Team numbers are stable per competition matrix.
FALLBACK_TEAMS = {
    "40680329": {  # EPL 2026/27
        "1": {"TeamId": "1", "Name": "Arsenal", "Group": "A"},
        "2": {"TeamId": "2", "Name": "Aston Villa", "Group": "A"},
        "3": {"TeamId": "3", "Name": "Bournemouth", "Group": "A"},
        "4": {"TeamId": "4", "Name": "Brentford", "Group": "A"},
        "5": {"TeamId": "5", "Name": "Brighton", "Group": "A"},
        "6": {"TeamId": "6", "Name": "Chelsea", "Group": "A"},
        "7": {"TeamId": "7", "Name": "Coventry", "Group": "A"},
        "8": {"TeamId": "8", "Name": "Crystal Palace", "Group": "A"},
        "9": {"TeamId": "9", "Name": "Everton", "Group": "A"},
        "10": {"TeamId": "10", "Name": "Fulham", "Group": "A"},
        "11": {"TeamId": "11", "Name": "Hull", "Group": "A"},
        "12": {"TeamId": "12", "Name": "Ipswich", "Group": "A"},
        "13": {"TeamId": "13", "Name": "Leeds", "Group": "A"},
        "14": {"TeamId": "14", "Name": "Liverpool", "Group": "A"},
        "15": {"TeamId": "15", "Name": "Man City", "Group": "A"},
        "16": {"TeamId": "16", "Name": "Man Utd", "Group": "A"},
        "17": {"TeamId": "17", "Name": "Newcastle", "Group": "A"},
        "18": {"TeamId": "18", "Name": "Nottm Forest", "Group": "A"},
        "19": {"TeamId": "19", "Name": "Sunderland", "Group": "A"},
        "20": {"TeamId": "20", "Name": "Tottenham", "Group": "A"},
    },
}
DEFAULT_GAME_ID = _CONFIG_DEFAULT_GAME_ID
SNAPSHOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")
STALENESS_BASELINE_FILE = os.path.join(SNAPSHOTS_DIR, "_staleness_baseline.json")

# ─── API Proxy ──────────────────────────────────────────────────────────────

def fetch_metadata(game_id):
    url = f"{SLOT_BASE_URL}/api/ObpQueryServer/GetMetaData?gameId={game_id}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            meta_str = data.get(game_id, "{}")
            return json.loads(meta_str) if isinstance(meta_str, str) else meta_str
    except (urllib.error.HTTPError, urllib.error.URLError):
        # SLOT down — fall back to hardcoded team map for known competitions
        fallback = FALLBACK_TEAMS.get(game_id)
        if fallback:
            return {"TeamInfo": fallback, "_fallback": True}
        raise Exception(f"SLOT is down and no fallback team map for game {game_id}")


def fetch_columns(game_id):
    url = f"{SLOT_BASE_URL}/api/ObpQueryServer/GetColumns?gameId={game_id}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            cols_str = data.get(game_id, "[]")
            return json.loads(cols_str) if isinstance(cols_str, str) else cols_str
    except (urllib.error.HTTPError, urllib.error.URLError):
        # Fall back to SOCOQS columns
        try:
            url2 = f"{SOCOQS_BASE_URL}/{game_id}/columns"
            req2 = urllib.request.Request(url2)
            with urllib.request.urlopen(req2, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception:
            return []


def evaluate_pql(game_id, pql):
    url = f"{SLOT_BASE_URL}/api/ObpQueryServer/EvaluatePqlPrice?gameId={game_id}"
    data = json.dumps(pql).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = resp.read().decode().strip()
            if result == 'null' or not result:
                return None
            price = float(result)
            return None if price >= 9999 else price
    except urllib.error.HTTPError as e:
        # SLOT down (503 no healthy upstream) — fall back to SOCOQS
        if e.code in (502, 503, 504):
            prob = evaluate_socoqs(game_id, pql)
            if prob and prob > 0:
                return 1.0 / prob
            return None
        raise
    except urllib.error.URLError:
        # Network / connection issue — try SOCOQS
        prob = evaluate_socoqs(game_id, pql)
        if prob and prob > 0:
            return 1.0 / prob
        return None


def evaluate_socoqs(src_id, pql):
    """Evaluate a PQL expression directly against SOCOQS. Returns probability (0-1)."""
    # SOCOQS wants: probability(<expr>) as plain text
    expr = pql.strip()
    if not expr.lower().startswith('probability('):
        expr = f'probability({expr})'
    url = f"{SOCOQS_BASE_URL}/{src_id}/evaluate"
    data = expr.encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'text/plain'}, method='POST')
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = resp.read().decode().strip()
        try:
            prob = float(result)
            return prob
        except ValueError:
            return None


def evaluate_pes(game_id, selection_ids):
    """Query PES for combined probability from selection IDs."""
    lines = [{"id": {"eventId": game_id, "selectionId": sid}} for sid in selection_ids]
    data = json.dumps({"lines": lines}).encode('utf-8')
    req = urllib.request.Request(PES_BASE_URL, data=data, method='POST')
    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'application/json')
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
    if result.get('status') != 'SUCCESS':
        return None
    prob = result['probability']
    p = prob['numerator'] / prob['denominator']
    return 1/p if p > 0 else None


def get_selections(game_id):
    """Get all selections with their RAMP IDs from SLOT. Falls back to a
    reconstructed market map (no RAMP IDs) if SLOT is down."""
    url = f"{SLOT_BASE_URL}/api/ObpEvent/GetByRampIdWithMarkets/{game_id}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        selections = {}
        for m in data.get('obpMarkets', []):
            market_name = m.get('marketName', '')
            market_type = m.get('marketType', '')
            for s in m.get('obpSelections', []):
                selections[f"{market_name}|{s.get('name', '')}"] = {
                    "selectionRampId": str(s.get('selectionRampId', '')),
                    "market": market_name,
                    "marketType": market_type,
                    "team": s.get('name', ''),
                    "evalJson": s.get('evalJson', ''),
                }
        return selections
    except (urllib.error.HTTPError, urllib.error.URLError):
        # SLOT down — reconstruct market map from fallback teams + deterministic PQL.
        # No RAMP IDs (so PES won't work) but OQS/SOCOQS pricing still works.
        teams = FALLBACK_TEAMS.get(game_id)
        if not teams:
            raise Exception(f"SLOT down and no fallback for game {game_id}")
        selections = {}
        # Standard EPL outright markets → PQL generator
        market_pql = {
            "Outright Winner": lambda n: f"@Rank_Group_Team_{n} == 1",
            "Top 2 Finish": lambda n: f"@Rank_Group_Team_{n} <= 2",
            "Top 3 Finish": lambda n: f"@Rank_Group_Team_{n} <= 3",
            "Top 4 Finish": lambda n: f"@Rank_Group_Team_{n} <= 4",
            "Top 5 Finish": lambda n: f"@Rank_Group_Team_{n} <= 5",
            "Top 6 Finish": lambda n: f"@Rank_Group_Team_{n} <= 6",
            "Top 7 Finish": lambda n: f"@Rank_Group_Team_{n} <= 7",
            "Top 8 Finish": lambda n: f"@Rank_Group_Team_{n} <= 8",
            "Top Half Finish": lambda n: f"@Rank_Group_Team_{n} <= 10",
            "Bottom Half Finish": lambda n: f"@Rank_Group_Team_{n} >= 11",
            "Avoid Relegation": lambda n: f"@Rank_Group_Team_{n} <= 17",
            "To Be Relegated": lambda n: f"@Rank_Group_Team_{n} >= 18",
            "To Finish Bottom": lambda n: f"@Rank_Group_Team_{n} == 20",
            "To Finish 2nd": lambda n: f"@Rank_Group_Team_{n} == 2",
            "To Finish 3rd": lambda n: f"@Rank_Group_Team_{n} == 3",
            "To Finish 4th": lambda n: f"@Rank_Group_Team_{n} == 4",
            "To Finish Outside Top 4": lambda n: f"@Rank_Group_Team_{n} >= 5",
            "To Finish Outside Top 6": lambda n: f"@Rank_Group_Team_{n} >= 7",
        }
        for tid, info in teams.items():
            name = info['Name']
            for market, pql_fn in market_pql.items():
                selections[f"{market}|{name}"] = {
                    "selectionRampId": "",  # unknown when SLOT down
                    "market": market,
                    "marketType": "",
                    "team": name,
                    "evalJson": pql_fn(tid),
                }
        return selections


# ─── Snapshot & Staleness Helpers ───────────────────────────────────────────

def ensure_snapshots_dir():
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)


def get_all_prices(game_id):
    """Fetch prices for all selections via OQS — uses batch evaluation for speed."""
    selections = get_selections(game_id)
    prices = {}
    
    # Build batch request for MultiEvaluatePqlPrice
    batch_selections = []
    key_order = []
    for key, sel in selections.items():
        pql = sel.get('evalJson', '')
        if pql:
            batch_selections.append({
                "id": len(batch_selections),
                "name": key,
                "evalJson": pql,
                "expressionType": "Probability",
                "autoSend": False,
                "isResulted": False,
            })
            key_order.append(key)
        else:
            prices[key] = None

    # Try batch endpoint first
    if batch_selections:
        try:
            url = f"{SLOT_BASE_URL}/api/ObpQueryServer/MultiEvaluatePqlPrice?gameId={game_id}"
            data = json.dumps(batch_selections).encode('utf-8')
            req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=60) as resp:
                results = json.loads(resp.read())
                if isinstance(results, list):
                    for i, result in enumerate(results):
                        if i < len(key_order):
                            price = result.get('price') or result.get('probability')
                            if isinstance(price, (int, float)) and price < 9999:
                                prices[key_order[i]] = price
                            else:
                                prices[key_order[i]] = None
                elif isinstance(results, dict) and 'prices' in results:
                    for i, price in enumerate(results['prices']):
                        if i < len(key_order):
                            prices[key_order[i]] = price if price and price < 9999 else None
                else:
                    # Unexpected response format — fall back to individual
                    raise ValueError("Unexpected batch response format")
        except Exception:
            # Fall back to individual requests if batch fails
            for key in key_order:
                sel = selections[key]
                pql = sel.get('evalJson', '')
                try:
                    price = evaluate_pql(game_id, pql)
                    prices[key] = price
                except Exception:
                    prices[key] = None

    return prices, selections


def fetch_springbox_prices(market_type_id):
    """Query SpringBox for production prices."""
    url = f"{SPRINGBOX_BASE_URL}/api/Event/{DEFAULT_GAME_ID}/markets/pregame"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    # Filter by marketType if provided
    results = []
    for market in data if isinstance(data, list) else data.get('markets', data.get('result', [])):
        mt = str(market.get('marketType', market.get('marketTypeId', '')))
        if market_type_id and mt != market_type_id:
            continue
        for sel in market.get('selections', market.get('outcomes', [])):
            results.append({
                "market": market.get('marketName', market.get('name', '')),
                "marketType": mt,
                "team": sel.get('name', sel.get('selectionName', '')),
                "price": sel.get('price', sel.get('decimalPrice', None)),
            })
    return results


def parse_multipart(content_type, body):
    """Parse multipart/form-data to extract file content."""
    boundary = None
    for part in content_type.split(';'):
        part = part.strip()
        if part.startswith('boundary='):
            boundary = part[9:].strip('"')
            break
    if not boundary:
        return None

    boundary_bytes = boundary.encode()
    parts = body.split(b'--' + boundary_bytes)
    for part in parts:
        if b'Content-Disposition' in part and b'filename=' in part:
            # Find the content after the double newline
            header_end = part.find(b'\r\n\r\n')
            if header_end == -1:
                header_end = part.find(b'\n\n')
                if header_end == -1:
                    continue
                content = part[header_end + 2:]
            else:
                content = part[header_end + 4:]
            # Strip trailing boundary markers
            content = content.rstrip(b'\r\n-')
            return content.decode('utf-8')
    return None


def parse_csv_scenarios(csv_text):
    """Parse CSV format: name,market1|team1,market2|team2"""
    scenarios = []
    for line in csv_text.strip().split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        parts = line.split(',')
        if len(parts) < 2:
            continue
        name = parts[0].strip()
        selections = []
        for part in parts[1:]:
            part = part.strip()
            if '|' in part:
                market, team = part.split('|', 1)
                selections.append({"market": market.strip(), "team": team.strip()})
        scenarios.append({"name": name, "selections": selections})
    return scenarios


def format_confluence_table(results):
    """Format results as Confluence storage format HTML table."""
    html = '<table><thead><tr>'
    if results and len(results) > 0:
        headers = results[0].keys()
        for h in headers:
            html += f'<th>{h}</th>'
        html += '</tr></thead><tbody>'
        for row in results:
            html += '<tr>'
            for h in headers:
                val = row.get(h, '')
                html += f'<td>{val}</td>'
            html += '</tr>'
        html += '</tbody></table>'
    else:
        html += '</tr></thead><tbody></tbody></table>'
    return html


# ─── HTTP Handler ───────────────────────────────────────────────────────────

class PricingHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/' or parsed.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            html_path = os.path.join(os.path.dirname(__file__), 'index.html')
            with open(html_path, 'rb') as f:
                self.wfile.write(f.read())

        elif parsed.path == '/api/version':
            result = {"currentVersion": APP_VERSION, "updateAvailable": False}
            try:
                req = urllib.request.Request(VERSION_CHECK_URL)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    latest = resp.read().decode().strip()
                result["latestVersion"] = latest
                result["updateAvailable"] = _version_tuple(latest) > _version_tuple(APP_VERSION)
            except Exception as e:
                # Can't reach GitHub — not fatal, just report current
                result["checkError"] = str(e)
            self.send_json(200, result)

        elif parsed.path == '/api/metadata':
            params = parse_qs(parsed.query)
            game_id = params.get('gameId', [DEFAULT_GAME_ID])[0]
            try:
                meta = fetch_metadata(game_id)
                columns = fetch_columns(game_id)
                self.send_json(200, {"metadata": meta, "columns": columns, "gameId": game_id})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == '/api/selections':
            params = parse_qs(parsed.query)
            game_id = params.get('gameId', [DEFAULT_GAME_ID])[0]
            try:
                selections = get_selections(game_id)
                self.send_json(200, {"selections": selections, "gameId": game_id})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == '/api/staleness':
            params = parse_qs(parsed.query)
            game_id = params.get('gameId', [DEFAULT_GAME_ID])[0]
            try:
                ensure_snapshots_dir()
                now = datetime.now(timezone.utc).isoformat()
                stale = False
                days_since_change = 0

                if os.path.exists(STALENESS_BASELINE_FILE):
                    with open(STALENESS_BASELINE_FILE, 'r') as f:
                        baseline = json.load(f)
                    last_change = baseline.get('lastChange', now)
                    baseline_prices = baseline.get('prices', {})

                    # Quick spot-check: test 3 prices instead of all 420
                    spot_keys = list(baseline_prices.keys())[:3]
                    all_match = True
                    for key in spot_keys:
                        old_price = baseline_prices[key]
                        try:
                            selections = get_selections(game_id)
                            sel = selections.get(key, {})
                            pql = sel.get('evalJson', '')
                            if pql and old_price:
                                current = evaluate_pql(game_id, pql)
                                if abs((current or 0) - old_price) > 0.001:
                                    all_match = False
                                    break
                        except Exception:
                            pass

                    if all_match:
                        stale = True
                        last_dt = datetime.fromisoformat(last_change.replace('Z', '+00:00'))
                        now_dt = datetime.now(timezone.utc)
                        days_since_change = (now_dt - last_dt).days

                self.send_json(200, {
                    "stale": stale,
                    "lastChecked": now,
                    "daysSinceChange": days_since_change,
                })
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == '/api/refresh':
            params = parse_qs(parsed.query)
            game_id = params.get('gameId', [DEFAULT_GAME_ID])[0]
            try:
                ensure_snapshots_dir()
                prices, _ = get_all_prices(game_id)
                now = datetime.now(timezone.utc).isoformat()

                changed_count = 0
                if os.path.exists(STALENESS_BASELINE_FILE):
                    with open(STALENESS_BASELINE_FILE, 'r') as f:
                        baseline = json.load(f)
                    baseline_prices = baseline.get('prices', {})
                    changed_count = sum(1 for k in prices if prices.get(k) != baseline_prices.get(k))

                with open(STALENESS_BASELINE_FILE, 'w') as f:
                    json.dump({"prices": prices, "lastChange": now if changed_count > 0 else baseline.get('lastChange', now) if os.path.exists(STALENESS_BASELINE_FILE) else now}, f, indent=2)

                # Also get fixture info from SpringBox
                fixture_updated = None
                try:
                    url = f"{SPRINGBOX_BASE_URL}/api/Fixtures/{game_id}/info"
                    req = urllib.request.Request(url)
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        fixture_data = json.loads(resp.read())
                        fixture_updated = fixture_data.get('updatedAt')
                except Exception:
                    pass

                self.send_json(200, {
                    "refreshed": True,
                    "timestamp": now,
                    "priceCount": len(prices),
                    "changedCount": changed_count,
                    "fixtureUpdatedAt": fixture_updated,
                    "message": f"Refreshed. {changed_count} prices changed since last check." if changed_count > 0 else "Refreshed. Matrix unchanged — no new simulation has been published."
                })
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == '/api/margin':
            params = parse_qs(parsed.query)
            market_value = params.get('market', [None])[0]
            game_id = params.get('gameId', [DEFAULT_GAME_ID])[0]
            if not market_value:
                self.send_json(400, {"error": "market parameter is required"})
                return
            try:
                selections = get_selections(game_id)
                market_prices = []
                # Map common UI names to exact SLOT market names
                MARKET_NAME_MAP = {
                    "winner": "Outright Winner", "outright winner": "Outright Winner",
                    "top 2": "Top 2 Finish", "top 2 finish": "Top 2 Finish",
                    "top 3": "Top 3 Finish", "top 3 finish": "Top 3 Finish",
                    "top 4": "Top 4 Finish", "top 4 finish": "Top 4 Finish",
                    "top 5": "Top 5 Finish", "top 5 finish": "Top 5 Finish",
                    "top 6": "Top 6 Finish", "top 6 finish": "Top 6 Finish",
                    "top 7": "Top 7 Finish", "top 7 finish": "Top 7 Finish",
                    "top 8": "Top 8 Finish", "top 8 finish": "Top 8 Finish",
                    "top half": "Top Half Finish", "top half finish": "Top Half Finish",
                    "bottom half": "Bottom Half Finish", "bottom half finish": "Bottom Half Finish",
                    "relegated": "To Be Relegated", "to be relegated": "To Be Relegated",
                    "avoid relegation": "Avoid Relegation",
                    "to finish bottom": "To Finish Bottom",
                    "to finish 2nd": "To Finish 2nd",
                    "to finish 3rd": "To Finish 3rd",
                    "to finish 4th": "To Finish 4th",
                    "outside top 4": "To Finish Outside Top 4",
                    "outside top 6": "To Finish Outside Top 6",
                    "top london club": "Top London Club",
                    "top north west club": "Top North West Club",
                    "top north east club": "Top North East Club",
                    "top yorkshire club": "Top Yorkshire Club",
                    "top midlands club": "Top Midlands Club",
                    "top northern club": "Top Northern Club",
                    "top promoted club": "Top Promoted Club",
                }
                target_market = MARKET_NAME_MAP.get(market_value.lower(), market_value)
                
                # Collect all PQLs for this market, then batch evaluate
                market_sels = [(key, sel) for key, sel in selections.items() if sel['market'] == target_market]
                
                # Try batch evaluation
                market_prices = []
                pqls_to_eval = [(sel['team'], sel.get('evalJson', '')) for _, sel in market_sels]
                
                for team, pql in pqls_to_eval:
                    price = None
                    if pql:
                        try:
                            price = evaluate_pql(game_id, pql)
                        except Exception:
                            pass
                    prob = (1.0 / price) if price and price > 0 else 0.0
                    market_prices.append({
                        "team": team,
                        "price": price,
                        "probability": round(prob, 6),
                    })

                total_prob = sum(p['probability'] for p in market_prices)
                margin = round(total_prob - 1.0, 6)
                overround = round(total_prob * 100, 2)

                self.send_json(200, {
                    "market": market_value,
                    "margin": margin,
                    "overround": overround,
                    "prices": market_prices,
                })
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == '/api/snapshot/list':
            try:
                ensure_snapshots_dir()
                files = [f for f in os.listdir(SNAPSHOTS_DIR)
                         if f.endswith('.json') and not f.startswith('_')]
                snapshots = []
                for f in sorted(files, reverse=True):
                    filepath = os.path.join(SNAPSHOTS_DIR, f)
                    stat = os.stat(filepath)
                    snapshots.append({
                        "filename": f,
                        "timestamp": f.replace('.json', ''),
                        "size": stat.st_size,
                    })
                self.send_json(200, {"snapshots": snapshots})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == '/api/springbox/prices':
            params = parse_qs(parsed.query)
            market_type = params.get('marketType', [None])[0]
            try:
                prices = fetch_springbox_prices(market_type)
                self.send_json(200, {"prices": prices, "marketType": market_type})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == '/api/price':
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length))
            game_id = body.get('gameId', DEFAULT_GAME_ID)
            pql = body.get('pql', '')
            selection_ids = body.get('selectionIds', [])

            result = {"pql": pql}

            # OQS price
            try:
                oqs_price = evaluate_pql(game_id, pql) if pql else None
                result["oqsPrice"] = oqs_price
                result["impossible"] = oqs_price is None and pql
            except Exception as e:
                result["oqsError"] = str(e)

            # PES price
            if selection_ids:
                try:
                    pes_price = evaluate_pes(game_id, selection_ids)
                    result["pesPrice"] = pes_price
                except Exception as e:
                    result["pesError"] = str(e)

            # SOCOQS price (round-trip verification — evaluates same expression via SOCOQS engine)
            if pql:
                try:
                    socoqs_prob = evaluate_socoqs(game_id, pql)
                    if socoqs_prob and socoqs_prob > 0:
                        result["socoqsPrice"] = 1.0 / socoqs_prob
                        result["socoqsProbability"] = socoqs_prob
                    else:
                        result["socoqsPrice"] = None
                except Exception as e:
                    result["socoqsError"] = str(e)

            self.send_json(200, result)

        elif parsed.path == '/api/pets-roundtrip':
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length))
            game_id = body.get('gameId', DEFAULT_GAME_ID)
            original_expr = body.get('originalExpression', '')
            translated_expr = body.get('translatedExpression', '')
            expected_odds = body.get('expectedOdds')  # SLOT trueOdds if provided

            result = {}

            # Evaluate original expression via SOCOQS
            if original_expr:
                try:
                    orig_prob = evaluate_socoqs(game_id, original_expr)
                    result["originalProbability"] = orig_prob
                    result["originalPrice"] = (1.0 / orig_prob) if orig_prob and orig_prob > 0 else None
                except Exception as e:
                    result["originalError"] = str(e)

            # Evaluate translated expression via SOCOQS
            if translated_expr:
                try:
                    trans_prob = evaluate_socoqs(game_id, translated_expr)
                    result["translatedProbability"] = trans_prob
                    result["translatedPrice"] = (1.0 / trans_prob) if trans_prob and trans_prob > 0 else None
                except Exception as e:
                    result["translatedError"] = str(e)

            # Compare
            op = result.get("originalProbability")
            tp = result.get("translatedProbability")
            if op is not None and tp is not None:
                diff = abs(op - tp)
                result["probabilityDiff"] = round(diff, 6)
                result["match"] = diff < 0.0001  # within tolerance
                result["matchTolerance"] = 0.0001

            # Compare against expected odds (SLOT trueOdds) if provided
            if expected_odds and op is not None and op > 0:
                orig_price = 1.0 / op
                odds_diff = abs(orig_price - float(expected_odds))
                result["expectedOdds"] = float(expected_odds)
                result["oddsDiff"] = round(odds_diff, 4)
                result["oddsMatch"] = odds_diff < 0.01

            self.send_json(200, result)

        elif parsed.path == '/api/stats':
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length))
            game_id = body.get('gameId', DEFAULT_GAME_ID)
            column = body.get('column', '')  # e.g. "Rank_Group_Team_1"

            if not column:
                self.send_json(400, {"error": "column is required"})
                return

            try:
                payload = {
                    "minimum_clock": 0,
                    "expressions": {
                        "stats": {
                            "expression": f"@{column}",
                            "statistics": {
                                "mean": "mean",
                                "min": "min",
                                "max": "max",
                                "median": "median",
                                "p10": {"quantile": {"quantile": 0.1, "direction": "left", "exclude_equal": False}},
                                "p25": {"quantile": {"quantile": 0.25, "direction": "left", "exclude_equal": False}},
                                "p75": {"quantile": {"quantile": 0.75, "direction": "left", "exclude_equal": False}},
                                "p90": {"quantile": {"quantile": 0.9, "direction": "left", "exclude_equal": False}},
                            }
                        }
                    }
                }
                url = f"{SOCOQS_BASE_URL}/{game_id}/evaluate_stats_json"
                data = json.dumps(payload).encode('utf-8')
                req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read())
                stats = result.get('results', {}).get('stats', {})
                out = {}
                for k, v in stats.items():
                    if isinstance(v, dict) and v.get('ok') is not None:
                        out[k] = v['ok']
                self.send_json(200, {"column": column, "stats": out})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == '/api/evaluate-raw':
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length))
            game_id = body.get('gameId', DEFAULT_GAME_ID)
            expr = body.get('expression', '')

            result = {"expression": expr}
            # SOCOQS
            try:
                prob = evaluate_socoqs(game_id, expr)
                result["socoqsProbability"] = prob
                result["socoqsPrice"] = (1.0 / prob) if prob and prob > 0 else None
            except Exception as e:
                result["socoqsError"] = str(e)
            # OQS (via SLOT)
            try:
                oqs_price = evaluate_pql(game_id, expr)
                result["oqsPrice"] = oqs_price
            except Exception as e:
                result["oqsError"] = str(e)

            self.send_json(200, result)

        elif parsed.path == '/api/batch':
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length))
            game_id = body.get('gameId', DEFAULT_GAME_ID)
            scenarios = body.get('scenarios', [])

            results = []
            for scenario in scenarios:
                pql = scenario.get('pql', '')
                try:
                    price = evaluate_pql(game_id, pql)
                    results.append({
                        "name": scenario.get('name', ''),
                        "pql": pql,
                        "price": price,
                        "impossible": price is None
                    })
                except Exception as e:
                    results.append({
                        "name": scenario.get('name', ''),
                        "pql": pql,
                        "price": None,
                        "impossible": False,
                        "error": str(e)
                    })

            self.send_json(200, {"results": results})

        elif parsed.path == '/api/batch-run':
            content_length = int(self.headers.get('Content-Length', 0))
            raw_body = self.rfile.read(content_length)
            content_type = self.headers.get('Content-Type', '')
            params = parse_qs(urlparse(self.path).query)
            game_id = params.get('gameId', [DEFAULT_GAME_ID])[0]

            scenarios = []
            if 'multipart/form-data' in content_type:
                csv_text = parse_multipart(content_type, raw_body)
                if csv_text:
                    scenarios = parse_csv_scenarios(csv_text)
                    # Check for gameId in form fields
                    # Simple extraction — look for a gameId field in the multipart
                else:
                    self.send_json(400, {"error": "Could not parse multipart file upload"})
                    return
            else:
                body = json.loads(raw_body)
                game_id = body.get('gameId', game_id)
                scenarios = body.get('scenarios', [])

            # Get all selections to look up PQL by market|team
            try:
                all_selections = get_selections(game_id)
            except Exception as e:
                self.send_json(500, {"error": f"Failed to fetch selections: {e}"})
                return

            results = []
            for scenario in scenarios:
                name = scenario.get('name', '')
                sels = scenario.get('selections', [])
                scenario_result = {"name": name, "legs": []}

                # Build combined PQL and selection IDs
                selection_ids = []
                for sel in sels:
                    key = f"{sel['market']}|{sel['team']}"
                    match = all_selections.get(key)
                    if match:
                        pql = match.get('evalJson', '')
                        sel_id = match.get('selectionRampId', '')

                        # Get individual OQS price
                        leg_result = {
                            "market": sel['market'],
                            "team": sel['team'],
                            "selectionId": sel_id,
                        }
                        try:
                            price = evaluate_pql(game_id, pql) if pql else None
                            leg_result["oqsPrice"] = price
                            leg_result["impossible"] = price is None and bool(pql)
                        except Exception as e:
                            leg_result["oqsPrice"] = None
                            leg_result["oqsError"] = str(e)
                            leg_result["impossible"] = False

                        if sel_id:
                            selection_ids.append(sel_id)
                        scenario_result["legs"].append(leg_result)
                    else:
                        scenario_result["legs"].append({
                            "market": sel['market'],
                            "team": sel['team'],
                            "error": "Selection not found",
                        })

                # Get combined PES price for all legs
                if selection_ids:
                    try:
                        pes_price = evaluate_pes(game_id, selection_ids)
                        scenario_result["pesPrice"] = pes_price
                    except Exception as e:
                        scenario_result["pesError"] = str(e)

                # Get combined OQS price by combining all leg PQLs
                all_pqls = []
                for sel in sels:
                    key = f"{sel['market']}|{sel['team']}"
                    match = all_selections.get(key)
                    if match and match.get('evalJson'):
                        pql = match['evalJson']
                        all_pqls.append(f"({pql})" if "&&" in pql else pql)

                if all_pqls:
                    combined_pql = " && ".join(all_pqls)
                    try:
                        combined_oqs = evaluate_pql(game_id, combined_pql)
                        scenario_result["oqsPrice"] = combined_oqs
                        scenario_result["impossible"] = combined_oqs is None
                        scenario_result["pql"] = combined_pql
                    except Exception as e:
                        scenario_result["oqsError"] = str(e)

                    # SOCOQS round-trip price
                    try:
                        socoqs_prob = evaluate_socoqs(game_id, combined_pql)
                        if socoqs_prob and socoqs_prob > 0:
                            scenario_result["socoqsPrice"] = 1.0 / socoqs_prob
                        else:
                            scenario_result["socoqsPrice"] = None
                    except Exception as e:
                        scenario_result["socoqsError"] = str(e)

                results.append(scenario_result)

            self.send_json(200, {"results": results, "gameId": game_id})

        elif parsed.path == '/api/snapshot/save':
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length)) if content_length > 0 else {}
            game_id = body.get('gameId', DEFAULT_GAME_ID)
            try:
                ensure_snapshots_dir()
                prices, selections = get_all_prices(game_id)
                timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
                filename = f"{timestamp}.json"
                filepath = os.path.join(SNAPSHOTS_DIR, filename)

                snapshot_data = {
                    "timestamp": timestamp,
                    "gameId": game_id,
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "prices": prices,
                    "selectionCount": len(prices),
                }
                with open(filepath, 'w') as f:
                    json.dump(snapshot_data, f, indent=2)

                self.send_json(200, {
                    "filename": filename,
                    "timestamp": timestamp,
                    "selectionCount": len(prices),
                })
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == '/api/snapshot/compare':
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length))
            game_id = body.get('gameId', DEFAULT_GAME_ID)
            snapshot_file = body.get('snapshot', '')
            if not snapshot_file:
                self.send_json(400, {"error": "snapshot filename is required"})
                return
            try:
                ensure_snapshots_dir()
                filepath = os.path.join(SNAPSHOTS_DIR, snapshot_file)
                if not os.path.exists(filepath):
                    self.send_json(404, {"error": f"Snapshot {snapshot_file} not found"})
                    return

                with open(filepath, 'r') as f:
                    snapshot_data = json.load(f)

                snapshot_prices = snapshot_data.get('prices', {})
                current_prices, _ = get_all_prices(game_id)

                diffs = []
                all_keys = set(list(snapshot_prices.keys()) + list(current_prices.keys()))
                for key in sorted(all_keys):
                    old_price = snapshot_prices.get(key)
                    new_price = current_prices.get(key)
                    if old_price != new_price:
                        diff = {
                            "selection": key,
                            "snapshotPrice": old_price,
                            "currentPrice": new_price,
                        }
                        if old_price and new_price:
                            diff["change"] = round(new_price - old_price, 4)
                            diff["changePct"] = round(((new_price - old_price) / old_price) * 100, 2)
                        diffs.append(diff)

                self.send_json(200, {
                    "snapshot": snapshot_file,
                    "snapshotTimestamp": snapshot_data.get('timestamp', ''),
                    "totalSelections": len(all_keys),
                    "changedCount": len(diffs),
                    "diffs": diffs,
                })
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == '/api/autoscan':
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length))
            game_id = body.get('gameId', DEFAULT_GAME_ID)
            team = body.get('team', '')
            num_legs = int(body.get('legs', 2))
            num_legs = max(2, min(4, num_legs))  # clamp 2-4
            if not team:
                self.send_json(400, {"error": "team parameter is required"})
                return
            try:
                import itertools
                all_selections = get_selections(game_id)
                team_markets = [sel for key, sel in all_selections.items()
                                if sel['team'] == team and sel.get('evalJson')]

                if not team_markets:
                    self.send_json(404, {"error": f"Team '{team}' not found in any market"})
                    return

                # Build all N-leg combos as PQL expressions
                combos = []
                expressions = {}
                idx = 0
                for combo_indices in itertools.combinations(range(len(team_markets)), num_legs):
                    legs = [team_markets[i] for i in combo_indices]
                    name = f"c{idx}"
                    idx += 1
                    combined = " && ".join(f"({leg['evalJson']})" for leg in legs)
                    expressions[name] = f"probability({combined})"
                    combos.append({
                        "key": name,
                        "combo": " + ".join(leg['market'] for leg in legs),
                    })

                # Batch call to SOCOQS evaluate_multi (chunked if very large)
                results = []
                combo_keys = list(expressions.keys())
                CHUNK = 2000
                batch_results = {}

                for start in range(0, len(combo_keys), CHUNK):
                    chunk_keys = combo_keys[start:start+CHUNK]
                    chunk_exprs = {k: expressions[k] for k in chunk_keys}
                    try:
                        url = f"{SOCOQS_BASE_URL}/{game_id}/evaluate_multi"
                        payload = {"minimum_clock": 0, "expressions": chunk_exprs}
                        data = json.dumps(payload).encode('utf-8')
                        req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
                        with urllib.request.urlopen(req, timeout=120) as resp:
                            batch = json.loads(resp.read())
                        batch_results.update(batch.get('results', {}))
                    except Exception:
                        pass

                for c in combos:
                    r = batch_results.get(c['key'], {})
                    prob = None
                    if isinstance(r, dict) and r.get('ok') is not None:
                        try:
                            prob = float(r['ok'])
                        except (ValueError, TypeError):
                            prob = None
                    price = (1.0 / prob) if prob and prob > 0 else None
                    results.append({
                        "combo": c['combo'],
                        "price": price,
                        "impossible": price is None,
                    })

                self.send_json(200, {
                    "team": team,
                    "legs": num_legs,
                    "totalCombos": len(results),
                    "impossibleCount": sum(1 for r in results if r.get('impossible')),
                    "results": results,
                })
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        elif parsed.path == '/api/confluence/export':
            content_length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(content_length))
            results = body.get('results', [])
            if not results:
                self.send_json(400, {"error": "results array is required"})
                return
            try:
                html = format_confluence_table(results)
                self.send_json(200, {"html": html})
            except Exception as e:
                self.send_json(500, {"error": str(e)})

        else:
            self.send_response(404)
            self.end_headers()

    def send_json(self, status, data):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def log_message(self, format, *args):
        if '/api/' in str(args[0]):
            sys.stderr.write(f"  API: {args[0]}\n")


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="EPL Outright Pricing Tool — Web UI")
    parser.add_argument("--port", "-p", type=int, default=PORT, help=f"Port (default: {PORT})")
    args = parser.parse_args()

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    server = http.server.HTTPServer(('0.0.0.0', args.port), PricingHandler)
    print(f"\n🏟️  EPL Outright Pricing Tool")
    print(f"   Running at: http://localhost:{args.port}")
    print(f"   SLOT endpoint: {SLOT_BASE_URL}")
    print(f"   Default game: {DEFAULT_GAME_ID}")
    print(f"\n   Press Ctrl+C to stop\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n   Shutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
