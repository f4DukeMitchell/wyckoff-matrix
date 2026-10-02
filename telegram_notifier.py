import requests

# User must fill these in with valid credentials
TELEGRAM_BOT_TOKEN = '8870935798:AAFx5-TdD0qEwQ4nTSRIFw4RvdI87GSXnDc'
TELEGRAM_CHAT_ID = '8610265859'

def is_configured():
    """Returns True if both token and chat_id are configured."""
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

def send_message(text):
    """Sends a text message to the configured Telegram chat."""
    if not is_configured():
        print("Telegram not configured. Skipping message send.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print("Telegram message sent successfully.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Failed to send Telegram message: {e}")
        return False

def send_trade_alert(ticker, direction, entry_price, stop_loss, take_profit, regime, timeframe="5m", options_flow=None):
    """Formats and sends a trading alert."""
    if not is_configured():
        print("Telegram not configured. Skipping trade alert.")
        return False
        
    styles = {
        "5m": "Day Trade Scalp (Intraday)",
        "15m": "Day Trade / Short Swing (1-2 days)",
        "1h": "Swing Trade (Days to Weeks)",
        "1d": "Long Term (Multiple Weeks)"
    }
    trade_style = styles.get(timeframe, "Unknown")
    
    try:
        risk = abs(float(entry_price) - float(stop_loss))
        reward = abs(float(take_profit) - float(entry_price))
        r_units = round(reward / risk, 2) if risk > 0 else 0.0
        entry_str = f"${float(entry_price):.2f}"
        sl_str = f"${float(stop_loss):.2f}"
        tp_str = f"${float(take_profit):.2f}"
    except:
        r_units = "N/A"
        entry_str = str(entry_price)
        sl_str = str(stop_loss)
        tp_str = str(take_profit)
        
    message = (
        f"🚨 TRADE ALERT: {ticker}\n"
        f"Direction: {direction}\n"
        f"Timeframe: {timeframe} - {trade_style}\n"
        f"Entry: {entry_str}\n"
        f"Stop Loss: {sl_str}\n"
        f"Take Profit: {tp_str}\n"
        f"Expected Return: {r_units}R Units\n"
        f"Regime Context: {regime}\n\n"
        f"🔗 Trade on Public: https://public.com/stocks/{ticker.lower()}"
    )
    
    if options_flow and options_flow.get('total_call_oi', 0) > 0:
        message += (
            f"\n\nOPTIONS FLOW INTEL:\n"
            f"Sentiment: {options_flow.get('net_sentiment', 'N/A')}\n"
            f"Put/Call Ratio: {options_flow.get('put_call_ratio', 'N/A')} ({options_flow.get('put_call_label', 'N/A')})\n"
            f"Gamma Wall (Magnet): ${options_flow.get('gamma_wall', 'N/A')}\n"
            f"Max Pain: ${options_flow.get('max_pain', 'N/A')}"
        )
        
    return send_message(message)

def send_daily_recap(recap_text):
    """Sends the daily recap text via Telegram."""
    if not is_configured():
        print("Telegram not configured. Skipping daily recap.")
        return False
        
    message = f"DAILY RECAP\n{recap_text}"
    return send_message(message)

def send_market_radar(radar_text):
    """Sends market radar updates."""
    if not is_configured():
        print("Telegram not configured. Skipping market radar.")
        return False
        
    message = f"MARKET RADAR\n{radar_text}"
    return send_message(message)

if __name__ == '__main__':
    if is_configured():
        print("Testing Telegram Notifier...")
        send_message("Test message from Telegram Notifier module.")
        send_trade_alert("BTC/USD", "LONG", 65000, 60000, 75000, "Markup")
    else:
        print("Please configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to run tests.")
