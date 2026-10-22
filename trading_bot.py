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
selected_model = "gemini-3.8-flash"  # Safe fallback
try:
    list_res = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}")
    models_data = list_res.json()
    
    if 'error' not in models_data:
        candidates = []
        for m in models_data.get("models", []):
            model_id = m["name"].replace("models/", "")
            methods = m.get("supportedGenerationMethods", [])
            
            # Look for chat models, exclude image/embedding, and EXCLUDE premium models like Argon
            if "generateContent" in methods and "image" not in model_id and "embedding" not in model_id:
                if "preview" not in model_id and "argon" not in model_id.lower():
                    candidates.append(model_id)
        
        # Sort by version number
        if candidates:
            candidates.sort(key=lambda m: [int(p) for p in re.findall(r'\d+', m)], reverse=True)
            selected_model = candidates[0]
            
    print(f"Using model: {selected_model}")
except Exception as e:
    print(f"Model discovery failed, using fallback: {e}")

# --- 4. Ask the AI for a Decision ---
prompt = (
    f"You are an autonomous trader. Portfolio Summary: {json.dumps(portfolio_summary)}. "
    f"Current Prices: {json.dumps(prices)}. "
    "Decide to buy, sell, or hold ONE stock. If buying/selling, choose a dollar amount up to $1000. "
    "Respond ONLY with a JSON object: {\"action\": \"buy\"|\"sell\"|\"hold\", \"symbol\": \"...\", \"amount\": 500, \"reason\": \"...\"}"
)

# --- NEW: Try the chosen model, fall back to known free models if it fails ---
fallback_models = [selected_model, "gemini-3.8-flash", "gemini-2.5-flash", "gemini-1.5-flash"]
ai_response = None

for model_to_try in fallback_models:
    print(f"Attempting with model: {model_to_try}...")
    res = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_try}:generateContent?key={GEMINI_KEY}",
        json={"contents": [{"parts": [{"text": prompt}]}]}
    )
    data = res.json()
    
    if 'error' not in data:
        ai_response = data
        selected_model = model_to_try  # Update to the model that actually worked
        print(f"Success with {selected_model}!")
        break
    else:
        print(f"Failed with {model_to_try}: {data['error'].get('message')}")

# --- 5. Parse the AI Response ---
try:
    if not ai_response:
        raise Exception("All fallback models failed due to quota errors.")
        
    raw_text = ai_response['candidates'][0]['content']['parts'][0]['text']
    clean_json = raw_text.replace("```json", "").replace("```", "").strip()
    decision = json.loads(clean_json)
except Exception as e:
    decision = {"action": "hold", "reason": f"AI error: {e}"}

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
        decision["reason"] += f" (Execution error: {e})"

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