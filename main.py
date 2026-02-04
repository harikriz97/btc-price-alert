import logging
import requests
import math
import os
import pytz
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')  # Still needed for auto-alerts
CHECK_INTERVAL = 360  # Check every 60 seconds

# Risk Calculator Config (INR 1000 Risk)
RISK_PER_DAY_USD = 11.76  # approx 1000 INR
CONSTANT_FACTOR = RISK_PER_DAY_USD / 0.003  # ~3920

# Global State for Strategy
base_price = None
session_high = None
session_low = None
high_alert_sent = False
low_alert_sent = False
daily_start_alert_sent = False
daily_close_alert_sent = False

# Setup Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- PART 1: TRADING CALCULATOR (c24 p42) ---

def calculate_leg_math(premium):
    try:
        p = float(premium)
        if p <= 0: return 0, 0
        sl_price = p * 5  # SL is 400% of premium (Entry + 4x)
        lots = math.floor(CONSTANT_FACTOR / p) 
        return int(sl_price), int(lots)
    except:
        return 0, 0

async def handle_calculator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Listens for 'c24 p42' and replies with Lot Size"""
    text = update.message.text.lower().strip()
    
    # Try to parse "c24 p42" or "c 24 p 42"
    if 'c' in text and 'p' in text:
        try:
            parts = text.replace("c", "").replace("p", " ").split()
            if len(parts) >= 2:
                call_prem, put_prem = parts[0], parts[1]
                
                c_sl, c_lots = calculate_leg_math(call_prem)
                p_sl, p_lots = calculate_leg_math(put_prem)

                response = (
                    f"📊 **Risk Calculator (1k INR)**\n"
                    f"-----------------------------\n"
                    f"📈 **CALL ($ {call_prem})**\n"
                    f"• **SL Price:** ${c_sl}\n"
                    f"• **Qty:** {c_lots} Contracts\n"
                    f"-----------------------------\n"
                    f"📉 **PUT ($ {put_prem})**\n"
                    f"• **SL Price:** ${p_sl}\n"
                    f"• **Qty:** {p_lots} Contracts"
                )
                await update.message.reply_text(response, parse_mode='Markdown')
                return
        except Exception as e:
            pass # Ignore errors, might be normal chat

# --- PART 2: AUTOMATED MARKET MONITOR ---

def get_btc_price():
    """Fetch BTC price from Binance/CoinGecko"""
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10)
        return float(r.json()['price'])
    except:
        try:
            r = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", timeout=10)
            return float(r.json()['bitcoin']['usd'])
        except:
            return None

async def market_monitor_job(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 60 seconds to check Strategy Rules"""
    global base_price, session_high, session_low
    global high_alert_sent, low_alert_sent, daily_start_alert_sent, daily_close_alert_sent

    current_price = get_btc_price()
    if not current_price: return

    # Get Time in India
    now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
    
    # Update Session High/Low if tracking
    if base_price:
        if session_high is None: session_high = current_price
        if session_low is None: session_low = current_price
        session_high = max(session_high, current_price)
        session_low = min(session_low, current_price)

    # --- RULE 1: 8:00 AM START ---
    if now_ist.hour == 8 and now_ist.minute == 0 and not daily_start_alert_sent:
        # Reset Everything
        base_price = current_price
        session_high = current_price
        session_low = current_price
        high_alert_sent = False
        low_alert_sent = False
        daily_start_alert_sent = True
        daily_close_alert_sent = False
        
        high_level = current_price * 1.02
        low_level = current_price * 0.98
        
        msg = (f"🌅 <b>8:00 AM Entry Alert</b>\n\n"
               f"📉 Spot: ${current_price:,.2f}\n"
               f"🛡️ <b>Sell Zones (2% OTM):</b>\n"
               f"🔴 Call Strike: ~${high_level:,.0f}\n"
               f"🟢 Put Strike: ~${low_level:,.0f}")
        await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')

    # --- RULE 2: RESET FLAGS AT MIDNIGHT ---
    if now_ist.hour == 0 and now_ist.minute == 0:
        daily_start_alert_sent = False

    # --- RULE 3: BREAKOUT ALERTS ---
    if base_price:
        high_limit = base_price * 1.02
        low_limit = base_price * 0.98

        if current_price >= high_limit and not high_alert_sent:
            await context.bot.send_message(chat_id=CHAT_ID, text=f"🚨 <b>BREAKOUT (+2%)</b>\nPrice: ${current_price:,.2f}", parse_mode='HTML')
            high_alert_sent = True
        
        if current_price <= low_limit and not low_alert_sent:
            await context.bot.send_message(chat_id=CHAT_ID, text=f"🚨 <b>BREAKDOWN (-2%)</b>\nPrice: ${current_price:,.2f}", parse_mode='HTML')
            low_alert_sent = True

    # --- RULE 4: 5:30 PM REPORT ---
    if now_ist.hour == 17 and now_ist.minute == 30 and not daily_close_alert_sent:
        if base_price:
            failed = (session_high >= base_price * 1.02) or (session_low <= base_price * 0.98)
            status = "❌ <b>STOP LOSS HIT</b>" if failed else "✅ <b>PROFIT (Expired)</b>"
            
            msg = (f"🏁 <b>5:30 PM Close Report</b>\n\n{status}\n"
                   f"Entry: ${base_price:,.0f}\n"
                   f"High: ${session_high:,.0f}\n"
                   f"Low: ${session_low:,.0f}")
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
            daily_close_alert_sent = True
            
    print(f"Checked: ${current_price} at {now_ist.strftime('%H:%M')}")

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN not found!")
        exit(1)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add Calculator Handler (Listens for text)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_calculator))

    # Add Market Monitor (Runs every 60s)
    app.job_queue.run_repeating(market_monitor_job, interval=60, first=10)

    print("🚀 Bot is Running...")
    app.run_polling()
