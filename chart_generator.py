
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
import os

def generate_straddle_chart(candles, spot_price, call_strike, put_strike, filename="straddle_chart.png"):
    """
    Generates a chart showing recent price action and the straddle/strangle levels.
    candles: list of dicts [{'time': ts, 'close': price, ...}, ...]
    """
    try:
        # Extract data
        dates = [datetime.fromtimestamp(c['time']) for c in candles]
        closes = [float(c['close']) for c in candles]
        
        # Plot setup
        plt.figure(figsize=(10, 6))
        plt.style.use('dark_background')
        
        # Plot Price
        plt.plot(dates, closes, color='cyan', label='BTC Price', linewidth=1.5)
        
        # Plot Levels
        plt.axhline(y=spot_price, color='white', linestyle='--', linewidth=1, label=f'Spot: ${spot_price:,.0f}')
        
        if call_strike:
            plt.axhline(y=call_strike, color='red', linestyle='-', linewidth=1, label=f'Call Sell: ${call_strike:,.0f}')
            plt.fill_between(dates, call_strike, closes, where=[c > call_strike for c in closes], color='red', alpha=0.1)
            
        if put_strike:
            plt.axhline(y=put_strike, color='green', linestyle='-', linewidth=1, label=f'Put Sell: ${put_strike:,.0f}')
            plt.fill_between(dates, put_strike, closes, where=[c < put_strike for c in closes], color='green', alpha=0.1)
            
        # Highlight the "Safe Zone" (Between Strikes)
        if call_strike and put_strike:
            plt.axhspan(put_strike, call_strike, color='yellow', alpha=0.05, label='Profit Zone')

        # Formatting
        plt.title(f"BTC Price & Strategy Levels\nSpot: ${spot_price:,.0f} | HV: Calculated", fontsize=14, color='white')
        plt.xlabel("Date", color='gray')
        plt.ylabel("Price (USD)", color='gray')
        plt.legend(loc='upper left', fontsize=8)
        plt.grid(True, linestyle=':', alpha=0.3)
        
        plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%d %b'))
        plt.gcf().autofmt_xdate()
        
        # Save
        path = os.path.join(os.getcwd(), filename)
        plt.savefig(path, bbox_inches='tight', dpi=100)
        plt.close()
        
        return path
    except Exception as e:
        print(f"Error generating chart: {e}")
        return None
