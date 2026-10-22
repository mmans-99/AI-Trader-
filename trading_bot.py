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

# --- 3. Ask the AI for a Decision ---
prompt = (
    f"You are an autonomous trader. Portfolio Summary: {json.dumps(portfolio_summary)}. "
    f"Current Prices: {json.dumps(prices)}. "
    "Decide to buy, sell, or hold ONE stock. If buying/selling, choose a dollar amount up to $1000. "
    "Respond ONLY with a JSON object: {\"action\": \"buy\"|\"sell\"|\"hold\", \"symbol\": \"...\", \"amount\": 500, \"reason\": \"...\"}"
)

# --- NEW: Dynamically find the best Gemini model ---
print("Fetching available Gemini models...")
list_url = f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}"
list_res = requests.get(list_url)

try:
    models_data = list_res.json()
    candidates = []
    
    for m in models_data.get("models", []):
        model_id = m["name"].replace("models/", "")
        methods = m.get("supportedGenerationMethods", [])
        
        # We only want text chat models that support generateContent
        if "generateContent" in methods:
            # Filter out image/TTS/embedding models, keep flash, pro, and core models
            if any(x in model_id for x in ["flash", "pro", "gemini"]) and not any(x in model_id for x in ["image", "tts", "embedding", "vision"]):
                if "preview" not in model_id: # Prefer stable versions over experimental ones
                    candidates.append(model_id)
    
    # Sort by version number (e.g., gemini-4 > gemini-3.8 > gemini-3.6)
    def version_key(m):
        parts = re.findall(r'\d+', m)
        return [int(p) for p in parts]
    
    candidates.sort(key=version_key, reverse=True)
    
    if candidates:
        selected_model = candidates[0]
        print(f"Selected best available model: {selected_model}")
    else:
        # Fallback if filtering was too strict
        selected_model = "gemini-3.8-flash" 
        print(f"Could not auto-select, using fallback: {selected_model}")

except Exception as e:
    print(f"Failed to fetch models: {e}")
    selected_model = "gemini-3.8-flash" # Hard fallback so the bot doesn't crash

# --- 4. Call the AI with the discovered model ---
print(f"Asking {selected_model} for a decision...")
ai_res = requests.post(
    f"https://generativelanguage.googleapis.com/v1beta/models/{selected_model}:generateContent?key={GEMINI_KEY}",
    json={"contents": [{"parts": [{"text": prompt}]}]}
)
ai_response = ai_res.json()

# --- 5. Parse the AI Response ---
try:
    if 'error' in ai_response:
        raise Exception(ai_response['error'].get('message', 'Unknown API Error'))
        
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
    "model": selected_model,  # Added so you can see which model the bot chose
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