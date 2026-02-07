from flask import Flask, render_template, jsonify
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

@app.template_filter('format_datetime')
def format_datetime(value):
    dt = datetime.fromisoformat(value)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting Dashboard on http://0.0.0.0:{port}")
    app.run(host='0.0.0.0', port=port, debug=True)
