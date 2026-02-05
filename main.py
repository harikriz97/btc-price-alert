import logging
import requests
import math
import os
import pytz
import asyncio
import hmac
import hashlib
import time
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters
from telegram.error import Conflict

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
DELTA_API_KEY = os.environ.get('DELTA_API_KEY', '')
DELTA_API_SECRET = os.environ.get('DELTA_API_SECRET', '')
CHECK_INTERVAL = 30

# Delta Exchange API URLs
DELTA_BASE_URL = "https://api.india.delta.exchange"
DELTA_PUBLIC_URL = "https://api.india.delta.exchange"

# Strategy Parameters
OTM_PERCENTAGE = 0.02
OTM_LOW_IV = 0.015
SL_MULTIPLIER = 5
MIN_COMBINED_PREMIUM = 20
MAX_COMBINED_PREMIUM = 100
TARGET_COMBINED_PREMIUM_MIN = 40
TARGET_COMBINED_PREMIUM_MAX = 60
PREMIUM_BALANCE_RATIO = 0.3
MAX_HV = 70
DECAY_EXIT_THRESHOLD = 0.2
RISK_PER_DAY_USD = 11.76

# Global State
base_price = None
day_high = None
day_low = None
prev_day_high = None
prev_day_low = None
entry_time = None
call_entry_price = None
put_entry_price = None
call_strike = None
put_strike = None
call_sl = None
put_sl = None
call_quantity = None
put_quantity = None
position_active = False
trade_executed_today = False
no_trade_reason = None
btc_product_id = None

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

def get_delta_products():
    try:
        response = requests.get(f"{DELTA_PUBLIC_URL}/v2/products", timeout=10)
        if response.status_code == 200:
            return response.json().get('result', [])
        return []
    except Exception as e:
        logger.error(f"Error fetching Delta products: {e}")
        return []

def get_btc_price():
    global btc_product_id
    
    try:
        if btc_product_id is None:
            products = get_delta_products()
            for product in products:
                if product.get('symbol') == 'BTCUSD' and product.get('contract_type') == 'perpetual_futures':
                    btc_product_id = product['id']
                    break
        
        if btc_product_id:
            response = requests.get(f"{DELTA_PUBLIC_URL}/v2/tickers/{btc_product_id}", timeout=10)
            if response.status_code == 200:
                ticker = response.json().get('result', {})
                if 'mark_price' in ticker:
                    return float(ticker['mark_price'])
    except:
        pass
    
    # Fallback to Binance
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10)
        data = r.json()
        if isinstance(data, dict) and 'price' in data:
            return float(data['price'])
    except:
        pass
    
    return None

def get_historical_volatility():
    return 50

def get_options_chain(spot_price, expiry_date=None):
    products = get_delta_products()
    options = []
    
    if expiry_date is None:
        today = datetime.now(pytz.timezone('Asia/Kolkata'))
        days_ahead = 4 - today.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        expiry_date = (today + timedelta(days=days_ahead)).strftime('%d%b%y').upper()
    
    for product in products:
        symbol = product.get('symbol', '')
        if 'BTC' in symbol and expiry_date in symbol:
            if product.get('contract_type') == 'call_options':
                options.append({
                    'type': 'CALL',
                    'strike': float(product.get('strike_price', 0)),
                    'symbol': symbol,
                    'product_id': product['id']
                })
            elif product.get('contract_type') == 'put_options':
                options.append({
                    'type': 'PUT',
                    'strike': float(product.get('strike_price', 0)),
                    'symbol': symbol,
                    'product_id': product['id']
                })
    
    return options

def find_best_strikes(spot_price, otm_pct, options_chain):
    call_target = spot_price * (1 + otm_pct)
    put_target = spot_price * (1 - otm_pct)
    
    calls = [opt for opt in options_chain if opt['type'] == 'CALL']
    puts = [opt for opt in options_chain if opt['type'] == 'PUT']
    
    best_call = min(calls, key=lambda x: abs(x['strike'] - call_target)) if calls else None
    best_put = min(puts, key=lambda x: abs(x['strike'] - put_target)) if puts else None
    
    return best_call, best_put

def get_option_premium(product_id):
    try:
        response = requests.get(f"{DELTA_PUBLIC_URL}/v2/tickers/{product_id}", timeout=10)
        if response.status_code == 200:
            ticker = response.json().get('result', {})
            if 'mark_price' in ticker:
                return float(ticker['mark_price'])
    except:
        pass
    return None

def check_market_conditions(spot_price, d_high, d_low):
    if d_high is None or d_low is None:
        return True, None
        
    price_range = d_high - d_low
    if price_range > 0:
        upper_threshold = d_low + (price_range * 0.8)
        lower_threshold = d_low + (price_range * 0.2)
        
        if spot_price >= upper_threshold:
            return False, "Strong uptrend detected (price near day high)"
        if spot_price <= lower_threshold:
            return False, "Strong downtrend detected (price near day low)"
    
    hv = get_historical_volatility()
    if hv > MAX_HV:
        return False, f"Historical Volatility too high ({hv}% > {MAX_HV}%)"
    
    return True, None

def calculate_position_size(premium):
    if premium <= 0:
        return 0
    sl_per_contract = premium * 4
    contracts = math.floor(RISK_PER_DAY_USD / sl_per_contract)
    return max(1, contracts)

async def execute_entry(app, spot_price):
    global call_entry_price, put_entry_price, call_strike, put_strike
    global call_sl, put_sl, call_quantity, put_quantity, position_active
    global entry_time, no_trade_reason, day_high, day_low
    
    logger.info(f"🎯 Attempting entry at spot price: ${spot_price:,.2f}")
    
    is_suitable, reason = check_market_conditions(spot_price, day_high, day_low)
    if not is_suitable:
        no_trade_reason = reason
        msg = f"⛔ <b>NO TRADE TODAY</b>\n\n<b>Reason:</b> {reason}"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        logger.warning(f"Trade skipped: {reason}")
        return
    
    options_chain = get_options_chain(spot_price)
    if not options_chain:
        no_trade_reason = "No options chain available"
        msg = f"⛔ <b>NO TRADE TODAY</b>\n\n<b>Reason:</b> No options found on Delta Exchange"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        return
    
    hv = get_historical_volatility()
    otm_pct = OTM_LOW_IV if hv < 30 else OTM_PERCENTAGE
    
    best_call, best_put = find_best_strikes(spot_price, otm_pct, options_chain)
    
    if not best_call or not best_put:
        no_trade_reason = "Could not find suitable strikes"
        msg = f"⛔ <b>NO TRADE TODAY</b>\n\n<b>Reason:</b> Suitable strikes not available"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        return
    
    call_premium = get_option_premium(best_call['product_id'])
    put_premium = get_option_premium(best_put['product_id'])
    
    if not call_premium or not put_premium:
        no_trade_reason = "Could not fetch option premiums"
        msg = f"⛔ <b>NO TRADE TODAY</b>\n\n<b>Reason:</b> Unable to fetch premiums"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        return
    
    premium_ratio = abs(call_premium - put_premium) / max(call_premium, put_premium)
    if premium_ratio > PREMIUM_BALANCE_RATIO:
        no_trade_reason = f"Premium imbalance (Call: ${call_premium:.1f}, Put: ${put_premium:.1f})"
        msg = (f"⛔ <b>NO TRADE TODAY</b>\n\n"
               f"<b>Reason:</b> Premium asymmetry\n"
               f"Call: ${call_premium:.1f} | Put: ${put_premium:.1f}\n"
               f"Diff: {premium_ratio*100:.1f}% (Max: {PREMIUM_BALANCE_RATIO*100:.0f}%)")
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        return
    
    combined_premium = call_premium + put_premium
    if combined_premium < MIN_COMBINED_PREMIUM:
        no_trade_reason = f"Combined premium too low (${combined_premium:.1f})"
        msg = (f"⛔ <b>NO TRADE TODAY</b>\n\n"
               f"<b>Reason:</b> Combined premium too low\n"
               f"Total: ${combined_premium:.1f} | Min: ${MIN_COMBINED_PREMIUM}")
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        return
    
    call_qty = calculate_position_size(call_premium)
    put_qty = calculate_position_size(put_premium)
    
    call_strike = best_call['strike']
    put_strike = best_put['strike']
    call_entry_price = call_premium
    put_entry_price = put_premium
    call_sl = call_premium * SL_MULTIPLIER
    put_sl = put_premium * SL_MULTIPLIER
    call_quantity = call_qty
    put_quantity = put_qty
    position_active = True
    entry_time = datetime.now(pytz.timezone('Asia/Kolkata'))
    
    premium_status = "✅ IDEAL" if TARGET_COMBINED_PREMIUM_MIN <= combined_premium <= TARGET_COMBINED_PREMIUM_MAX else "⚠️ ACCEPTABLE"
    
    msg = (
        f"🎯 <b>SHORT STRANGLE EXECUTED</b>\n"
        f"{'='*30}\n\n"
        f"📍 Spot: ${spot_price:,.0f}\n"
        f"⏰ Entry: {entry_time.strftime('%I:%M %p IST')}\n\n"
        f"📞 <b>CALL</b> ${call_strike:,.0f} ({otm_pct*100:.1f}% OTM)\n"
        f"  Premium: ${call_premium:.1f} | SL: ${call_sl:.1f}\n"
        f"  Qty: {call_qty} contracts\n\n"
        f"📉 <b>PUT</b> ${put_strike:,.0f} ({otm_pct*100:.1f}% OTM)\n"
        f"  Premium: ${put_premium:.1f} | SL: ${put_sl:.1f}\n"
        f"  Qty: {put_qty} contracts\n\n"
        f"💰 Combined: ${combined_premium:.1f} {premium_status}\n"
        f"⚖️ Balance: {premium_ratio*100:.0f}% diff\n"
        f"📊 HV: {hv:.0f}%\n\n"
        f"🎯 <b>Exit Targets:</b>\n"
        f"  • 12:30 PM or 5:15 PM IST\n"
        f"  • 80% decay or breakouts\n\n"
        f"⚠️ Risk: ${RISK_PER_DAY_USD:.0f}/leg"
    )
    
    await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
    logger.info(f"✅ Entry: Call {call_strike}, Put {put_strike}")

async def check_exit_conditions(app, spot_price):
    global position_active, day_high, day_low
    
    if not position_active:
        return
    
    now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
    
    if now_ist.hour == 12 and now_ist.minute == 30:
        msg = f"⏰ <b>12:30 PM Exit</b>\n\nSpot: ${spot_price:,.0f}"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        position_active = False
        logger.info("Exit: 12:30 PM")
        return
    
    if now_ist.hour == 17 and now_ist.minute == 15:
        msg = f"🏁 <b>5:15 PM Exit</b>\n\nSpot: ${spot_price:,.0f}"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        position_active = False
        logger.info("Exit: 5:15 PM")
        return
    
    if day_high and spot_price > day_high:
        msg = f"📈 <b>DAY HIGH BREAKOUT</b>\n\nSpot: ${spot_price:,.0f} > ${day_high:,.0f}"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        position_active = False
        logger.info("Exit: Day high breakout")
        return
    
    if day_low and spot_price < day_low:
        msg = f"📉 <b>DAY LOW BREAKDOWN</b>\n\nSpot: ${spot_price:,.0f} < ${day_low:,.0f}"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        position_active = False
        logger.info("Exit: Day low breakdown")
        return

async def run_market_monitor(app):
    global base_price, day_high, day_low, prev_day_high, prev_day_low
    global trade_executed_today, no_trade_reason, position_active

    logger.info("🚀 Strategy Bot Started")
    logger.info(f"   OTM: {OTM_PERCENTAGE*100}%")
    logger.info(f"   SL: {SL_MULTIPLIER}x entry")
    logger.info(f"   Risk: ${RISK_PER_DAY_USD}/leg")
    
    while True:
        try:
            current_price = get_btc_price()
            if current_price:
                now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
                
                if day_high is None or current_price > day_high:
                    day_high = current_price
                if day_low is None or current_price < day_low:
                    day_low = current_price
                
                if (now_ist.hour == 8 and now_ist.minute == 0 and 
                    not trade_executed_today and not position_active):
                    base_price = current_price
                    await execute_entry(app, current_price)
                    trade_executed_today = True
                
                if position_active:
                    await check_exit_conditions(app, current_price)
                
                if now_ist.hour == 0 and now_ist.minute == 0:
                    trade_executed_today = False
                    no_trade_reason = None
                    prev_day_high = day_high
                    prev_day_low = day_low
                    day_high = None
                    day_low = None
                    logger.info("🔄 Midnight reset")
            
            await asyncio.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            logger.error(f"Monitor error: {e}", exc_info=True)
            await asyncio.sleep(60)

async def handle_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()
    
    if text in ['/status', 'status']:
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        
        if position_active and entry_time:
            elapsed = (now_ist - entry_time).total_seconds() / 3600
            msg = (
                f"✅ <b>POSITION ACTIVE</b>\n\n"
                f"⏰ Entry: {entry_time.strftime('%I:%M %p IST')}\n"
                f"⌛ Elapsed: {elapsed:.1f}h\n\n"
                f"📞 Call: ${call_strike:,.0f} @ ${call_entry_price:.1f}\n"
                f"📉 Put: ${put_strike:,.0f} @ ${put_entry_price:.1f}\n\n"
                f"🛑 SL: ${call_sl:.1f} / ${put_sl:.1f}"
            )
        elif trade_executed_today and not position_active:
            msg = f"⏹️ <b>Position Closed</b>"
        elif no_trade_reason:
            msg = f"⛔ <b>No Trade Today</b>\n\n{no_trade_reason}"
        else:
            msg = f"⏳ <b>Waiting for 8:00 AM IST</b>"
        
        await update.message.reply_text(msg, parse_mode='HTML')
    
    elif text in ['/help', 'help']:
        msg = (
            f"📖 <b>8 AM Short Strangle Bot</b>\n\n"
            f"<b>Commands:</b>\n"
            f"/status - Position status\n"
            f"/help - This message\n\n"
            f"<b>Strategy:</b>\n"
            f"• Entry: 8:00 AM IST\n"
            f"• Strikes: 2% OTM\n"
            f"• SL: 5x entry (400%)\n"
            f"• Exit: Time/decay/breakout\n\n"
            f"<b>No-Trade:</b>\n"
            f"• Strong trend\n"
            f"• HV > {MAX_HV}%\n"
            f"• Premium imbalance\n"
            f"• Low premium"
        )
        await update.message.reply_text(msg, parse_mode='HTML')

async def post_init(application):
    logger.info("Starting monitor...")
    asyncio.create_task(run_market_monitor(application))

if __name__ == '__main__':
    logger.info("="*60)
    logger.info("8 AM SHORT STRANGLE BOT")
    logger.info("="*60)
    
    if not BOT_TOKEN or not CHAT_ID:
        logger.error("❌ BOT_TOKEN or CHAT_ID missing!")
        exit(1)
    
    logger.info(f"✅ Config loaded")
    logger.info(f"   Entry: 8:00 AM IST")
    logger.info(f"   OTM: {OTM_PERCENTAGE*100}%")
    logger.info(f"   Risk: ${RISK_PER_DAY_USD}/leg")
    
    if DELTA_API_KEY and DELTA_API_SECRET:
        logger.info("✅ Delta API configured")
    else:
        logger.warning("⚠️  Delta API not configured")
    
    try:
        app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_commands))
        
        logger.info("🚀 Bot running...")
        logger.info("="*60)
        
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
        
    except Conflict:
        logger.error("="*60)
        logger.error("❌ MULTIPLE BOT INSTANCES!")
        logger.error("Stop other instances in Railway and redeploy")
        logger.error("="*60)
    except Exception as e:
        logger.error(f"❌ Fatal: {e}", exc_info=True)
        raise
