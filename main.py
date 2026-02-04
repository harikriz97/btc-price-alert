import logging
import requests
import math
import os
import pytz
import asyncio
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
CHECK_INTERVAL = 60  # Check every 60 seconds

# Risk Calculator Config (INR 1000 Risk)
RISK_PER_DAY_USD = 11.76  # approx 1000 INR
CONSTANT_FACTOR = RISK_PER_DAY_USD / 0.003  # ~3920

# Global State
base_price = None
session_high = None
session_low = None
high_alert_sent = False
low_alert_sent = False
daily_start_alert_sent = False
daily_close_alert_sent = False

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- PART 1: SL CALCULATOR (Input: "c24 p42") ---

def calculate_leg_math(premium):
    try:
        p = float(premium)
        if p <= 0: return 0, 0
        sl_price = p * 5  # SL is 400% (Entry x 5)
        lots = math.floor(CONSTANT_FACTOR / p) 
        return int(sl_price), int(lots)
    except:
        return 0, 0

async def handle_calculator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Listens for 'c24 p42' and gives SL & Quantity"""
    text = update.message.text.lower().strip()
    
    if 'c' in text and 'p' in text:
        try:
            parts = text.replace("c", "").replace("p", " ").split()
            if len(parts) >= 2:
                call_prem, put_prem = parts[0], parts[1]
                
                c_sl, c_lots = calculate_leg_math(call_prem)
                p_sl, p_lots = calculate_leg_math(put_prem)

                response = (
                    f"📊 **Risk Calculator**\n"
                    f"------------------------\n"
                    f"📈 **CALL ($ {call_prem})**\n"
                    f"• **SL Price:** ${c_sl}\n"
                    f"• **Qty:** {c_lots} Lots\n"
                    f"------------------------\n"
                    f"📉 **PUT ($ {put_prem})**\n"
                    f"• **SL Price:** ${p_sl}\n"
                    f"• **Qty:** {p_lots} Lots"
                )
                await update.message.reply_text(response, parse_mode='Markdown')
        except:
            pass 

# --- PART 2: AUTO MARKET MONITOR ---

def get_btc_price():
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10)
        return float(r.json()['price'])
    except:
        return None

async def run_market_monitor(app):
    """Background loop to check prices independently of the bot"""
    global base_price, session_high, session_low, high_alert_sent, low_alert_sent
    global daily_start_alert_sent, daily_close_alert_sent

    print("🚀 Market Monitor Started...")
    
    while True:
        try:
            current_price = get_btc_price()
            if current_price:
                now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))

                # 1. Update Session High/Low
                if base_price:
                    if session_high is None: session_high = current_price
                    if session_low is None: session_low = current_price
                    session_high = max(session_high, current_price)
                    session_low = min(session_low, current_price)

                # 2. 8:00 AM Entry Alert
                if now_ist.hour == 8 and now_ist.minute == 0 and not daily_start_alert_sent:
                    base_price = current_price
                    session_high = current_price
                    session_low = current_price
                    high_alert_sent = False
                    low_alert_sent = False
                    daily_start_alert_sent = True
                    daily_close_alert_sent = False
                    
                    high_strike = current_price * 1.02
                    low_strike = current_price * 0.98
                    
                    msg = (f"🌅 <b>8:00 AM Started</b>\n"
                           f"Price: ${current_price:,.0f}\n"
                           f"Sell Call Strike: ${high_strike:,.0f}\n"
                           f"Sell Put Strike: ${low_strike:,.0f}")
                    await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')

                # 3. Midnight Reset
                if now_ist.hour == 0 and now_ist.minute == 0:
                    daily_start_alert_sent = False

                # 4. Breakout Alerts (Spot SL)
                if base_price:
                    high_limit = base_price * 1.02
                    low_limit = base_price * 0.98

                    if current_price >= high_limit and not high_alert_sent:
                        await app.bot.send_message(chat_id=CHAT_ID, text=f"🚨 <b>STOP LOSS HIT (+2%)</b>\nPrice: ${current_price:,.0f}", parse_mode='HTML')
                        high_alert_sent = True
                    
                    if current_price <= low_limit and not low_alert_sent:
                        await app.bot.send_message(chat_id=CHAT_ID, text=f"🚨 <b>STOP LOSS HIT (-2%)</b>\nPrice: ${current_price:,.0f}", parse_mode='HTML')
                        low_alert_sent = True

                # 5. 5:30 PM Close Report
                if now_ist.hour == 17 and now_ist.minute == 30 and not daily_close_alert_sent:
                    if base_price:
                        msg = f"🏁 <b>5:30 PM Close</b>\nHigh: ${session_high:,.0f}\nLow: ${session_low:,.0f}"
                        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
                        daily_close_alert_sent = True

            await asyncio.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"Error in monitor: {e}")
            await asyncio.sleep(60)

if __name__ == '__main__':
    if not BOT_TOKEN:
        print("Error: BOT_TOKEN not found!")
        exit(1)

    # Initialize Bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    # Add Calculator Handler
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_calculator))

    # Run Bot + Monitor together
    loop = asyncio.get_event_loop()
    loop.create_task(run_market_monitor(app))
    
    print("🤖 Bot is Running...")
    app.run_polling()
