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
logger = logging.getLogger(__name__)

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
        except Exception as e:
            logger.error(f"Calculator error: {e}")

# --- PART 2: AUTO MARKET MONITOR ---

def get_btc_price():
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10)
        return float(r.json()['price'])
    except Exception as e:
        logger.error(f"Error fetching BTC price: {e}")
        return None

async def run_market_monitor(app):
    """Background loop to check prices independently of the bot"""
    global base_price, session_high, session_low, high_alert_sent, low_alert_sent
    global daily_start_alert_sent, daily_close_alert_sent

    logger.info("🚀 Market Monitor Started...")
    
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
            logger.error(f"Error in monitor: {e}")
            await asyncio.sleep(60)

async def post_init(application):
    """Called after the bot starts - creates the background task"""
    logger.info("Post-init: Starting background monitor task")
    asyncio.create_task(run_market_monitor(application))

if __name__ == '__main__':
    # Detailed startup checks
    logger.info("=" * 50)
    logger.info("BTC Price Alert Bot Starting...")
    logger.info("=" * 50)
    
    if not BOT_TOKEN:
        logger.error("❌ ERROR: BOT_TOKEN environment variable is not set!")
        logger.error("Please set BOT_TOKEN in Railway environment variables")
        exit(1)
    
    # Validate token format
    if not BOT_TOKEN.count(':') == 1 or len(BOT_TOKEN) < 40:
        logger.error(f"❌ ERROR: BOT_TOKEN format appears invalid")
        logger.error(f"Token should be in format: 1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ")
        logger.error(f"Current token length: {len(BOT_TOKEN)} characters")
        exit(1)
    
    logger.info(f"✅ BOT_TOKEN found (length: {len(BOT_TOKEN)})")
    logger.info(f"✅ Token format appears valid")
    
    if not CHAT_ID:
        logger.warning("⚠️  WARNING: CHAT_ID not set - alerts will fail!")
        logger.warning("Please set CHAT_ID in Railway environment variables")
    else:
        logger.info(f"✅ CHAT_ID found: {CHAT_ID}")
    
    try:
        logger.info("🔧 Building Telegram Application...")
        app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
        logger.info("✅ Application built successfully")
        
        # Add Calculator Handler
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_calculator))
        logger.info("✅ Message handlers registered")
        
        logger.info("🚀 Starting bot polling...")
        logger.info("=" * 50)
        app.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        logger.error("=" * 50)
        logger.error(f"❌ FATAL ERROR: {type(e).__name__}")
        logger.error(f"Error message: {str(e)}")
        logger.error("=" * 50)
        logger.error("\nPossible causes:")
        logger.error("1. Invalid bot token - check if token is correct")
        logger.error("2. Bot was deleted in @BotFather")
        logger.error("3. Network connectivity issues")
        logger.error("4. Token has extra spaces or quotes")
        logger.error("\nTo fix:")
        logger.error("1. Go to @BotFather on Telegram")
        logger.error("2. Create a new bot or get your existing bot token")
        logger.error("3. Update BOT_TOKEN in Railway environment variables")
        logger.error("4. Make sure there are no spaces or quotes around the token")
        raise
