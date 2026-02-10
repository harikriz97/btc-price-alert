#!/bin/bash

# Start the dashboard in the background
# Using gunicorn for better performance/reliability if deployed, or python if simple
if command -v gunicorn >/dev/null 2>&1; then
    gunicorn dashboard:app --bind 0.0.0.0:$PORT &
else
    python dashboard.py &
fi

# Start the trading bot in the foreground
python main.py
