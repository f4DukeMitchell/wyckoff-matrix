import os
import time
import datetime
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Any

# Simple in-memory cache with 60s TTL
_CACHE: Dict[str, Any] = {}
_CACHE_TIME: Dict[str, float] = {}

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

def _fetch_rss(url: str, timeout: int = 5) -> List[Dict[str, Any]]:
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    headlines = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read()
            root = ET.fromstring(content)
            items = root.findall('.//item')
            for it in items[:15]:
                title_el = it.find('title')
                pub_el = it.find('pubDate')
                link_el = it.find('link')
                desc_el = it.find('description')

                title = (title_el.text or '').strip() if title_el is not None else ''
                pub = (pub_el.text or '').strip() if pub_el is not None else ''
                link = (link_el.text or '').strip() if link_el is not None else ''
                desc = (desc_el.text or '').strip() if desc_el is not None else ''

                if not title:
                    continue

                # Sentiment heuristic based on key financial keywords
                t_lower = title.lower()
                sentiment = "NEUTRAL"
                if any(w in t_lower for w in ['surge', 'jump', 'rally', 'gain', 'beat', 'record', 'bull', 'upgrade', 'profit', 'soar', 'high', 'breakout']):
                    sentiment = "BULLISH"
                elif any(w in t_lower for w in ['drop', 'fall', 'sink', 'slump', 'tumble', 'bear', 'downgrade', 'loss', 'miss', 'inflation', 'fear', 'warn', 'pressure']):
                    sentiment = "BEARISH"

                # Format time string cleanly
                time_str = pub
                try:
                    # Example: Thu, 15 Oct 2026 13:48:00 GMT
                    parts = pub.split(' ')
                    if len(parts) >= 5:
                        time_str = f"{parts[4][:5]} {parts[0].replace(',', '')}"
                except Exception:
                    pass

                headlines.append({
                    "title": title,
                    "published": pub,
                    "time_str": time_str,
                    "link": link,
                    "description": desc[:250] + ("..." if len(desc) > 250 else ""),
                    "sentiment": sentiment
                })
    except Exception as e:
        print(f"Error fetching RSS from {url}: {e}")
    return headlines

def get_market_wire() -> List[Dict[str, Any]]:
    """Fetches breaking macroeconomic and market news from CNBC and MarketWatch."""
    now = time.time()
    if 'market_wire' in _CACHE and (now - _CACHE_TIME.get('market_wire', 0)) < 90:
        return _CACHE['market_wire']

    sources = [
        ("CNBC Market", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
        ("MarketWatch", "http://feeds.marketwatch.com/marketwatch/topstories/")
    ]

    all_items = []
    seen_titles = set()
    for src_name, url in sources:
        items = _fetch_rss(url)
        for it in items:
            t_norm = "".join(ch for ch in it['title'].lower() if ch.isalnum())[:40]
            if t_norm in seen_titles:
                continue
            seen_titles.add(t_norm)
            it['source'] = src_name
            all_items.append(it)

    _CACHE['market_wire'] = all_items[:25]
    _CACHE_TIME['market_wire'] = now
    return _CACHE['market_wire']

def get_ticker_news(ticker: str) -> List[Dict[str, Any]]:
    """Fetches real-time company news for a specific stock via Yahoo Finance RSS."""
    ticker = ticker.upper().strip()
    if not ticker:
        return []

    cache_key = f"ticker_{ticker}"
    now = time.time()
    if cache_key in _CACHE and (now - _CACHE_TIME.get(cache_key, 0)) < 120:
        return _CACHE[cache_key]

    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}"
    items = _fetch_rss(url)
    for it in items:
        it['source'] = 'Yahoo Finance'
        it['ticker'] = ticker

    _CACHE[cache_key] = items[:12]
    _CACHE_TIME[cache_key] = now
    return _CACHE[cache_key]

def get_today_actions() -> Dict[str, Any]:
    """Retrieves today's triggered Wyckoff setups, trade executions, and P&L ledger."""
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wyckoff_trades.db")
    today_alerts = []
    closed_today = []
    active_trades = []
    
    if os.path.exists(db_path):
        import sqlite3
        try:
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # 1. Today's alerts (strictly today's calendar date)
            c.execute("""
                SELECT id, ticker, direction, entry_price, stop_loss, take_profit, timeframe, 
                       regime, timestamp, outcome, user_active, pnl_r, ml_confidence
                FROM alerts
                WHERE date(timestamp) = date('now')
                   OR timestamp >= date('now', 'start of day')
                ORDER BY id DESC LIMIT 20
            """)
            rows = c.fetchall()
            for r in rows:
                d = dict(r)
                today_alerts.append(d)
                if d.get('outcome') in ['WIN', 'LOSS', 'BREAKEVEN', 'CLOSED']:
                    closed_today.append(d)
                elif d.get('outcome') == 'OPEN':
                    active_trades.append(d)
            conn.close()
        except Exception as e:
            print(f"Error querying today actions: {e}")

    # Top Hot Radar Movers
    hot_setups = []
    try:
        from priority_queue import get_priority_queue
        q = get_priority_queue()
        for item in q[:8]:
            if item.get('status') in ['HOT_SPRING', 'HOT_UTAD', 'TEST_SUPPORT', 'TEST_RESIST']:
                hot_setups.append(item)
    except Exception:
        pass

    return {
        "today_alerts_count": len(today_alerts),
        "recent_alerts": today_alerts[:10],
        "closed_today": closed_today,
        "active_trades": active_trades,
        "hot_setups": hot_setups
    }

def get_market_pulse(chart_tickers: List[str] = None) -> Dict[str, Any]:
    """Assembles complete market pulse payload for dedicated page & chart wire."""
    chart_tickers = chart_tickers or ['SPY', 'QQQ', 'NVDA', 'TSLA']

    # 1. Major index quotes
    index_syms = ['SPY', 'QQQ', 'IWM', 'DIA']
    quotes = {}
    try:
        from public_executor import get_live_prices
        quotes = get_live_prices(index_syms + [t.upper() for t in chart_tickers])
    except Exception:
        pass

    # 2. Breaking Market Wire
    wire = get_market_wire()

    # 3. Chart-specific headlines
    chart_news = {}
    for sym in chart_tickers:
        sym_clean = sym.upper().strip()
        if sym_clean:
            chart_news[sym_clean] = get_ticker_news(sym_clean)

    # 4. Today's Actions & Bot ledger
    actions = get_today_actions()

    return {
        "status": "SUCCESS",
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S EST"),
        "indices": {
            "SPY": quotes.get("SPY", 0.0),
            "QQQ": quotes.get("QQQ", 0.0),
            "IWM": quotes.get("IWM", 0.0),
            "DIA": quotes.get("DIA", 0.0)
        },
        "market_wire": wire,
        "chart_news": chart_news,
        "today_actions": actions
    }
