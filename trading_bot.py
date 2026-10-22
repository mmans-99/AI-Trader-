import os, json, requests, datetime, re

# --- 1. Load Configuration (with # support) ---
persona_choice = "Aggressive Growth"
try:
    with open("config.json", "r") as f:
        lines = f.readlines()
    clean_lines = []
    for line in lines:
        if "#" in line: line = line.split("#")[0]
        if line.strip(): clean_lines.append(line)
    config = json.loads("".join(clean_lines))
    persona_choice = config.get("persona", "Aggressive Growth")
except Exception:
    pass

PERSONAS = {
    "Aggressive Growth": "an Aggressive Growth trader who loves high volatility and tech stocks.",
    "Conservative Value": "a Conservative Value investor who looks for safe, steady dividends and avoids risk.",
    "Momentum Trader": "a Momentum Day Trader who only buys things going up right now and quickly cuts losses.",
    "Contrarian": "a Contrarian who buys when others are fearful and sells when the market is greedy."
}
persona = PERSONAS.get(persona_choice, PERSONAS["Aggressive Growth"])

ALL_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD", "NFLX", "CRM",
    "ADBE", "INTC", "QCOM", "AVGO", "COST", "PEP", "KO", "WMT", "JPM", "BAC",
    "V", "MA", "DIS", "NKE", "PYPL", "UBER", "SNOW", "PLTR", "COIN", "SHOP"
]
ALPACA_KEY = os.environ["ALPACA_KEY"]
ALPACA_SECRET = os.environ["ALPACA_SECRET"]
GEMINI_KEY = os.environ["GEMINI_KEY"]
headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

# --- Fetch Market Data ---
print("Fetching market data...")
prices = {}
for sym in ALL_SYMBOLS:
    try:
        r = requests.get(f"https://data.alpaca.markets/v2/stocks/{sym}/snapshot?feed=iex",
                         headers=headers, timeout=10)
        prices[sym] = r.json().get("latestTrade", {}).get("p", 0)
    except:
        pass

# --- Get Account & Live Positions ---
acct_raw = requests.get("https://paper-api.alpaca.markets/v2/account", headers=headers).json()
positions_raw = requests.get("https://paper-api.alpaca.markets/v2/positions", headers=headers).json()

portfolio_summary = {
    "cash": round(float(acct_raw.get("cash", 0)), 2),
    "portfolio_value": round(float(acct_raw.get("portfolio_value", 0)), 2),
    "total_profit_loss": round(float(acct_raw.get("portfolio_value", 100000)) - 100000, 2)
}

current_positions = []
held_symbols = []
if isinstance(positions_raw, list):
    for p in positions_raw:
        held_symbols.append(p["symbol"])
        current_positions.append({
            "symbol": p["symbol"],
            "qty": p["qty"],
            "avg_price": round(float(p["avg_entry_price"]), 2),
            "current_price": round(float(p["current_price"]), 2),
            "market_value": round(float(p["market_value"]), 2),
            "unrealized_pl": round(float(p["unrealized_pl"]), 2)
        })

# --- Extract Recent Memory ---
recent_history = []
if os.path.exists("log.json"):
    try:
        with open("log.json", "r") as f:
            past_logs = json.load(f)
            for past in past_logs[:3]:
                if "decision" in past and past["decision"]["action"] != "hold":
                    recent_history.append({
                        "action": past["decision"]["action"],
                        "symbol": past["decision"].get("symbol"),
                        "reason": past["decision"].get("reason")
                    })
    except Exception:
        pass

# --- OPTIMIZATION: Filter prices to keep the prompt small ---
# Only send the AI the stocks it owns + the 5 biggest winners + 5 biggest losers
valid_prices = {k: v for k, v in prices.items() if v > 0}
sorted_by_price = sorted(valid_prices.items(), key=lambda x: x[1])
losers = sorted_by_price[:5]
winners = sorted_by_price[-5:]

prompt_prices = {}
for sym in held_symbols:
    if sym in prices: prompt_prices[sym] = prices[sym]
for sym, p in losers + winners:
    prompt_prices[sym] = p

# --- Find Best FREE Model ---
selected_model = "gemini-3.8-flash"
try:
    list_res = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}")
    models_data = list_res.json()
    if 'error' not in models_data:
        candidates = []
        for m in models_data.get("models", []):
            model_id = m["name"].replace("models/", "")
            if "generateContent" in m.get("supportedGenerationMethods", []) and "image" not in model_id and "embedding" not in model_id:
                if "preview" not in model_id and "argon" not in model_id.lower():
                    candidates.append(model_id)
        if candidates:
            candidates.sort(key=lambda m: [int(p) for p in re.findall(r'\d+', m)], reverse=True)
            selected_model = candidates[0]
except Exception:
    pass

# --- Ask the AI ---
prompt = (
    f"You are {persona} "
    f"Portfolio: {json.dumps(portfolio_summary)}. "
    f"Holdings: {json.dumps(current_positions)}. "
    f"Recent Trades: {json.dumps(recent_history)}. "
    f"Market Snapshot (Your holdings + Top Winners/Losers): {json.dumps(prompt_prices)}. "
    "Reflect on your past trades. Decide to buy, sell, or hold ONE stock. If buying/selling, choose an amount up to $2000. "
    "Respond ONLY with a valid JSON object: {\"action\": \"buy\"|\"sell\"|\"hold\", \"symbol\": \"...\", \"amount\": 500, \"reason\": \"...\"}"
)

fallback_models = [selected_model, "gemini-3.8-flash", "gemini-2.5-flash", "gemini-1.5-flash"]
ai_response = None

for model_to_try in fallback_models:
    try:
        res = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_try}:generateContent?key={GEMINI_KEY}",
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"response_mime_type": "application/json"} 
            },
            timeout=30 # Can be lower now because the prompt is much smaller
        )
        data = res.json()
        if 'error' not in data:
            ai_response = data
            selected_model = model_to_try
            break
    except Exception:
        continue

# --- Parse and Execute ---
try:
    if not ai_response: raise Exception("Timeout")
    raw_text = ai_response['candidates'][0]['content']['parts'][0]['text']
    start, end = raw_text.find('{'), raw_text.rfind('}')
    decision = json.loads(raw_text[start:end+1])
except Exception as e:
    decision = {"action": "hold", "reason": f"Parse error: {e}"}

if decision.get("action") in ["buy", "sell"]:
    try:
        requests.post("https://paper-api.alpaca.markets/v2/orders", headers=headers,
                      json={"symbol": decision["symbol"], "notional": str(decision.get("amount", 100)),
                            "side": decision["action"], "type": "market", "time_in_force": "day"})
    except Exception as e:
        decision["reason"] += f" (Exec error: {e})"

# --- Save to Log ---
log_entry = {
    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    "model": selected_model, "persona": persona_choice,
    "account": portfolio_summary, "positions": current_positions, "decision": decision
}

existing_log = []
if os.path.exists("log.json"):
    with open("log.json", "r") as f: existing_log = json.load(f)

existing_log.insert(0, log_entry)
with open("log.json", "w") as f:
    json.dump(existing_log[:100], f, indent=2)