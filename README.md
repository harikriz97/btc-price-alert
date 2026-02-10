# BTC Price Alert Bot

🚀 Real-time Bitcoin price monitoring with Telegram alerts

## Features
- ✅ Daily alert at 8:00 AM IST with base price and 2% levels
- ✅ Instant alert when BTC price goes +2% above base
- ✅ Instant alert when BTC price goes -2% below base
- ✅ Runs 24/7 on Railway cloud (free tier)

## Setup on Railway

1. **Fork or upload these files to your GitHub repository**

2. **Go to Railway.app and login with GitHub**

3. **Create new project from GitHub repo**

4. **Add Environment Variables in Railway:**
   - `BOT_TOKEN`: Your Telegram bot token
   - `CHAT_ID`: Your Telegram chat ID

5. **Deploy and monitor logs**

## Files Required
- `main.py` - Main bot code
- `requirements.txt` - Python dependencies
- `Procfile` - Tells Railway how to run the bot
- `runtime.txt` - Specifies Python version

## Get Your Chat ID
1. Message your bot on Telegram
2. Visit: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
3. Find the `chat_id` number in the response

## Cost
FREE! Railway gives $5 monthly credit. This bot uses ~$1-2/month.
