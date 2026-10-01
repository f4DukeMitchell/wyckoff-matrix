import requests

# User must fill these in with valid credentials
TELEGRAM_BOT_TOKEN = ''
TELEGRAM_CHAT_ID = ''

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

def send_trade_alert(ticker, direction, entry_price, stop_loss, take_profit, regime, options_flow=None):
    """Formats and sends a trading alert."""
    if not is_configured():
        print("Telegram not configured. Skipping trade alert.")
        return False
        
    message = (
        f"TRADE ALERT: {ticker}\n"
        f"Direction: {direction}\n"
        f"Entry: {entry_price}\n"
        f"Stop Loss: {stop_loss}\n"
        f"Take Profit: {take_profit}\n"
        f"Regime Context: {regime}"
    )
    
    if options_flow:
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
