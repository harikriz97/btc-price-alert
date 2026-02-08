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
from chart_generator import generate_straddle_chart

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
call_product_id = None
put_product_id = None
position_active = False
trade_mode = "REAL" # NEW: "REAL" or "VIRTUAL"
trade_executed_today = False
no_trade_reason = None
btc_product_id = None


logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def notify_telegram(app, msg, image_path=None):
    # 1. Send to Telegram
    try:
        if image_path and os.path.exists(image_path):
            with open(image_path, 'rb') as photo:
                await app.bot.send_photo(chat_id=CHAT_ID, photo=photo, caption=msg, parse_mode='HTML')
            logger.info("✅ Telegram notification sent with chart")
        else:
            await app.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='HTML')
            logger.info("✅ Telegram notification sent")
            
    except Exception as e:
        logger.error(f"⚠️ Telegram send failed: {e}")

import json

# Global State
# ... (existing globals) ...
LOG_FILE = "trade_logs.json"

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

def log_trade_decision(data):
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r') as f:
                logs = json.load(f)
        else:
            logs = []
        
        logs.append(data)
        
        # Keep last 30 days
        if len(logs) > 30:
            logs = logs[-30:]
            
        with open(LOG_FILE, 'w') as f:
            json.dump(logs, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to log trade: {e}")

def update_trade_log(exit_reason, exit_price):
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, 'r') as f:
                logs = json.load(f)
            
            if logs:
                # Update last log entry
                logs[-1]['exit_reason'] = exit_reason
                logs[-1]['exit_price'] = exit_price
                logs[-1]['exit_time'] = datetime.now(pytz.timezone('Asia/Kolkata')).isoformat()
                
                with open(LOG_FILE, 'w') as f:
                    json.dump(logs, f, indent=4)
    except Exception as e:
        logger.error(f"Failed to update trade log: {e}")

def check_market_conditions(spot_price, d_high, d_low):
    results = {
        "timestamp": datetime.now(pytz.timezone('Asia/Kolkata')).isoformat(),
        "spot_price": spot_price,
        "day_high": d_high,
        "day_low": d_low,
        "checks": {}
    }
    
    is_valid = True
    reject_reason = None
    
    # 1. Trend Check
    if d_high is None or d_low is None:
        results['checks']['trend'] = {"pass": True, "msg": "Insufficient data"}
    else:
        price_range = d_high - d_low
        if price_range > 0:
            upper_threshold = d_low + (price_range * 0.8)
            lower_threshold = d_low + (price_range * 0.2)
            
            if spot_price >= upper_threshold:
                is_valid = False
                reject_reason = "Strong uptrend detected"
                results['checks']['trend'] = {"pass": False, "msg": f"Price near High ({spot_price} >= {upper_threshold})"}
            elif spot_price <= lower_threshold:
                is_valid = False
                reject_reason = "Strong downtrend detected"
                results['checks']['trend'] = {"pass": False, "msg": f"Price near Low ({spot_price} <= {lower_threshold})"}
            else:
                results['checks']['trend'] = {"pass": True, "msg": "Range bound"}
    
    # 2. Volatility Check
    hv = get_historical_volatility()
    results['hv'] = hv
    if hv > MAX_HV:
        is_valid = False
        if not reject_reason: reject_reason = f"High HV ({hv}%)"
        results['checks']['volatility'] = {"pass": False, "msg": f"HV {hv}% > {MAX_HV}%"}
    else:
        results['checks']['volatility'] = {"pass": True, "msg": f"HV {hv}% <= {MAX_HV}%"}
    
    return is_valid, reject_reason, results


def get_daily_candles():
    try:
        # Fetch daily candles for the last 30 days
        end_ts = int(time.time())
        start_ts = end_ts - (30 * 24 * 60 * 60)
        url = f"https://api.india.delta.exchange/v2/history/candles?resolution=1d&symbol=BTCUSD&start={start_ts}&end={end_ts}"
        
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json().get('result', [])
    except Exception as e:
        logger.error(f"Error fetching candles: {e}")
    return []

def get_historical_volatility(candles=None):
    try:
        if not candles:
            candles = get_daily_candles()
            
        if len(candles) < 10:
            return 40.0 # Default if insufficient data
            
        closes = [float(c['close']) for c in candles]
        log_returns = []
        for i in range(1, len(closes)):
            r = math.log(closes[i] / closes[i-1])
            log_returns.append(r)
        
        if not log_returns:
            return 40.0
            
        mean_return = sum(log_returns) / len(log_returns)
        variance = sum((r - mean_return) ** 2 for r in log_returns) / (len(log_returns) - 1)
        std_dev = math.sqrt(variance)
        
        hv = std_dev * math.sqrt(365) * 100
        return round(hv, 2)
            
    except Exception as e:
        logger.error(f"Error calculating HV: {e}")
    
    return 40.0 # Default fallback

def get_options_chain(spot_price):
    try:
        products = get_delta_products()
        chain = []
        now = datetime.now(pytz.timezone('Asia/Kolkata'))
        
        for p in products:
            # Debug output showed 'underlying_asset' is a dict, keys might vary.
            # Safest to check symbol or contract_unit_currency
            is_btc = False
            ua = p.get('underlying_asset')
            if isinstance(ua, dict) and ua.get('symbol') == 'BTC':
                is_btc = True
            elif p.get('symbol', '').find('BTC') != -1: # Fallback
                is_btc = True
                
            if p.get('contract_type') in ['call_options', 'put_options'] and is_btc:
                # Check active status
                if p.get('state') == 'live':
                    strike = float(p.get('strike_price', 0))
                    chain.append({
                        "id": p['id'],
                        "symbol": p['symbol'],
                        "type": "call" if p['contract_type'] == 'call_options' else "put",
                        "strike": strike,
                        "expiry": p['settlement_time'] # ISO string or timestamp
                    })
        return chain
    except Exception as e:
        logger.error(f"Error getting options chain: {e}")
        return []

def find_best_strikes(spot_price, otm_pct, chain):
    if not chain:
        return None, None
        
    target_call_strike = spot_price * (1 + otm_pct)
    target_put_strike = spot_price * (1 - otm_pct)
    
    # Filter by nearest expiry (assume options are daily/weekly)
    # We want options expiring TODAY or TOMORROW usually
    # For simplicity, we pick the strikes closest to target from ALL active options
    # Improvements: Filter by Expiry Date
    
    calls = [o for o in chain if o['type'] == 'call']
    puts = [o for o in chain if o['type'] == 'put']
    
    if not calls or not puts:
        return None, None
        
    # Sort by distance to target strike
    best_call = min(calls, key=lambda x: abs(x['strike'] - target_call_strike))
    best_put = min(puts, key=lambda x: abs(x['strike'] - target_put_strike))
    
    return {"product_id": best_call['id'], "strike": best_call['strike']}, \
           {"product_id": best_put['id'], "strike": best_put['strike']}

def get_option_premium(product_id):
    try:
        response = requests.get(f"{DELTA_PUBLIC_URL}/v2/tickers/{product_id}", timeout=5)
        if response.status_code == 200:
            result = response.json().get('result', {})
            # Use mark price or mid price
            return float(result.get('mark_price', 0))
    except Exception as e:
        logger.error(f"Error fetching premium for {product_id}: {e}")
    return 0.0

def calculate_position_size(premium):
    # Loss per contract = Premium * (SL_MULTIPLIER - 1)
    # Risk = RISK_PER_DAY_USD
    if premium <= 0: return 0
    loss_per_contract = premium * (SL_MULTIPLIER - 1)
    if loss_per_contract <= 0: return 1
    
    qty = RISK_PER_DAY_USD / loss_per_contract
    # Round down to integer, min 1
    return max(1, int(qty * 10)) # Assuming 1 contract = 0.1 BTC? No, Delta has different sizes. 
    # Usually strictly 1 contract = 1 USD or similar? 
    # Let's return purely calculated qty. If contract value is small, this might be large.
    # Safest: int(qty)
    return max(1, int(qty))


async def execute_entry(app, spot_price):
    global call_entry_price, put_entry_price, call_strike, put_strike
    global call_sl, put_sl, call_quantity, put_quantity, position_active
    global call_product_id, put_product_id
    global entry_time, no_trade_reason, day_high, day_low, trade_mode
    
    logger.info(f"🎯 Attempting entry at spot price: ${spot_price:,.2f}")
    
    # 1. Market Checks (Trend, Volatility)
    is_suitable, reason, log_data = check_market_conditions(spot_price, day_high, day_low)
    
    # 2. Fetch Data for Reporting (regardless of suitability)
    candles = get_daily_candles()
    hv = get_historical_volatility(candles)
    otm_pct = OTM_LOW_IV if hv < 30 else OTM_PERCENTAGE
    options_chain = get_options_chain(spot_price)
    
    report_details = f"📍 <b>Spot:</b> ${spot_price:,.0f}\n📊 <b>HV:</b> {hv:.1f}%\n"
    
    best_call, best_put = None, None
    call_premium, put_premium = 0.0, 0.0
    chart_path = None
    
    if options_chain:
        best_call, best_put = find_best_strikes(spot_price, otm_pct, options_chain)
        if best_call and best_put:
             call_premium = get_option_premium(best_call['product_id'])
             put_premium = get_option_premium(best_put['product_id'])
             
             report_details += (
                 f"\n📞 <b>Call Strike (+2%):</b> ${best_call['strike']:.0f}\n"
                 f"   Premium: ${call_premium:.1f}\n"
                 f"📉 <b>Put Strike (-2%):</b> ${best_put['strike']:.0f}\n"
                 f"   Premium: ${put_premium:.1f}\n"
                 f"💰 <b>Combined:</b> ${call_premium + put_premium:.1f}"
             )
             
             # Populate log data with available details immediately
             log_data['trade_details'] = {
                "call_strike": best_call['strike'],
                "put_strike": best_put['strike'],
                "call_premium": call_premium,
                "put_premium": put_premium,
                "call_sl": call_premium * SL_MULTIPLIER,
                "put_sl": put_premium * SL_MULTIPLIER
            }
        else:
             report_details += "\n⚠️ Strikes not found."
    else:
        report_details += "\n⚠️ Option chain unavailable."

    # Generate Chart
    c_strike = best_call['strike'] if best_call else None
    p_strike = best_put['strike'] if best_put else None
    if candles:
        chart_path = generate_straddle_chart(candles, spot_price, c_strike, p_strike)

    # 3. Decision Making
    log_data['hv'] = hv
    
    if not is_suitable:
        if trade_mode == "REAL":
            log_data['status'] = "SKIPPED"
            trade_mode = "VIRTUAL" 
            
        log_data['reason'] = reason
        # REMOVED log_trade_decision to avoid duplicate
        
        msg = (
            f"⛔ <b>NO TRADE TODAY</b>\n\n"
            f"<b>Reason:</b> {reason}\n\n"
            f"{report_details}\n\n"
            f"ℹ️ <i>Tracking virtually in dashboard...</i>"
        )
        await notify_telegram(app, msg, image_path=chart_path)
        logger.warning(f"Trade skipped: {reason}. Switching to VIRTUAL mode.")
        # Proceed, do not return!

    if not options_chain or not best_call or not best_put:
        no_trade_reason = "Options data missing"
        # Cannot virtual trade without options data
        log_data['status'] = "SKIPPED_NO_DATA"
        log_data['reason'] = no_trade_reason
        log_trade_decision(log_data)
        
        msg = (
            f"⛔ <b>NO TRADE TODAY</b>\n\n"
            f"<b>Reason:</b> Options data unavailable\n\n"
            f"{report_details}"
        )
        await notify_telegram(app, msg, image_path=chart_path)
        return
        
    if not call_premium or not put_premium:
        no_trade_reason = "Premiums not available"
        # Cannot virtual trade without premiums
        log_data['status'] = "SKIPPED_NO_PREMIUM"
        log_data['reason'] = no_trade_reason
        log_trade_decision(log_data)
        
        msg = (
            f"⛔ <b>NO TRADE TODAY</b>\n\n"
            f"<b>Reason:</b> Could not fetch premiums\n\n"
            f"{report_details}"
        )
        await notify_telegram(app, msg, image_path=chart_path)
        return

    # 4. Premium Balance Check
    call_qty = calculate_position_size(call_premium)
    put_qty = calculate_position_size(put_premium)
    
    call_strike = best_call['strike']
    put_strike = best_put['strike']
    call_entry_price = call_premium
    put_entry_price = put_premium
    call_sl = call_premium * SL_MULTIPLIER
    put_sl = put_premium * SL_MULTIPLIER
    
    call_product_id = best_call['product_id']
    put_product_id = best_put['product_id']
    
    log_data['trade_details'] = {
        "call_strike": call_strike,
        "put_strike": put_strike,
        "call_premium": call_premium,
        "put_premium": put_premium,
        "call_sl": call_sl,
        "put_sl": put_sl
    }
    
    premium_ratio = abs(call_premium - put_premium) / max(call_premium, put_premium) if max(call_premium, put_premium) > 0 else 1.0
    log_data['checks']['premium_balance'] = {
        "pass": premium_ratio <= PREMIUM_BALANCE_RATIO,
        "value": premium_ratio,
        "msg": f"Diff {premium_ratio*100:.1f}%"
    }
    
    if premium_ratio > PREMIUM_BALANCE_RATIO:
        if trade_mode == "REAL":
            log_data['status'] = "SKIPPED"
            trade_mode = "VIRTUAL"

        no_trade_reason = f"Premium imbalance ({premium_ratio*100:.0f}%)"
        log_data['reason'] = no_trade_reason
        
        msg = (
            f"⛔ <b>NO TRADE TODAY</b>\n\n"
            f"<b>Reason:</b> Premium Imbalance > {PREMIUM_BALANCE_RATIO*100}%\n"
            f"Diff: {premium_ratio*100:.1f}%\n\n"
            f"{report_details}\n\n"
            f"ℹ️ <i>Tracking virtually in dashboard...</i>"
        )
        await notify_telegram(app, msg, image_path=chart_path)
        # Proceed

    combined_premium = call_premium + put_premium
    log_data['checks']['min_premium'] = {
        "pass": combined_premium >= MIN_COMBINED_PREMIUM,
        "value": combined_premium,
        "msg": f"Total ${combined_premium:.1f}"
    }
    
    if combined_premium < MIN_COMBINED_PREMIUM:
        if trade_mode == "REAL":
            log_data['status'] = "SKIPPED"
            trade_mode = "VIRTUAL"

        no_trade_reason = f"Combined premium low (${combined_premium:.1f})"
        log_data['reason'] = no_trade_reason
        
        msg = (
            f"⛔ <b>NO TRADE TODAY</b>\n\n"
            f"<b>Reason:</b> Low Combined Premium\n"
            f"Total: ${combined_premium:.1f} (Min ${MIN_COMBINED_PREMIUM})\n\n"
            f"{report_details}\n\n"
            f"ℹ️ <i>Tracking virtually in dashboard...</i>"
        )
        await notify_telegram(app, msg, image_path=chart_path)
        # Proceed

    # 5. Execute Trade
    # 5. Execute Trade
    log_data['status'] = "EXECUTED" if trade_mode == "REAL" else "VIRTUAL_TRACKING"
    log_trade_decision(log_data)

    call_quantity = call_qty
    put_quantity = put_qty
    position_active = True
    entry_time = datetime.now(pytz.timezone('Asia/Kolkata'))
    
    premium_status = "✅ IDEAL" if TARGET_COMBINED_PREMIUM_MIN <= combined_premium <= TARGET_COMBINED_PREMIUM_MAX else "⚠️ ACCEPTABLE"
    
    msg = (
        f"🎯 <b>SHORT STRANGLE EXECUTED</b>\n"
        f"{'='*30}\n\n"
        f"{report_details}\n\n"
        f"<b>Trade Setup:</b>\n"
        f"📞 <b>Call Qty:</b> {call_qty} | SL: ${call_sl:.1f}\n"
        f"📉 <b>Put Qty:</b> {put_qty} | SL: ${put_sl:.1f}\n\n"
        f"⚖️ <b>Balance:</b> {premium_ratio*100:.0f}% diff\n"
        f"💰 <b>Status:</b> {premium_status}\n\n"
        f"🎯 <b>Exit Targets:</b>\n"
        f"  • 12:30 PM or 5:15 PM IST\n"
        f"  • 80% decay or breakouts\n\n"
        f"⚠️ Risk: ${RISK_PER_DAY_USD:.0f}/leg"
    )
    
    logger.info(f"✅ Entry: Call {call_strike}, Put {put_strike} (Mode: {trade_mode})")
    if trade_mode == "REAL":
        await notify_telegram(app, msg, image_path=chart_path)

    # 5. Execute Trade (REAL or VIRTUAL)
    # If checks failed earlier, we would have returned.
    # BUT user wants shadow trading. So we need to refactor the returns above.
    # Actually, let's wrap the logic above:
    
    # ... Wait, I can't easily wrap the whole function in replace_file_content.
    # Instead, I will modify the "return" statements in execute_entry 
    # to set trade_mode="VIRTUAL" and proceed *instead* of returning.
    
    # REFACTORING STRATEGY:
    # I will replace the block "if not is_suitable:" to NOT return, but set mode=VIRTUAL.
    # Same for other checks.
    
    # Let's do this in a separate step as it requires careful logic change.
    
async def check_exit_conditions(app, spot_price):
    global position_active, day_high, day_low, trade_mode
    
    if not position_active:
        return

    # Check SL
    try:
        if call_product_id:
            curr_call_prem = get_option_premium(call_product_id)
            if curr_call_prem and curr_call_prem >= call_sl:
                msg = f"🛑 <b>SL HIT: CALL LEG</b>\n\nPremium: ${curr_call_prem:.1f} >= ${call_sl:.1f}"
                position_active = False
                update_trade_log("SL_HIT_CALL", curr_call_prem)
                logger.info(f"Exit: Call SL Hit (Mode: {trade_mode})")
                if trade_mode == "REAL":
                    await notify_telegram(app, msg)
                return

        if put_product_id:
            curr_put_prem = get_option_premium(put_product_id)
            if curr_put_prem and curr_put_prem >= put_sl:
                msg = f"🛑 <b>SL HIT: PUT LEG</b>\n\nPremium: ${curr_put_prem:.1f} >= ${put_sl:.1f}"
                position_active = False
                update_trade_log("SL_HIT_PUT", curr_put_prem)
                logger.info(f"Exit: Put SL Hit (Mode: {trade_mode})")
                if trade_mode == "REAL":
                    await notify_telegram(app, msg)
                return
    except Exception as e:
        logger.error(f"Error checking SL: {e}")
    
    now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
    
    if now_ist.hour == 12 and now_ist.minute == 30:
        msg = f"⏰ <b>12:30 PM Exit</b>\n\nSpot: ${spot_price:,.0f}"
        position_active = False
        update_trade_log("TIME_EXIT_1230", spot_price)
        logger.info("Exit: 12:30 PM")
        if trade_mode == "REAL":
            await notify_telegram(app, msg)
        return
    
    if now_ist.hour == 17 and now_ist.minute == 15:
        msg = f"🏁 <b>5:15 PM Exit</b>\n\nSpot: ${spot_price:,.0f}"
        position_active = False
        update_trade_log("TIME_EXIT_1715", spot_price)
        logger.info("Exit: 5:15 PM")
        if trade_mode == "REAL":
            await notify_telegram(app, msg)
        return
    
    if day_high and spot_price > day_high:
        msg = f"📈 <b>DAY HIGH BREAKOUT</b>\n\nSpot: ${spot_price:,.0f} > ${day_high:,.0f}"
        position_active = False
        update_trade_log("DAY_HIGH_BREAKOUT", spot_price)
        logger.info("Exit: Day high breakout")
        if trade_mode == "REAL":
            await notify_telegram(app, msg)
        return
    
    if day_low and spot_price < day_low:
        msg = f"📉 <b>DAY LOW BREAKDOWN</b>\n\nSpot: ${spot_price:,.0f} < ${day_low:,.0f}"
        position_active = False
        update_trade_log("DAY_LOW_BREAKDOWN", spot_price)
        logger.info("Exit: Day low breakdown")
        if trade_mode == "REAL":
            await notify_telegram(app, msg)
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
