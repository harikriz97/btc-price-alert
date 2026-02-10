from flask import Flask, render_template, jsonify, request
import json
import os
from datetime import datetime

app = Flask(__name__)
LOG_FILE = "trade_logs.json"

def load_logs():
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, 'r') as f:
                # Load and sort by timestamp descending
                logs = json.load(f)
                return sorted(logs, key=lambda x: x['timestamp'], reverse=True)
        except Exception as e:
            print(f"Error loading logs: {e}")
            return []
    return []

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/logs')
def get_logs():
    logs = load_logs()
    return jsonify(logs)

@app.route('/api/send_telegram', methods=['POST'])
def send_telegram():
    import requests
    try:
        data = request.json
        log_index = int(data.get('index', 0))
        logs = load_logs()
        
        if log_index < len(logs):
            log_entry = logs[log_index]
            
            # Format Message
            details = log_entry.get('trade_details', {})
            checks = log_entry.get('checks', {})
            
            # Construct message with safe gets
            status = log_entry.get('status', 'UNKNOWN')
            reason = log_entry.get('reason', 'N/A')
            hv = log_entry.get('hv', 'N/A')
            spot = log_entry.get('spot_price', 'N/A')
            
            msg = (
                f"📢 <b>MANUAL ALERT</b>\n\n"
                f"<b>Status:</b> {status}\n"
                f"<b>Reason:</b> {reason}\n\n"
                f"📍 <b>Spot:</b> ${spot}\n"
                f"📊 <b>HV:</b> {hv}%\n"
            )
            
            if details:
                c_strike = details.get('call_strike', 0)
                p_strike = details.get('put_strike', 0)
                c_prem = details.get('call_premium', 0)
                p_prem = details.get('put_premium', 0)
                
                msg += (
                    f"\n📞 <b>Call:</b> ${c_strike} (${c_prem})\n"
                    f"📉 <b>Put:</b> ${p_strike} (${p_prem})\n"
                    f"💰 <b>Total:</b> ${c_prem + p_prem:.1f}"
                )
            
            # Send to Telegram
            bot_token = os.environ.get('BOT_TOKEN')
            chat_id = os.environ.get('CHAT_ID')
            
            if not bot_token or not chat_id:
                return jsonify({"status": "error", "message": "Bot Token or Chat ID missing (Check env)"}), 500
                
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": msg,
                "parse_mode": "HTML"
            }
            
            resp = requests.post(url, json=payload, timeout=10)
            
            if resp.status_code == 200:
                print(f"Telegram sent: {resp.json()}")
                return jsonify({"status": "success", "message": "Notification sent!"})
            else:
                print(f"Telegram failed: {resp.text}")
                return jsonify({"status": "error", "message": f"Telegram API Error: {resp.text}"}), 500
                
        return jsonify({"status": "error", "message": "Log entry not found"}), 404
        
    except Exception as e:
        print(f"Telegram Exception: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500



@app.template_filter('format_datetime')
def format_datetime(value):
    dt = datetime.fromisoformat(value)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting Dashboard on http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=True)
