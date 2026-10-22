import os, json, requests, datetime

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
acct = requests.get("https://paper-api.alpaca.markets/v2/account",
                    headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}).json()

# --- 3. Ask the AI for a Decision ---
prompt = (
    f"You are an autonomous trader. Portfolio: {json.dumps(acct)}. "
    f"Current Prices: {json.dumps(prices)}. "
    "Decide to buy, sell, or hold ONE stock. If buying/selling, choose a dollar amount up to $1000. "
    "Respond ONLY with a JSON object: {\"action\": \"buy\"|\"sell\"|\"hold\", \"symbol\": \"...\", \"amount\": 500, \"reason\": \"...\"}"
)
ai_response = requests.post(
    f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}",
    json={"contents": [{"parts": [{"text": prompt}]}]}
).json()

# --- 4. Parse the AI Response ---
try:
    raw_text = ai_response['candidates'][0]['content']['parts'][0]['text']
    clean_json = raw_text.replace("```json", "").replace("```", "").strip()
    decision = json.loads(clean_json)
except Exception as e:
    decision = {"action": "hold", "reason": f"AI response parsing error: {e}"}

# --- 5. Execute the Trade (if any) ---
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

# --- 6. Save the Decision to a Local Log File ---
log_entry = {
    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
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