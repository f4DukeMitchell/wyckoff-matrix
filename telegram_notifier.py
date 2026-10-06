import requests
import datetime

# User must fill these in with valid credentials
TELEGRAM_BOT_TOKEN = '8870935798:AAFx5-TdD0qEwQ4nTSRIFw4RvdI87GSXnDc'
TELEGRAM_CHAT_ID = '8610265859'

# Optional: Set these to route different trade types to different groups
TELEGRAM_CHAT_ID_DAY = '-5547865201'    # WYCKOFF Day Trades (For 5m, 15m)
TELEGRAM_CHAT_ID_SWING = None   # For 1h
TELEGRAM_CHAT_ID_LONG = None    # For 1d

def is_configured():
    """Returns True if both token and chat_id are configured."""
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

def send_message(text, reply_markup=None, chat_id=None):
    """Sends a text message to the configured Telegram chat."""
    if not is_configured():
        print("Telegram not configured. Skipping message send.")
        return False

    target_chat = chat_id if chat_id else TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": text
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        print("Telegram message sent successfully.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Failed to send Telegram message: {e}")
        return False

def send_trade_alert(ticker, direction, entry_price, stop_loss, take_profit, regime, timeframe="5m", options_flow=None, trade_id=None, ml_confidence=None):
    """Formats and sends a trading alert."""
    if not is_configured():
        print("Telegram not configured. Skipping trade alert.")
        return False
        
    styles = {
        "5m": "Day Trade Scalp",
        "15m": "Day Trade / Short Swing",
        "1h": "Swing Trade",
        "1d": "Long Term"
    }
    
    expected_times = {
        "5m": "1 - 4 Hours",
        "15m": "1 - 3 Days",
        "1h": "1 - 2 Weeks",
        "1d": "1 - 3 Months"
    }
    
    trade_style = styles.get(timeframe, "Unknown")
    exp_time = expected_times.get(timeframe, "Unknown")
    
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
        
    now_str = datetime.datetime.now().strftime('%b %d, %I:%M %p')
    ml_str = f"\n🤖 ML Win Confidence: {ml_confidence:.1f}% (Random Forest)" if ml_confidence is not None else ""
    message = (
        f"🚨 TRADE ALERT: {ticker}\n"
        f"Direction: {direction}\n"
        f"Style: {trade_style} ({timeframe})\n"
        f"Initiated: {now_str}\n"
        f"Expected Duration: {exp_time}\n"
        f"Entry: {entry_str}\n"
        f"Stop Loss: {sl_str}\n"
        f"Take Profit: {tp_str}\n"
        f"Expected Return: {r_units}R Units\n"
        f"Regime Context: {regime}"
        f"{ml_str}\n\n"
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
        
    reply_markup = None
    if trade_id:
        from public_executor import calculate_test_allocation
        alloc_1pct = calculate_test_allocation(0.01)
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": f"🚀 BUY 1% (${alloc_1pct:.2f}) on Public", "callback_data": f"buy_1pct_{trade_id}_{ticker}"},
                    {"text": "Track Only 🟢", "callback_data": f"in_trade_{trade_id}"}
                ]
            ]
        }
        
    target_chat = None
    if timeframe in ["5m", "15m"] and TELEGRAM_CHAT_ID_DAY: target_chat = TELEGRAM_CHAT_ID_DAY
    elif timeframe == "1h" and TELEGRAM_CHAT_ID_SWING: target_chat = TELEGRAM_CHAT_ID_SWING
    elif timeframe == "1d" and TELEGRAM_CHAT_ID_LONG: target_chat = TELEGRAM_CHAT_ID_LONG
        
    return send_message(message, reply_markup=reply_markup, chat_id=target_chat)

def send_daily_recap(recap_text):
    """Sends the daily recap text via Telegram."""
    if not is_configured():
        print("Telegram not configured. Skipping daily recap.")
        return False
        
    message = f"DAILY RECAP\n{recap_text}"
    return send_message(message)

def send_market_radar(radar_data, session_name="Market Radar"):
    """Sends formatted market radar updates."""
    if not is_configured():
        print("Telegram not configured. Skipping market radar.")
        return False
        
    if isinstance(radar_data, str):
        message = f"📡 WYCKOFF MARKET RADAR: {session_name}\n\n{radar_data}"
    else:
        message = f"📡 WYCKOFF MARKET RADAR: {session_name}\n"
        message += "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        if not radar_data:
            message += "No stocks currently showing deep regime exhaustion (>= 20 bars).\nMarket is balanced or in trend-following mode.\n"
        else:
            message += "Top Coiled Setups Stalking for Reversal:\n\n"
            for t in radar_data:
                # t is (ticker, regime, bars, target)
                emoji = "🟢" if "SPRING" in str(t[3]) else "🔴"
                message += f"{emoji} <b>{t[0]}</b>: {t[1]} Regime ({t[2]} bars deep)\n   🎯 Stalking: <code>{t[3]}</code>\n\n"
        message += "⚡ <i>Wait for Micro (1,1) Supertrend confirmation before entry!</i>"
    return send_message(message)

_last_update_id = None
def check_callbacks():
    """Polls Telegram for button clicks and executes orders or updates the DB."""
    global _last_update_id
    if not is_configured(): return
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    params = {"timeout": 5, "allowed_updates": ["callback_query", "message"]}
    if _last_update_id:
        params["offset"] = _last_update_id + 1
        
    try:
        res = requests.get(url, params=params).json()
        if not res.get("ok"): return
        
        updates = res.get("result", [])
        for u in updates:
            _last_update_id = u["update_id"]
            
            # Print group chat IDs for user setup
            if "message" in u:
                chat = u["message"].get("chat", {})
                if chat.get("type") in ["group", "supergroup", "channel"]:
                    print(f"\n[TELEGRAM SETUP] Detected new group: '{chat.get('title')}' -> Chat ID: {chat.get('id')}\n")
            
            if "callback_query" in u:
                cq = u["callback_query"]
                data = cq.get("data", "")
                cq_id = cq.get("id")
                from_chat_id = cq.get("message", {}).get("chat", {}).get("id")
                
                if data.startswith("buy_1pct_"):
                    # Live Broker Execution via Public.com
                    parts = data.split("_")
                    trade_id = int(parts[2])
                    ticker = parts[3].upper()
                    
                    ans_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
                    requests.post(ans_url, json={"callback_query_id": cq_id, "text": f"Submitting 1% BUY for {ticker}..."})
                    
                    try:
                        from public_executor import calculate_test_allocation, execute_dollar_buy
                        alloc = calculate_test_allocation(0.01)
                        order_res = execute_dollar_buy(ticker, alloc)
                        
                        # Mark trade active in database
                        import sqlite3
                        conn = sqlite3.connect("wyckoff_trades.db")
                        c = conn.cursor()
                        c.execute("UPDATE alerts SET user_active = 1 WHERE id = ?", (trade_id,))
                        conn.commit()
                        conn.close()
                        
                        exec_msg = (
                            f"✅ PUBLIC.COM ORDER EXECUTED!\n"
                            f"Symbol: {ticker}\n"
                            f"Allocation: 1% (${alloc:.2f})\n"
                            f"Status: {order_res.get('status')}\n"
                            f"Order UUID: {order_res.get('order_id')}\n\n"
                            f"🛡️ Stop Loss & Breakeven Ratchet (+0.75R) are now active in the Wyckoff engine."
                        )
                        send_message(exec_msg, chat_id=from_chat_id)
                        print(f"Executed 1% test BUY for {ticker} (${alloc:.2f}) on Public.com.")
                    except Exception as e:
                        err_msg = f"❌ Order Execution Failed for {ticker}: {str(e)}"
                        send_message(err_msg, chat_id=from_chat_id)
                        print(f"Order error: {e}")
                        
                elif data.startswith("in_trade_"):
                    ans_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
                    requests.post(ans_url, json={"callback_query_id": cq_id, "text": "Trade marked as ACTIVE! 🟢"})
                    try:
                        trade_id = int(data.split("_")[2])
                        import sqlite3
                        conn = sqlite3.connect("wyckoff_trades.db")
                        c = conn.cursor()
                        c.execute("UPDATE alerts SET user_active = 1 WHERE id = ?", (trade_id,))
                        conn.commit()
                        conn.close()
                        print(f"User marked trade {trade_id} as ACTIVE.")
                    except Exception as e:
                        print(f"Error updating DB for callback: {e}")
    except Exception as e:
        pass

if __name__ == '__main__':
    if is_configured():
        print("Testing Telegram Notifier...")
        send_message("Test message from Telegram Notifier module.")
        send_trade_alert("BTC/USD", "LONG", 65000, 60000, 75000, "Markup")
    else:
        print("Please configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to run tests.")
