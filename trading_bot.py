import os, json, requests, datetime, re

# --- Configuration ---
SYMBOLS = ["AAPL", "NVDA", "TSLA"]
ALPACA_KEY = os.environ["ALPACA_KEY"]
ALPACA_SECRET = os.environ["ALPACA_SECRET"]
GEMINI_KEY = os.environ["GEMINI_KEY"]

# --- 1. Fetch Market Data ---
prices = {}
for sym in SYMBOLS:
    r = requests.get(f"https://data.alpaca.markets/v2/stocks/{sym}/snapshot?feed=iex",
                     headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET})
    prices[sym] = r.json().get("latestTrade", {}).get("p", 0)

# --- 2. Get Account Info ---
acct_raw = requests.get("https://paper-api.alpaca.markets/v2/account",
                        headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}).json()

portfolio_summary = {
    "cash": acct_raw.get("cash"),
    "portfolio_value": acct_raw.get("portfolio_value"),
    "buying_power": acct_raw.get("buying_power")
}

# --- 3. Dynamically Find Best FREE Model ---
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
except Exception as e:
    pass

# --- 4. Ask the AI for a Decision ---
prompt = (
    f"You are an autonomous trader. Portfolio Summary: {json.dumps(portfolio_summary)}. "
    f"Current Prices: {json.dumps(prices)}. "
    "Decide to buy, sell, or hold ONE stock. If buying/selling, choose a dollar amount up to $1000. "
    "Respond ONLY with a valid JSON object matching this schema: {\"action\": \"buy\"|\"sell\"|\"hold\", \"symbol\": \"...\", \"amount\": 500, \"reason\": \"...\"}"
)

fallback_models = [selected_model, "gemini-3.8-flash", "gemini-2.5-flash", "gemini-1.5-flash"]
ai_response = None

for model_to_try in fallback_models:
    res = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_try}:generateContent?key={GEMINI_KEY}",
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            # THIS FORCES GOOGLE TO RETURN STRICT JSON
            "generationConfig": {"response_mime_type": "application/json"} 
        },
        timeout=15
    )
    data = res.json()
    
    if 'error' not in data:
        ai_response = data
        selected_model = model_to_try
        break

# --- 5. Parse the AI Response ---
try:
    if not ai_response:
        raise Exception("All models failed.")
        
    raw_text = ai_response['candidates'][0]['content']['parts'][0]['text']
    
    # Safely extract the JSON block (finds the first '{' and last '}')
    start = raw_text.find('{')
    end = raw_text.rfind('}')
    if start != -1 and end != -1:
        clean_json = raw_text[start:end+1]
        decision = json.loads(clean_json)
    else:
        raise Exception(f"No JSON found. AI said: {raw_text[:50]}")
        
except Exception as e:
    decision = {"action": "hold", "reason": f"Parse error: {e}"}

# --- 6. Execute the Trade (if any) ---
if decision.get("action") in ["buy", "sell"]:
    try:
        requests.post("https://paper-api.alpaca.markets/v2/orders",
                      headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
                      json={
                          "symbol": decision["symbol"],
                          "notional": str(decision.get("amount", 100)),
                          "side": decision["action"],
                          "type": "market",
                          "time_in_force": "day"
                      })
    except Exception as e:
        decision["reason"] += f" (Exec error: {e})"

# --- 7. Save the Decision to a Local Log File ---
log_entry = {
    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    "model": selected_model,
    "decision": decision,
    "prices": prices
}

existing_log = []
if os.path.exists("log.json"):
    with open("log.json", "r") as f:
        existing_log = json.load(f)

existing_log.insert(0, log_entry)
with open("log.json", "w") as f:
    json.dump(existing_log[:50], f, indent=2)

print("Bot run complete. Decision:", decision)