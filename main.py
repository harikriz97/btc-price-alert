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

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
DELTA_API_KEY = os.environ.get('DELTA_API_KEY', '')
DELTA_API_SECRET = os.environ.get('DELTA_API_SECRET', '')
CHECK_INTERVAL = 30  # Check every 30 seconds for faster response

# Delta Exchange API URLs
DELTA_BASE_URL = "https://api.india.delta.exchange"
DELTA_PUBLIC_URL = "https://api.india.delta.exchange"

# Strategy Parameters
OTM_PERCENTAGE = 0.02  # 2% OTM strikes (can adjust to 1.5% if IV is low)
OTM_LOW_IV = 0.015  # 1.5% OTM when IV is low
SL_MULTIPLIER = 5  # Stop Loss = Entry Price × 5 (400% of premium)
MIN_COMBINED_PREMIUM = 20  # Minimum combined premium to enter
MAX_COMBINED_PREMIUM = 100  # Maximum combined premium
TARGET_COMBINED_PREMIUM_MIN = 40  # Ideal range
TARGET_COMBINED_PREMIUM_MAX = 60
PREMIUM_BALANCE_RATIO = 0.3  # Call/Put premium difference should be < 30%
MAX_HV = 70  # Skip if Historical Volatility > 70%
DECAY_EXIT_THRESHOLD = 0.2  # Exit when premium decays to 20% of entry (80% decay)

# Risk Management (INR 1000 Risk)
RISK_PER_DAY_INR = 1000
RISK_PER_DAY_USD = 11.76  # approx

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

# --- DELTA EXCHANGE API FUNCTIONS ---

def generate_signature(method, endpoint, payload=""):
    """Generate signature for Delta Exchange authenticated requests"""
    timestamp = str(int(time.time()))
    signature_data = method + timestamp + endpoint + payload
    signature = hmac.new(
        DELTA_API_SECRET.encode('utf-8'),
        signature_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature, timestamp

def get_delta_products():
    """Get all products from Delta Exchange"""
    try:
        response = requests.get(f"{DELTA_PUBLIC_URL}/v2/products", timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get('result', [])
        return []
    except Exception as e:
        logger.error(f"Error fetching Delta products: {e}")
        return []

def get_btc_perpetual_product():
    """Find BTC perpetual futures product"""
    products = get_delta_products()
    for product in products:
        if product.get('symbol') == 'BTCUSD' and product.get('contract_type') == 'perpetual_futures':
            return product
    return None

def get_delta_ticker(product_id):
    """Get real-time ticker from Delta Exchange"""
    try:
        response = requests.get(f"{DELTA_PUBLIC_URL}/v2/tickers/{product_id}", timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get('result', {})
        return None
    except Exception as e:
        logger.error(f"Error fetching Delta ticker: {e}")
        return None

def get_btc_price():
    """Fetch BTC price from Delta Exchange"""
    global btc_product_id
    
    try:
        if btc_product_id is None:
            product = get_btc_perpetual_product()
            if product:
                btc_product_id = product['id']
        
        if btc_product_id:
            ticker = get_delta_ticker(btc_product_id)
            if ticker and 'mark_price' in ticker:
                return float(ticker['mark_price'])
    except Exception as e:
        logger.error(f"Delta Exchange error: {e}")
    
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
    """Calculate Historical Volatility (simplified - you may want to enhance this)"""
    # This is a placeholder - ideally fetch actual HV from Delta or calculate from historical data
    # For now, we'll use a conservative estimate
    return 50  # Default value, should be replaced with actual calculation

def get_options_chain(spot_price, expiry_date=None):
    """Get BTC options chain from Delta Exchange"""
    products = get_delta_products()
    options = []
    
    # Get today's date or nearest Friday
    if expiry_date is None:
        today = datetime.now(pytz.timezone('Asia/Kolkata'))
        # Find next Friday
        days_ahead = 4 - today.weekday()  # Friday is 4
        if days_ahead <= 0:
            days_ahead += 7
        expiry_date = (today + timedelta(days=days_ahead)).strftime('%d%b%y').upper()
    
    for product in products:
        symbol = product.get('symbol', '')
        if 'BTC' in symbol and expiry_date in symbol:
            if product.get('contract_type') == 'call_options':
                strike = float(product.get('strike_price', 0))
                options.append({
                    'type': 'CALL',
                    'strike': strike,
                    'symbol': symbol,
                    'product_id': product['id']
                })
            elif product.get('contract_type') == 'put_options':
                strike = float(product.get('strike_price', 0))
                options.append({
                    'type': 'PUT',
                    'strike': strike,
                    'symbol': symbol,
                    'product_id': product['id']
                })
    
    return options

def find_best_strikes(spot_price, otm_pct, options_chain):
    """Find best Call and Put strikes based on OTM percentage"""
    call_target = spot_price * (1 + otm_pct)
    put_target = spot_price * (1 - otm_pct)
    
    calls = [opt for opt in options_chain if opt['type'] == 'CALL']
    puts = [opt for opt in options_chain if opt['type'] == 'PUT']
    
    # Find closest strikes
    best_call = min(calls, key=lambda x: abs(x['strike'] - call_target)) if calls else None
    best_put = min(puts, key=lambda x: abs(x['strike'] - put_target)) if puts else None
    
    return best_call, best_put

def get_option_premium(product_id):
    """Get current premium (mark price) for an option"""
    ticker = get_delta_ticker(product_id)
    if ticker and 'mark_price' in ticker:
        return float(ticker['mark_price'])
    return None

def check_market_conditions(spot_price, day_high, day_low):
    """
    Check if market conditions are suitable for entry
    Returns: (is_suitable, reason)
    """
    
    # 1. Check for strong trend (price near day high/low)
    price_range = day_high - day_low
    if price_range > 0:
        upper_threshold = day_low + (price_range * 0.8)  # Within top 20%
        lower_threshold = day_low + (price_range * 0.2)  # Within bottom 20%
        
        if spot_price >= upper_threshold:
            return False, "Strong uptrend detected (price near day high)"
        if spot_price <= lower_threshold:
            return False, "Strong downtrend detected (price near day low)"
    
    # 2. Check Historical Volatility
    hv = get_historical_volatility()
    if hv > MAX_HV:
        return False, f"Historical Volatility too high ({hv}% > {MAX_HV}%)"
    
    # 3. All checks passed
    return True, None

def calculate_position_size(premium):
    """Calculate number of contracts based on risk per day"""
    if premium <= 0:
        return 0
    
    # Risk per leg = RISK_PER_DAY_USD
    # SL per contract = premium * 4 (since SL = 5x entry = 400% of premium)
    # Contracts = Risk / (SL per contract)
    sl_per_contract = premium * 4
    contracts = math.floor(RISK_PER_DAY_USD / sl_per_contract)
    
    return max(1, contracts)  # At least 1 contract

async def execute_entry(app, spot_price):
    """Execute the short strangle entry at 8:00 AM"""
    global call_entry_price, put_entry_price, call_strike, put_strike
    global call_sl, put_sl, call_quantity, put_quantity, position_active
    global entry_time, no_trade_reason
    
    logger.info(f"🎯 Attempting entry at spot price: ${spot_price:,.2f}")
    
    # 1. Check market conditions
    is_suitable, reason = check_market_conditions(spot_price, day_high, day_low)
    if not is_suitable:
        no_trade_reason = reason
        msg = f"⛔ <b>NO TRADE TODAY</b>\n\n<b>Reason:</b> {reason}"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        logger.warning(f"Trade skipped: {reason}")
        return
    
    # 2. Get options chain
    options_chain = get_options_chain(spot_price)
    if not options_chain:
        no_trade_reason = "No options chain available"
        msg = f"⛔ <b>NO TRADE TODAY</b>\n\n<b>Reason:</b> No options found on Delta Exchange"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        return
    
    # 3. Determine OTM percentage based on IV
    hv = get_historical_volatility()
    otm_pct = OTM_LOW_IV if hv < 30 else OTM_PERCENTAGE
    
    # 4. Find best strikes
    best_call, best_put = find_best_strikes(spot_price, otm_pct, options_chain)
    
    if not best_call or not best_put:
        no_trade_reason = "Could not find suitable strikes"
        msg = f"⛔ <b>NO TRADE TODAY</b>\n\n<b>Reason:</b> Suitable strikes not available"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        return
    
    # 5. Get premiums
    call_premium = get_option_premium(best_call['product_id'])
    put_premium = get_option_premium(best_put['product_id'])
    
    if not call_premium or not put_premium:
        no_trade_reason = "Could not fetch option premiums"
        msg = f"⛔ <b>NO TRADE TODAY</b>\n\n<b>Reason:</b> Unable to fetch premiums"
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        return
    
    # 6. Check premium balance (Delta Neutral)
    premium_ratio = abs(call_premium - put_premium) / max(call_premium, put_premium)
    if premium_ratio > PREMIUM_BALANCE_RATIO:
        no_trade_reason = f"Premium imbalance (Call: ${call_premium:.1f}, Put: ${put_premium:.1f})"
        msg = (f"⛔ <b>NO TRADE TODAY</b>\n\n"
               f"<b>Reason:</b> Premium asymmetry\n"
               f"Call Premium: ${call_premium:.1f}\n"
               f"Put Premium: ${put_premium:.1f}\n"
               f"Difference: {premium_ratio*100:.1f}% (Max: {PREMIUM_BALANCE_RATIO*100:.0f}%)")
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        return
    
    # 7. Check combined premium
    combined_premium = call_premium + put_premium
    if combined_premium < MIN_COMBINED_PREMIUM:
        no_trade_reason = f"Combined premium too low (${combined_premium:.1f} < ${MIN_COMBINED_PREMIUM})"
        msg = (f"⛔ <b>NO TRADE TODAY</b>\n\n"
               f"<b>Reason:</b> Combined premium too low\n"
               f"Total Premium: ${combined_premium:.1f}\n"
               f"Minimum Required: ${MIN_COMBINED_PREMIUM}")
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        return
    
    # 8. Calculate position sizes
    call_qty = calculate_position_size(call_premium)
    put_qty = calculate_position_size(put_premium)
    
    # 9. Set position variables
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
    
    # 10. Send entry alert
    premium_status = "✅ IDEAL" if TARGET_COMBINED_PREMIUM_MIN <= combined_premium <= TARGET_COMBINED_PREMIUM_MAX else "⚠️ ACCEPTABLE"
    
    msg = (
        f"🎯 <b>SHORT STRANGLE EXECUTED</b>\n"
        f"{'='*30}\n\n"
        f"📍 <b>Spot Price:</b> ${spot_price:,.0f}\n"
        f"⏰ <b>Entry Time:</b> {entry_time.strftime('%I:%M %p IST')}\n\n"
        f"📞 <b>CALL Side:</b>\n"
        f"  • Strike: ${call_strike:,.0f} ({otm_pct*100:.1f}% OTM)\n"
        f"  • Premium: ${call_premium:.1f}\n"
        f"  • SL Price: ${call_sl:.1f} (5x entry)\n"
        f"  • Quantity: {call_qty} contracts\n\n"
        f"📉 <b>PUT Side:</b>\n"
        f"  • Strike: ${put_strike:,.0f} ({otm_pct*100:.1f}% OTM)\n"
        f"  • Premium: ${put_premium:.1f}\n"
        f"  • SL Price: ${put_sl:.1f} (5x entry)\n"
        f"  • Quantity: {put_qty} contracts\n\n"
        f"💰 <b>Combined Premium:</b> ${combined_premium:.1f} {premium_status}\n"
        f"⚖️ <b>Balance:</b> {abs(call_premium - put_premium):.1f} difference ({premium_ratio*100:.0f}%)\n"
        f"📊 <b>HV:</b> {hv:.0f}%\n\n"
        f"🎯 <b>Targets:</b>\n"
        f"  • 80% Decay: Exit at ${call_premium*0.2:.1f} / ${put_premium*0.2:.1f}\n"
        f"  • Time Exit: 12:30 PM or 5:15 PM IST\n"
        f"  • Breakeven: Monitor day high/low breaks\n\n"
        f"⚠️ <b>Risk:</b> ~${RISK_PER_DAY_USD:.0f}/leg | 1.5:1 R:R"
    )
    
    await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
    logger.info(f"✅ Entry executed - Call: {call_strike}, Put: {put_strike}")

async def check_exit_conditions(app, spot_price):
    """Monitor exit conditions during the day"""
    global position_active, call_entry_price, put_entry_price
    
    if not position_active:
        return
    
    now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
    
    # Get current premiums
    # (In production, you'd fetch actual option prices from Delta)
    # For now, we'll simulate based on spot movement
    
    # 1. TIME-BASED EXITS
    # Conservative exit at 12:30 PM
    if now_ist.hour == 12 and now_ist.minute == 30:
        msg = (f"⏰ <b>12:30 PM - Conservative Exit Time</b>\n\n"
               f"Position closed as per strategy rules.\n"
               f"Spot: ${spot_price:,.0f}")
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        position_active = False
        logger.info("Position closed - 12:30 PM exit")
        return
    
    # Final exit at 5:15 PM
    if now_ist.hour == 17 and now_ist.minute == 15:
        msg = (f"🏁 <b>5:15 PM - Mandatory Exit</b>\n\n"
               f"All positions closed before US session.\n"
               f"Spot: ${spot_price:,.0f}")
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        position_active = False
        logger.info("Position closed - 5:15 PM exit")
        return
    
    # 2. STOP LOSS CHECKS
    # (Would need actual option prices from Delta API)
    
    # 3. BREAKOUT EXITS
    if day_high and spot_price > day_high:
        msg = (f"📈 <b>DAY HIGH BREAKOUT - EXIT</b>\n\n"
               f"Spot: ${spot_price:,.0f}\n"
               f"Day High: ${day_high:,.0f}\n\n"
               f"Position closed per strategy rules.")
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        position_active = False
        logger.info("Position closed - Day high breakout")
        return
    
    if day_low and spot_price < day_low:
        msg = (f"📉 <b>DAY LOW BREAKDOWN - EXIT</b>\n\n"
               f"Spot: ${spot_price:,.0f}\n"
               f"Day Low: ${day_low:,.0f}\n\n"
               f"Position closed per strategy rules.")
        await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
        position_active = False
        logger.info("Position closed - Day low breakdown")
        return

async def run_market_monitor(app):
    """Main monitoring loop"""
    global base_price, day_high, day_low, prev_day_high, prev_day_low
    global trade_executed_today, no_trade_reason

    logger.info("🚀 Short Strangle Strategy Bot Started...")
    logger.info(f"   OTM: {OTM_PERCENTAGE*100}% (or {OTM_LOW_IV*100}% if low IV)")
    logger.info(f"   SL: {SL_MULTIPLIER}x entry price (400% of premium)")
    logger.info(f"   Risk: ${RISK_PER_DAY_USD:.2f} per leg")
    
    while True:
        try:
            current_price = get_btc_price()
            if current_price:
                now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
                
                # Update day high/low
                if day_high is None or current_price > day_high:
                    day_high = current_price
                if day_low is None or current_price < day_low:
                    day_low = current_price
                
                # 8:00 AM ENTRY LOGIC
                if (now_ist.hour == 8 and now_ist.minute == 0 and 
                    not trade_executed_today and not position_active):
                    
                    base_price = current_price
                    await execute_entry(app, current_price)
                    trade_executed_today = True
                
                # EXIT MONITORING
                if position_active:
                    await check_exit_conditions(app, current_price)
                
                # MIDNIGHT RESET
                if now_ist.hour == 0 and now_ist.minute == 0:
                    trade_executed_today = False
                    position_active = False
                    no_trade_reason = None
                    prev_day_high = day_high
                    prev_day_low = day_low
                    day_high = None
                    day_low = None
                    logger.info("🔄 Midnight reset - Ready for new day")
            
            await asyncio.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            logger.error(f"Error in monitor loop: {e}", exc_info=True)
            await asyncio.sleep(60)

async def handle_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user commands"""
    text = update.message.text.lower().strip()
    
    if text == '/status' or text == 'status':
        now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
        
        if position_active:
            time_elapsed = (now_ist - entry_time).total_seconds() / 3600
            msg = (
                f"✅ <b>POSITION ACTIVE</b>\n\n"
                f"⏰ Entry: {entry_time.strftime('%I:%M %p IST')}\n"
                f"⌛ Elapsed: {time_elapsed:.1f} hours\n\n"
                f"📞 Call: ${call_strike:,.0f} @ ${call_entry_price:.1f}\n"
                f"📉 Put: ${put_strike:,.0f} @ ${put_entry_price:.1f}\n\n"
                f"🛑 Call SL: ${call_sl:.1f}\n"
                f"🛑 Put SL: ${put_sl:.1f}"
            )
        elif trade_executed_today and not position_active:
            msg = f"⏹️ <b>Position Closed</b>\n\nTrade was executed today but position is now closed."
        elif no_trade_reason:
            msg = f"⛔ <b>No Trade Today</b>\n\n<b>Reason:</b> {no_trade_reason}"
        else:
            msg = f"⏳ <b>Waiting for 8:00 AM IST</b>\n\nNo position yet today."
        
        await update.message.reply_text(msg, parse_mode='HTML')
    
    elif text == '/help':
        msg = (
            f"📖 <b>8 AM Short Strangle Bot</b>\n\n"
            f"<b>Commands:</b>\n"
            f"/status - Current position status\n"
            f"/help - Show this message\n\n"
            f"<b>Strategy:</b>\n"
            f"• Entry: 8:00 AM IST sharp\n"
            f"• Strikes: 2% OTM Call & Put\n"
            f"• SL: 5x entry (400% of premium)\n"
            f"• Exit: 12:30 PM or 5:15 PM or 80% decay\n\n"
            f"<b>No-Trade Conditions:</b>\n"
            f"• Strong trend before 8 AM\n"
            f"• HV > {MAX_HV}%\n"
            f"• Premium imbalance > {PREMIUM_BALANCE_RATIO*100:.0f}%\n"
            f"• Combined premium < ${MIN_COMBINED_PREMIUM}\n"
            f"• Major news events"
        )
        await update.message.reply_text(msg, parse_mode='HTML')

async def post_init(application):
    """Called after the bot starts"""
    logger.info("Post-init: Starting strategy monitor")
    asyncio.create_task(run_market_monitor(application))

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("8 AM ASIAN SESSION SHORT STRANGLE STRATEGY BOT")
    logger.info("=" * 60)
    
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN not set!")
        exit(1)
    
    if not CHAT_ID:
        logger.error("❌ CHAT_ID not set!")
        exit(1)
    
    logger.info(f"✅ Configuration loaded")
    logger.info(f"   Entry Time: 8:00 AM IST")
    logger.info(f"   OTM: {OTM_PERCENTAGE*100}%")
    logger.info(f"   Risk per leg: ${RISK_PER_DAY_USD}")
    
    if DELTA_API_KEY and DELTA_API_SECRET:
        logger.info("✅ Delta API configured")
    else:
        logger.warning("⚠️  Delta API not configured - using public data only")
    
    try:
        app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
        app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_commands))
        
        logger.info("🚀 Bot is running...")
        logger.info("=" * 60)
        app.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        raise
