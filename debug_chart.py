
import logging
from main import get_daily_candles
from chart_generator import generate_straddle_chart
import os

logging.basicConfig(level=logging.INFO)

print("Fetching candles...")
candles = get_daily_candles()
print(f"Candles fetched: {len(candles)}")

if candles:
    print("Generating chart...")
    path = generate_straddle_chart(candles, 69000, 70000, 68000, "debug_chart.png")
    print(f"Chart path: {path}")
    
    if path and os.path.exists(path):
        print("✅ Chart generated successfully")
    else:
        print("❌ Chart generation returned path but file missing or None returned")
else:
    print("❌ No candles fetched")
