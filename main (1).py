import requests
import time
from datetime import datetime
import pytz
import os

# Environment variables (set these in Railway)
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
CHECK_INTERVAL = 60  # Check every 60 seconds

# Global state
base_price = None
high_alert_sent = False
low_alert_sent = False
daily_alert_sent_today = False

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
        response = requests.post(TELEGRAM_API_URL, data=payload, timeout=10)
        response.raise_for_status()
        print(f"✅ Message sent at {datetime.now(pytz.timezone('Asia/Kolkata'))}")
        return True
    except Exception as e:
        print(f"❌ Error sending message: {e}")
        return False

def send_daily_alert(price):
    """Send daily 8 AM alert"""
    global base_price, high_alert_sent, low_alert_sent, daily_alert_sent_today
    
    base_price = round(price, 2)
    high_level = round(base_price * 1.02, 2)
    low_level = round(base_price * 0.98, 2)
    
    message = f"""
🔔 <b>BTC/USD Daily Alert</b> 🔔
⏰ Time: 8:00 AM IST

📊 <b>Base Price:</b> ${base_price:,.2f}

📈 <b>High Alert (+2%):</b> ${high_level:,.2f}
📉 <b>Low Alert (-2%):</b> ${low_level:,.2f}

━━━━━━━━━━━━━━━━━━━━
🔍 Monitoring active!
You'll get alerts when price hits these levels!
    """.strip()
    
    send_telegram(message)
    high_alert_sent = False
    low_alert_sent = False
    daily_alert_sent_today = True
    print(f"📊 Daily alert sent - Base Price: ${base_price:,.2f}")

def check_alerts(price):
    """Check if price hit ±2% thresholds"""
    global high_alert_sent, low_alert_sent, base_price
    
    if not base_price:
        return
    
    high_level = base_price * 1.02
    low_level = base_price * 0.98
    change = ((price - base_price) / base_price) * 100
    
    # HIGH alert
    if price >= high_level and not high_alert_sent:
        message = f"""
🚨 <b>HIGH ALERT TRIGGERED!</b> 🚨

📈 BTC has reached +2% level!

📊 <b>Base Price:</b> ${base_price:,.2f}
💰 <b>Current Price:</b> ${price:,.2f}
📊 <b>Change:</b> +{change:.2f}%

⏰ Time: {datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%I:%M %p IST')}

🎯 Target: ${round(high_level, 2):,.2f}
        """.strip()
        send_telegram(message)
        high_alert_sent = True
        print(f"🚨 HIGH ALERT SENT! Price: ${price:,.2f}")
    
    # LOW alert
    elif price <= low_level and not low_alert_sent:
        message = f"""
🚨 <b>LOW ALERT TRIGGERED!</b> 🚨

📉 BTC has reached -2% level!

📊 <b>Base Price:</b> ${base_price:,.2f}
💰 <b>Current Price:</b> ${price:,.2f}
📊 <b>Change:</b> {change:.2f}%

⏰ Time: {datetime.now(pytz.timezone('Asia/Kolkata')).strftime('%I:%M %p IST')}

🎯 Target: ${round(low_level, 2):,.2f}
        """.strip()
        send_telegram(message)
        low_alert_sent = True
        print(f"🚨 LOW ALERT SENT! Price: ${price:,.2f}")

def main():
    """Main monitoring loop"""
    global base_price, daily_alert_sent_today
    
    print("🚀 BTC Price Alert Bot Started!")
    print(f"⏰ Current Time: {datetime.now(pytz.timezone('Asia/Kolkata'))}")
    print(f"🔍 Checking price every {CHECK_INTERVAL} seconds")
    print("="*60)
    
    # Initialize base price
    initial_price = get_btc_price()
    if initial_price:
        base_price = round(initial_price, 2)
        print(f"📊 Initial Base Price: ${base_price:,.2f}")
        print(f"📈 High Alert Level (+2%): ${round(base_price * 1.02, 2):,.2f}")
        print(f"📉 Low Alert Level (-2%): ${round(base_price * 0.98, 2):,.2f}")
        print("="*60)
    
    last_check_date = None
    
    # Main loop
    while True:
        try:
            current_price = get_btc_price()
            
            if current_price:
                now_ist = datetime.now(pytz.timezone('Asia/Kolkata'))
                current_date = now_ist.strftime('%Y-%m-%d')
                
                # Reset daily flag at new day
                if last_check_date != current_date:
                    daily_alert_sent_today = False
                    last_check_date = current_date
                
                # Send daily alert at 8:00 AM IST
                if now_ist.hour == 8 and now_ist.minute == 0 and not daily_alert_sent_today:
                    send_daily_alert(current_price)
                
                # Check price thresholds
                check_alerts(current_price)
                
                # Print current status
                if base_price:
                    change = ((current_price - base_price) / base_price) * 100
                    print(f"[{now_ist.strftime('%I:%M:%S %p')}] Price: ${current_price:,.2f} | Change: {change:+.2f}%")
            
            time.sleep(CHECK_INTERVAL)
            
        except KeyboardInterrupt:
            print("\n🛑 Bot stopped")
            break
        except Exception as e:
            print(f"❌ Error in main loop: {e}")
            time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    if not CHAT_ID or not BOT_TOKEN:
        print("\n⚠️  ERROR: Set BOT_TOKEN and CHAT_ID in Railway environment variables!")
        print("Go to Railway dashboard → Variables tab → Add them\n")
    else:
        main()
