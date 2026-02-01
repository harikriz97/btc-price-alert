import requests
import time
from datetime import datetime
import pytz
import os

# Environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
CHECK_INTERVAL = 60  # Check every 60 seconds

# Global state
base_price = None
session_high = None
session_low = None

high_alert_sent = False
low_alert_sent = False
daily_start_alert_sent = False
daily_close_alert_sent = False

def get_btc_price():
    """Fetch BTC price from Binance, fallback to CoinGecko"""
    try:
        response = requests.get("https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10)
        response.raise_for_status()
        return float(response.json()['price'])
    except:
        try:
            response = requests.get("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", timeout=10)
            return float(response.json()['bitcoin']['usd'])
        except:
            return None

def send_telegram(message):
    """Send message to Telegram"""
    try:
        payload = {'chat_id': CHAT_ID, 'text': message, 'parse_mode': 'HTML'}
        requests.post(TELEGRAM_API_URL, data=payload, timeout=10)
        print(f"✅ Message sent.")
        return True
    except Exception as e:
        print(f"❌ Error sending message: {e}")
        return False

def reset_daily_stats(price):
    """Reset all trackers at 8:00 AM"""
    global base_price, session_high, session_low, high_alert_sent, low_alert_sent
    global daily_start_alert_sent, daily_close_alert_sent
    
    base_price = price
    session_high = price
    session_low = price
    
    # Reset alert flags
    high_alert_sent = False
    low_alert_sent = False
    daily_start_alert_sent = True
    daily_close_alert_sent = False  # Reset so 5:30 PM report can run
    
    send_start_report(price)

def update_session_stats(price):
    """Track the highest and lowest price since 8:00 AM"""
    global session_high, session_low
    
    if session_high is None: 
        session_high = price
        session_low = price

    if price > session_high:
        session_high = price
    if price < session_low:
        session_low = price

def send_start_report(price):
    """8:00 AM Entry Report"""
    high_level = price * 1.02
    low_level = price * 0.98
    
    message = f"""
🌅 <b>Market Open (8:00 AM) - Entry Taken</b>

📉 <b>Spot Price:</b> ${price:,.2f}

🛡️ <b>Selling 2% OTM Strikes:</b>
🔴 <b>Call Sell Level (+2%):</b> ${high_level:,.2f}
🟢 <b>Put Sell Level (-2%):</b> ${low_level:,.2f}

<i>Tracking performance until 5:30 PM...</i>
    """.strip()
    send_telegram(message)

def send_closing_report(current_price):
    """5:30 PM Performance Report"""
    global daily_close_alert_sent
    
    if not base_price:
        return

    # Calculate max moves in percentage
    max_up_move_pct = ((session_high - base_price) / base_price) * 100
    max_down_move_pct = ((session_low - base_price) / base_price) * 100
    
    # Determine Success or Failure
    # Success = Price never touched +2% or -2%
    failed_high = session_high >= (base_price * 1.02)
    failed_low = session_low <= (base_price * 0.98)
    
    if failed_high or failed_low:
        status = "❌ <b>FAILURE</b> (Stop Loss Hit)"
        result_text = "Price moved OUTSIDE the 2% range."
    else:
        status = "✅ <b>SUCCESS</b> (Premium Collected)"
        result_text = "Price stayed INSIDE the 2% range."

    message = f"""
🏁 <b>Market Close Report (5:30 PM)</b>

{status}
{result_text}

📊 <b>Session Stats (8am - 5:30pm):</b>
🔹 <b>Entry Price:</b> ${base_price:,.2f}
🔹 <b>Current Price:</b> ${current_price:,.2f}

📈 <b>Max High Move:</b> +{max_up_move_pct:.2f}%  (High: ${session_high:,.2f})
📉 <b>Max Low Move:</b> {max_down_move_pct:.2f}%  (Low: ${session_low:,.2f})

<i>Resetting for tomorrow...</i>
    """.strip()
    
    send_telegram(message)
    daily_close_alert_sent = True

def check_instant_alerts(price):
    """Check for immediate breakouts"""
    global high_alert_sent, low_alert_sent, base_price
    
    if not base_price: return
    
    high_level = base_price * 1.02
    low_level = base_price * 0.98
    
    if price >= high_level and not high_alert_sent:
        send_telegram(f"🚨 <b>BREAKOUT ALERT!</b>\nPrice hit +2% High!\nCurrent: ${price:,.2f}")
        high_alert_sent = True
    
    elif price <= low_level and not low_alert_sent:
        send_telegram(f"🚨 <b>BREAKDOWN ALERT!</b>\nPrice hit -2% Low!\nCurrent: ${price:,.2f}")
        low_alert_sent = True

def main():
    global daily_start_alert_sent, daily_close_alert_sent, base_price
    
    print("🚀 Bot Started - Tracking 8AM to 5:30PM Strategy")
    
    # Initialize with current price if restarting mid-day
    current_price = get_btc_price()
    if current_price:
        base_price = current_price
        update_session_stats(current_price)

    while True:
        try:
            current_price = get_btc_price()
            
            if current_price:
                now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
                
                # 1. 8:00 AM - Reset & Entry
                if now_ist.hour == 8 and now_ist.minute == 0 and not daily_start_alert_sent:
                    reset_daily_stats(current_price)
                
                # 2. Reset flags for next day (at midnight)
                if now_ist.hour == 0 and now_ist.minute == 0:
                     daily_start_alert_sent = False
                
                # 3. Update High/Low stats
                if base_price:
                    update_session_stats(current_price)
                    check_instant_alerts(current_price)
                
                # 4. 5:30 PM - Report
                if now_ist.hour == 17 and now_ist.minute == 30 and not daily_close_alert_sent:
                    send_closing_report(current_price)

                # Log to console
                if base_price:
                    print(f"[{now_ist.strftime('%H:%M')}] ${current_price:,.0f} | High: ${session_high:,.0f} | Low: ${session_low:,.0f}")
            
            time.sleep(CHECK_INTERVAL)
            
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
