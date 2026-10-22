import os, json, requests, datetime, re

# --- 1. Load Configuration (with # support) ---
persona_choice = "Aggressive Growth" # Default
try:
    with open("config.json", "r") as f:
        lines = f.readlines()
        
    clean_lines = []
    for line in lines:
        # Remove everything after a hashtag (#)
        if "#" in line:
            line = line.split("#")[0]
        # Keep the line if it's not completely empty
        if line.strip():
            clean_lines.append(line)
            
    config = json.loads("".join(clean_lines))
    persona_choice = config.get("persona", "Aggressive Growth")
except Exception as e:
    print(f"Could not read config.json, using default. Error: {e}")

PERSONAS = {
    "Aggressive Growth": "an Aggressive Growth trader who loves high volatility and tech stocks.",
    "Conservative Value": "a Conservative Value investor who looks for safe, steady dividends and avoids risk.",
    "Momentum Trader": "a Momentum Day Trader who only buys things going up right now and quickly cuts losses.",
    "Contrarian": "a Contrarian who buys when others are fearful and sells when the market is greedy."
}
persona = PERSONAS.get(persona_choice, PERSONAS["Aggressive Growth"])

# --- 2. Configuration ---
ALL_SYMBOLS = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD", "NFLX", "CRM",
    "ADBE", "INTC", "QCOM", "AVGO", "COST", "PEP", "KO", "WMT", "JPM", "BAC",
    "V", "MA", "DIS", "NKE", "PYPL", "UBER", "SNOW", "PLTR", "COIN", "SHOP"
]
ALPACA_KEY = os.environ["ALPACA_KEY"]
ALPACA_SECRET = os.environ["ALPACA_SECRET"]
GEMINI_KEY = os.environ["GEMINI_KEY"]
headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

# --- 3. Fetch Market Data for ALL stocks (so it can pick anything) ---
prices = {}
for sym in ALL_SYMBOLS:
    try:
        r = requests.get(f"https://data.alpaca.markets/v2/stocks/{sym}/snapshot?feed=iex",
                         headers=headers, timeout=10)
        prices[sym] = r.json().get("latestTrade", {}).get("p", 0)
    except:
        pass

# --- 4. Get Account & Live Positions ---
acct_raw = requests.get("https://paper-api.alpaca.markets/v2/account", headers=headers).json()
positions_raw = requests.get("https://paper-api.alpaca.markets/v2/positions", headers=headers).json()

portfolio_summary = {
    "cash": round(float(acct_raw.get("cash", 0)), 2),
    "portfolio_value": round(float(acct_raw.get("portfolio_value", 0)), 2),
    "total_profit_loss": round(float(acct_raw.get("portfolio_value", 100000)) - 100000, 2)
}

current_positions = []
if isinstance(positions_raw, list):
    for p in positions_raw:
        current_positions.append({
            "symbol": p["symbol"],
            "qty": p["qty"],
            "avg_price": round(float(p["avg_entry_price"]), 2),
            "current_price": round(float(p["current_price"]), 2),
            "market_value": round(float(p["market_value"]), 2),
            "unrealized_pl": round(float(p["unrealized_pl"]), 2)
        })

# --- 5. Find Best FREE Model ---
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

# --- 6. Ask the AI ---
prompt = (
    f"You are {persona} "
    f"Your Portfolio: {json.dumps(portfolio_summary)}. "
    f"Your Current Holdings: {json.dumps(current_positions)}. "
    f"Current Prices for major stocks: {json.dumps(prices)}. "
    "Decide to buy, sell, or hold ONE stock. If buying/selling, choose a dollar amount up to $2000. "
    "Respond ONLY with a valid JSON object matching this schema: {\"action\": \"buy\"|\"sell\"|\"hold\", \"symbol\": \"...\", \"amount\": 500, \"reason\": \"...\"}"
)

fallback_models = [selected_model, "gemini-3.8-flash", "gemini-2.5-flash", "gemini-1.5-flash"]
ai_response = None

for model_to_try in fallback_models:
    res = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_try}:generateContent?key={GEMINI_KEY}",
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"response_mime_type": "application/json"} 
        },
        timeout=15
    )
    data = res.json()
    if 'error' not in data:
        ai_response = data
        selected_model = model_to_try
        break

# --- 7. Parse and Execute ---
try:
    if not ai_response:
        raise Exception("All models failed.")
    raw_text = ai_response['candidates'][0]['content']['parts'][0]['text']
    start = raw_text.find('{')
    end = raw_text.rfind('}')
    clean_json = raw_text[start:end+1]
    decision = json.loads(clean_json)
except Exception as e:
    decision = {"action": "hold", "reason": f"Parse error: {e}"}

if decision.get("action") in ["buy", "sell"]:
    try:
        requests.post("https://paper-api.alpaca.markets/v2/orders",
                      headers=headers,
                      json={
                          "symbol": decision["symbol"],
                          "notional": str(decision.get("amount", 100)),
                          "side": decision["action"],
                          "type": "market",
                          "time_in_force": "day"
                      })
    except Exception as e:
        decision["reason"] += f" (Exec error: {e})"

# --- 8. Save to Log ---
log_entry = {
    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    "model": selected_model,
    "persona": persona_choice,
    "account": portfolio_summary,
    "positions": current_positions, # Saved for the dashboard!
    "decision": decision
}

existing_log = []
if os.path.exists("log.json"):
    with open("log.json", "r") as f:
        existing_log = json.load(f)

existing_log.insert(0, log_entry)
with open("log.json", "w") as f:
    json.dump(existing_log[:50], f, indent=2) # Keep last 50 runs for the graph