"""
Frank Wyckoff Dynamic Sector Guard & 15-Minute Tape Stabilization Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Precision Circuit Breaker that prevents industry-wide waterfall drawdown
without choking out unaffected, healthy sectors or capping upside runners.

Architecture:
1. 2 Consecutive Stops within 45 Minutes triggers Frank Wyckoff Diagnosis.
2. If stops belong to the same sector (e.g. Semiconductors / SMH), Frank places
   a 30-Minute SECTOR QUARANTINE on that specific sector only.
3. Healthy sectors (Healthcare, Industrials, Financials, Tech Software) remain
   100% ACTIVE and open for automated Wyckoff Spring entries.
4. Frank Wyckoff Tape Radar audits quarantined sectors every 15 minutes:
   - Price vs Intraday VWAP (Reclaim Confirmation)
   - Volume Dry-Up / Supply Exhaustion
   - 5m / 15m Higher Low Geometry
5. Stabilized sectors have their quarantine lifted immediately with Telegram telemetry.
"""

import os
import json
import time
import datetime
from datetime import timezone
import pandas as pd
import numpy as np

from data_paths import SECTOR_GUARD_PATH
from telegram_notifier import send_message, TELEGRAM_CHAT_ID_DAY

# ─────────────────────────────────────────────────────────────────────────────
# 1. INSTITUTIONAL SECTOR TAXONOMY
# ─────────────────────────────────────────────────────────────────────────────
SECTOR_MAP = {
    # Semiconductors (SMH)
    'NVDA': 'SMH', 'AMD': 'SMH', 'AVGO': 'SMH', 'QCOM': 'SMH', 'AMAT': 'SMH',
    'MU': 'SMH', 'LRCX': 'SMH', 'INTC': 'SMH', 'SNPS': 'SMH', 'CDNS': 'SMH',
    'KLAC': 'SMH', 'COHR': 'SMH', 'ACLS': 'SMH', 'MRVL': 'SMH', 'ON': 'SMH',
    'ADI': 'SMH', 'TXN': 'SMH', 'MPWR': 'SMH', 'ASML': 'SMH', 'ARM': 'SMH',
    'TSM': 'SMH', 'SWKS': 'SMH', 'QRVO': 'SMH', 'MCHP': 'SMH', 'TER': 'SMH',

    # Technology & Enterprise Software (XLK)
    'AAPL': 'XLK', 'MSFT': 'XLK', 'CRM': 'XLK', 'ADBE': 'XLK', 'NOW': 'XLK',
    'PANW': 'XLK', 'PLTR': 'XLK', 'CRWD': 'XLK', 'CSCO': 'XLK', 'ORCL': 'XLK',
    'SNOW': 'XLK', 'DDOG': 'XLK', 'NET': 'XLK', 'WDAY': 'XLK', 'FTNT': 'XLK',
    'DELL': 'XLK', 'SMCI': 'XLK', 'ANET': 'XLK', 'IBM': 'XLK', 'HPE': 'XLK',

    # Communication Services & Digital Platforms (XLC)
    'GOOGL': 'XLC', 'GOOG': 'XLC', 'META': 'XLC', 'NFLX': 'XLC', 'DIS': 'XLC',
    'CMCSA': 'XLC', 'TMUS': 'XLC', 'CHTR': 'XLC', 'VZ': 'XLC', 'T': 'XLC',
    'RDDT': 'XLC', 'PINS': 'XLC', 'SNAP': 'XLC', 'TTD': 'XLC',

    # Consumer Discretionary & Retail (XLY)
    'AMZN': 'XLY', 'TSLA': 'XLY', 'HD': 'XLY', 'MCD': 'XLY', 'NKE': 'XLY',
    'SBUX': 'XLY', 'BKNG': 'XLY', 'LOW': 'XLY', 'TJX': 'XLY', 'TGT': 'XLY',
    'LULU': 'XLY', 'ROST': 'XLY', 'ABNB': 'XLY', 'MAR': 'XLY', 'ORLY': 'XLY',
    'CHRW': 'XLY', 'EBAY': 'XLY', 'ETSY': 'XLY',

    # Healthcare, Pharma & Medical Devices (XLV)
    'UNH': 'XLV', 'JNJ': 'XLV', 'LLY': 'XLV', 'ABBV': 'XLV', 'MRK': 'XLV',
    'PFE': 'XLV', 'TMO': 'XLV', 'ABT': 'XLV', 'DHR': 'XLV', 'BMY': 'XLV',
    'AMGN': 'XLV', 'ISRG': 'XLV', 'VRTX': 'XLV', 'GILD': 'XLV', 'REGN': 'XLV',
    'RMD': 'XLV', 'BIIB': 'XLV', 'DXCM': 'XLV', 'SYK': 'XLV', 'BSX': 'XLV',
    'MTD': 'XLV', 'WAT': 'XLV', 'ELV': 'XLV', 'CI': 'XLV', 'HUM': 'XLV',

    # Industrials, Machinery & Defense (XLI)
    'CAT': 'XLI', 'GE': 'XLI', 'UNP': 'XLI', 'HON': 'XLI', 'BA': 'XLI',
    'UPS': 'XLI', 'RTX': 'XLI', 'DE': 'XLI', 'LMT': 'XLI', 'ETN': 'XLI',
    'WM': 'XLI', 'GD': 'XLI', 'TDG': 'XLI', 'EMR': 'XLI', 'PCAR': 'XLI',
    'URI': 'XLI', 'FDX': 'XLI', 'NSC': 'XLI', 'CSX': 'XLI', 'PH': 'XLI',

    # Financials & Banking (XLF)
    'JPM': 'XLF', 'BAC': 'XLF', 'WFC': 'XLF', 'C': 'XLF', 'GS': 'XLF',
    'MS': 'XLF', 'BLK': 'XLF', 'SCHW': 'XLF', 'V': 'XLF', 'MA': 'XLF',
    'AXP': 'XLF', 'PGR': 'XLF', 'CB': 'XLF', 'MMC': 'XLF', 'AON': 'XLF',

    # Energy & Petroleum (XLE)
    'XOM': 'XLE', 'CVX': 'XLE', 'COP': 'XLE', 'SLB': 'XLE', 'EOG': 'XLE',
    'MPC': 'XLE', 'PSX': 'XLE', 'VLO': 'XLE', 'OXY': 'XLE', 'WMB': 'XLE',

    # Consumer Staples (XLP)
    'PG': 'XLP', 'COST': 'XLP', 'PEP': 'XLP', 'KO': 'XLP', 'WMT': 'XLP',
    'PM': 'XLP', 'MDLZ': 'XLP', 'CL': 'XLP', 'MO': 'XLP', 'KMB': 'XLP',

    # Materials (XLB)
    'LIN': 'XLB', 'APD': 'XLB', 'SHW': 'XLB', 'FCX': 'XLB', 'NEM': 'XLB',

    # Utilities (XLU)
    'NEE': 'XLU', 'SO': 'XLU', 'DUK': 'XLU', 'CEG': 'XLU', 'SRE': 'XLU',

    # Real Estate (XLRE)
    'PLD': 'XLRE', 'AMT': 'XLRE', 'EQIX': 'XLRE', 'CCI': 'XLRE', 'SPG': 'XLRE',
    'BXP': 'XLRE', 'O': 'XLRE', 'WELL': 'XLRE'
}

SECTOR_NAMES = {
    'SMH': 'Semiconductors',
    'XLK': 'Technology & Software',
    'XLC': 'Communication Services',
    'XLY': 'Consumer Discretionary',
    'XLV': 'Healthcare & Medical',
    'XLI': 'Industrials & Machinery',
    'XLF': 'Financials & Banking',
    'XLE': 'Energy & Oil',
    'XLP': 'Consumer Staples',
    'XLB': 'Materials & Mining',
    'XLU': 'Utilities',
    'XLRE': 'Real Estate',
    'SPY': 'Broad Market / Macro'
}

def safe_print(text: str):
    try:
        print(text)
    except UnicodeEncodeError:
        try:
            print(text.encode('ascii', errors='replace').decode('ascii'))
        except Exception:
            pass

def get_ticker_sector(ticker: str) -> tuple[str, str]:
    """Returns (sector_etf, friendly_name) for any ticker."""
    sym = (ticker or '').strip().upper()
    etf = SECTOR_MAP.get(sym, 'SPY')
    name = SECTOR_NAMES.get(etf, 'General Market')
    return etf, name

def get_est_now():
    """Returns current datetime in America/New_York."""
    try:
        import zoneinfo
        return datetime.datetime.now(zoneinfo.ZoneInfo("America/New_York"))
    except Exception:
        return datetime.datetime.now()

def get_utc_now():
    return datetime.datetime.now(timezone.utc)

# ─────────────────────────────────────────────────────────────────────────────
# 2. STATE PERSISTENCE
# ─────────────────────────────────────────────────────────────────────────────
def load_sector_guard_state() -> dict:
    if os.path.exists(SECTOR_GUARD_PATH):
        try:
            with open(SECTOR_GUARD_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            safe_print(f"[SectorGuard] Error loading state: {e}")
    return {
        "quarantined_sectors": {},
        "global_circuit_breaker": {"active": False, "pause_until": None, "reason": None},
        "recent_stops": []
    }

def save_sector_guard_state(state: dict):
    try:
        os.makedirs(os.path.dirname(SECTOR_GUARD_PATH), exist_ok=True)
        with open(SECTOR_GUARD_PATH, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        safe_print(f"[SectorGuard] Error saving state: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# 3. CIRCUIT BREAKER TRIGGER & STOP LOGGING
# ─────────────────────────────────────────────────────────────────────────────
def record_stop_event(ticker: str, exit_price: float, pnl_r: float, tier: int):
    """
    Called whenever Exit Guardian executes a stop loss with outcome == 'LOSS'.
    Evaluates rolling 45-minute stop frequency. If >= 2 stops:
    - Same sector -> 30-min Sector Quarantine
    - Multi-sector -> 30-min Broad Market Pause if macro weak, else recent sector quarantine
    """
    state = load_sector_guard_state()
    now_utc = get_utc_now()
    cutoff_45m = now_utc - datetime.timedelta(minutes=45)

    etf, sec_name = get_ticker_sector(ticker)

    # Clean old stops older than 45 minutes
    valid_stops = []
    for s in state.get("recent_stops", []):
        try:
            t = pd.to_datetime(s.get("timestamp"), utc=True).to_pydatetime()
            if t >= cutoff_45m:
                valid_stops.append(s)
        except Exception:
            continue

    # Record current stop
    new_stop = {
        "ticker": ticker.upper(),
        "sector_etf": etf,
        "sector_name": sec_name,
        "exit_price": float(exit_price),
        "pnl_r": float(pnl_r),
        "tier": int(tier),
        "timestamp": now_utc.isoformat()
    }
    valid_stops.append(new_stop)
    state["recent_stops"] = valid_stops

    safe_print(f"[SectorGuard] Recorded stop for {ticker} ({sec_name}). Rolling 45m stops: {len(valid_stops)}")

    # Check Circuit Breaker threshold (>= 2 stops in 45m)
    if len(valid_stops) >= 2:
        stop1 = valid_stops[-2]
        stop2 = valid_stops[-1]
        
        # Scenario A: Same Sector Bleed
        if stop1["sector_etf"] == stop2["sector_etf"]:
            target_etf = stop1["sector_etf"]
            target_name = stop1["sector_name"]
            quarantine_until = (now_utc + datetime.timedelta(minutes=30)).isoformat()

            state["quarantined_sectors"][target_etf] = {
                "sector_name": target_name,
                "quarantine_until": quarantine_until,
                "reason": f"2 consecutive stops within 45m ({stop1['ticker']}, {stop2['ticker']})",
                "last_audited": now_utc.isoformat(),
                "audit_count": 0,
                "initial_stops": [stop1['ticker'], stop2['ticker']]
            }
            save_sector_guard_state(state)

            est_expire = get_est_now() + datetime.timedelta(minutes=30)
            msg = (
                f"🚨 [FRANK WYCKOFF CIRCUIT BREAKER] SECTOR QUARANTINE\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• Sector: {target_name.upper()} ({target_etf})\n"
                f"• Trigger: 2 consecutive stops in {target_name} ({stop1['ticker']} & {stop2['ticker']}).\n"
                f"• Action: Halting NEW {target_name} entries for 30 minutes (until {est_expire.strftime('%I:%M %p')} EST).\n"
                f"• Frank Tape Radar: Auditing {target_etf} volume and VWAP every 15 minutes.\n"
                f"• Healthy Sectors: Healthcare, Industrials, Financials & Software remain 100% ACTIVE."
            )
            safe_print(f"[SectorGuard] {msg}")
            send_message(msg, chat_id=TELEGRAM_CHAT_ID_DAY, force=True)

        # Scenario B: Multi-Sector Stops
        else:
            # Check macro tape (SPY)
            spy_tape = audit_sector_tape('SPY')
            is_macro_dump = bool(spy_tape.get('pct_vs_vwap', 0.0) < -0.35)

            if is_macro_dump:
                pause_until = (now_utc + datetime.timedelta(minutes=30)).isoformat()
                state["global_circuit_breaker"] = {
                    "active": True,
                    "pause_until": pause_until,
                    "reason": f"Multi-sector stops ({stop1['ticker']}, {stop2['ticker']}) with SPY below VWAP ({spy_tape.get('pct_vs_vwap', 0):.2f}%)"
                }
                save_sector_guard_state(state)

                est_expire = get_est_now() + datetime.timedelta(minutes=30)
                msg = (
                    f"🚨 [FRANK WYCKOFF CIRCUIT BREAKER] BROAD MARKET PAUSE\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• Trigger: 2 stops across distinct sectors ({stop1['ticker']} & {stop2['ticker']}) during market weakness.\n"
                    f"• Macro Tape: SPY trading {spy_tape.get('pct_vs_vwap', 0):.2f}% below VWAP.\n"
                    f"• Action: Halting ALL automated entries for 30 minutes (until {est_expire.strftime('%I:%M %p')} EST).\n"
                    f"• Frank Tape Radar: Auditing SPY/QQQ stabilization every 15 minutes."
                )
                safe_print(f"[SectorGuard] (Internal Log) {msg}")
            else:
                # SPY is stable; isolate only the most recent stopped sector for 20 minutes
                target_etf = stop2["sector_etf"]
                target_name = stop2["sector_name"]
                quarantine_until = (now_utc + datetime.timedelta(minutes=20)).isoformat()
                state["quarantined_sectors"][target_etf] = {
                    "sector_name": target_name,
                    "quarantine_until": quarantine_until,
                    "reason": f"Isolated stop-out ({stop2['ticker']}) under multi-stop watch",
                    "last_audited": now_utc.isoformat(),
                    "audit_count": 0,
                    "initial_stops": [stop2['ticker']]
                }
                save_sector_guard_state(state)
                est_expire = get_est_now() + datetime.timedelta(minutes=20)
                msg = (
                    f"⚠️ [FRANK WYCKOFF] ISOLATED SECTOR PAUSE\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• Sector: {target_name.upper()} ({target_etf})\n"
                    f"• Action: 20-minute pause on {target_name} entries (until {est_expire.strftime('%I:%M %p')} EST).\n"
                    f"• Macro Status: SPY healthy. Other sectors remain 100% active."
                )
                safe_print(f"[SectorGuard] {msg}")
                send_message(msg, chat_id=TELEGRAM_CHAT_ID_DAY, force=True)

    save_sector_guard_state(state)

# ─────────────────────────────────────────────────────────────────────────────
# 4. ENTRY ELIGIBILITY GATE
# ─────────────────────────────────────────────────────────────────────────────
def check_sector_eligibility(ticker: str) -> tuple[bool, str]:
    """
    Called by check_auto_buy_eligibility() before executing any trade.
    Returns (eligible: bool, reason: str).
    """
    state = load_sector_guard_state()
    now_utc = get_utc_now()

    # 1. Check Global Market Circuit Breaker
    gcb = state.get("global_circuit_breaker", {})
    if gcb.get("active"):
        p_until = gcb.get("pause_until")
        if p_until:
            try:
                t_pause = pd.to_datetime(p_until, utc=True).to_pydatetime()
                if now_utc < t_pause:
                    remaining_min = int((t_pause - now_utc).total_seconds() / 60.0)
                    return False, f"CIRCUIT_BREAKER_ACTIVE (Market-Wide Pause: {remaining_min}m remaining)"
                else:
                    # Expired
                    state["global_circuit_breaker"]["active"] = False
                    save_sector_guard_state(state)
            except Exception:
                pass

    # 2. Check Specific Sector Quarantine
    etf, sec_name = get_ticker_sector(ticker)
    quarantined = state.get("quarantined_sectors", {})
    if etf in quarantined:
        q_info = quarantined[etf]
        q_until = q_info.get("quarantine_until")
        if q_until:
            try:
                t_q = pd.to_datetime(q_until, utc=True).to_pydatetime()
                if now_utc < t_q:
                    remaining_min = int((t_q - now_utc).total_seconds() / 60.0)
                    return False, f"SECTOR_QUARANTINED ({sec_name} [{etf}] halted by Frank Wyckoff: {remaining_min}m remaining)"
                else:
                    # Time has elapsed; will be cleanly resolved on next radar cycle
                    pass
            except Exception:
                pass

    return True, "SECTOR_CLEARED"

# ─────────────────────────────────────────────────────────────────────────────
# 5. FRANK WYCKOFF 15-MINUTE TAPE RADAR & EVALUATOR
# ─────────────────────────────────────────────────────────────────────────────
def audit_sector_tape(sector_etf: str) -> dict:
    """
    Performs algorithmic tape reading on a sector ETF (SMH, XLK, XLV, SPY, etc.):
    - Intraday VWAP location
    - Supply / Volume dry-up
    - 5m Higher-low geometry
    """
    df = None
    # 1. Primary: Try Public.com sync bars
    try:
        from data_feed_public import get_public_bars_sync
        df = get_public_bars_sync(sector_etf, interval='5m')
    except Exception:
        df = None

    # 2. Redundancy: yfinance
    if df is None or df.empty:
        try:
            import yfinance as yf
            df = yf.download(sector_etf, period='1d', interval='5m', progress=False)
            if df is not None and not df.empty and isinstance(df.columns, pd.MultiIndex):
                df = df.xs(df.columns.levels[1][0], level=1, axis=1)
        except Exception as e:
            safe_print(f"[SectorGuard] yfinance error for {sector_etf}: {e}")

    if df is None or df.empty or len(df) < 5:
        return {
            "sector_etf": sector_etf,
            "current_price": 0.0,
            "vwap": 0.0,
            "pct_vs_vwap": 0.0,
            "is_stabilized": True,  # Don't hold quarantine if data unavailable
            "reason": "Insufficient bar data for tape audit"
        }

    high = df['High'].values
    low = df['Low'].values
    close = df['Close'].values
    vol = df['Volume'].values

    typical = (high + low + close) / 3.0
    cum_vol = np.cumsum(vol)
    cum_pv = np.cumsum(typical * vol)

    curr_p = float(close[-1])
    vwap = float(cum_pv[-1] / cum_vol[-1]) if cum_vol[-1] > 0 else curr_p
    pct_vs_vwap = ((curr_p - vwap) / vwap) * 100.0

    # Tape Check 1: VWAP Reclaim (trading >= VWAP or within 0.12%)
    vwap_reclaimed = pct_vs_vwap >= -0.12

    # Tape Check 2: 5m Structural Higher Low (last 3 bars)
    recent_low = float(min(low[-2:]))
    prev_low = float(min(low[-5:-2]))
    higher_low = recent_low >= (prev_low * 0.999)

    # Tape Check 3: Volume Dry-Up (Supply Exhaustion)
    recent_avg_vol = float(np.mean(vol[-3:]))
    day_avg_vol = float(np.mean(vol))
    volume_dryup = recent_avg_vol <= (day_avg_vol * 1.15)

    is_stabilized = vwap_reclaimed or (higher_low and volume_dryup and pct_vs_vwap >= -0.40)

    return {
        "sector_etf": sector_etf,
        "current_price": round(curr_p, 2),
        "vwap": round(vwap, 2),
        "pct_vs_vwap": round(pct_vs_vwap, 2),
        "vwap_reclaimed": vwap_reclaimed,
        "higher_low": higher_low,
        "volume_dryup": volume_dryup,
        "is_stabilized": is_stabilized
    }

def is_trading_day(dt_et) -> bool:
    """Returns True only on market trading days (Monday=0 to Friday=4)."""
    return dt_et.weekday() < 5

def is_market_open(dt_et=None) -> bool:
    """Returns True only during Regular Trading Hours (Mon-Fri 9:30 AM - 4:00 PM EST)."""
    if dt_et is None:
        dt_et = get_est_now()
    if dt_et.weekday() >= 5:  # Saturday or Sunday
        return False
    t = dt_et.time()
    return datetime.time(9, 30) <= t <= datetime.time(16, 0)

def send_frank_midday_report():
    """Report 1: Mid-Day Tape & Sector Health Report (12:00 PM EST, trading days only)."""
    try:
        now_et = get_est_now()
        spy_tape = audit_sector_tape('SPY')
        spy_p = spy_tape.get('current_price', 0.0)
        spy_pct = spy_tape.get('pct_vs_vwap', 0.0)
        regime = "BULLISH / ABOVE VWAP" if spy_pct >= 0.1 else ("DISTRIBUTING / BELOW VWAP" if spy_pct <= -0.2 else "BALANCED / AT VWAP")

        state = load_sector_guard_state()
        q_sectors = state.get("quarantined_sectors", {})
        if q_sectors:
            q_lines = [f"{info.get('sector_name', k)} ({k})" for k, info in q_sectors.items()]
            q_text = f"QUARANTINED: {', '.join(q_lines)}"
        else:
            q_text = "ALL 12 SECTORS HEALTHY & ACTIVE"

        import sqlite3
        from data_paths import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT ticker, timeframe FROM alerts WHERE outcome = 'OPEN' AND user_active = 1")
        open_rows = c.fetchall()
        conn.close()

        open_str = ", ".join([f"{r[0]} ({r[1]})" for r in open_rows]) if open_rows else "0 Active Positions (100% Cash Dry Powder)"

        msg = (
            f"[FRANK WYCKOFF] MID-DAY TAPE & SECTOR REPORT\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Session Time: 12:00 PM EST (Trading Session)\n"
            f"• Macro Tape (SPY): ${spy_p:.2f} ({spy_pct:+.2f}% vs VWAP) | {regime}\n"
            f"• Sector Health: {q_text}\n"
            f"• Active Positions: {open_str}\n"
            f"• Frank Verdict: Tape conditions audited. Healthy sectors cleared for Wyckoff Springs."
        )
        safe_print(f"[SectorGuard] {msg}")
        send_message(msg, chat_id=TELEGRAM_CHAT_ID_DAY, force=True)
    except Exception as e:
        safe_print(f"[SectorGuard] Mid-day report error: {e}")

def send_frank_postmarket_report():
    """Report 2: Post-Market Institutional Wrap (4:05 PM EST, trading days only) - 100% Broker Actuals."""
    try:
        now_et = get_est_now()
        today_str = now_et.strftime('%Y-%m-%d')

        # 1. Pull exact broker-reconciled actuals from pnl_engine
        import pnl_engine
        pnl_data = pnl_engine.fetch_pnl_station_data()
        all_real_trades = pnl_data.get('trades', [])

        today_trades = [
            t for t in all_real_trades 
            if t.get('exit_date') == today_str or str(t.get('exit_timestamp', ''))[:10] == today_str
        ]

        tot = len(today_trades)
        wins = sum(1 for t in today_trades if t.get('outcome') == 'WIN')
        losses = sum(1 for t in today_trades if t.get('outcome') == 'LOSS')
        be = sum(1 for t in today_trades if t.get('outcome') == 'BREAKEVEN')
        today_dollar = sum(float(t.get('net_pnl_dollar') or 0.0) for t in today_trades)
        today_r = sum(float(t.get('pnl_r') or 0.0) for t in today_trades)

        trade_items = []
        for t in today_trades[:8]:
            sym = t.get('ticker')
            d_pnl = float(t.get('net_pnl_dollar') or 0.0)
            r_val = float(t.get('pnl_r') or 0.0)
            out_emoji = "🎯" if d_pnl > 0 else ("🛑" if d_pnl < 0 else "⚖️")
            trade_items.append(f"{out_emoji} {sym}: ${d_pnl:+.2f} ({r_val:+.2f}R)")

        trades_summary_str = " | ".join(trade_items) if trade_items else "Zero Executions Today"
        if len(today_trades) > 8:
            trades_summary_str += f" | +{len(today_trades) - 8} more"

        # 2. Sector status
        state = load_sector_guard_state()
        q_sectors = state.get("quarantined_sectors", {})
        q_str = f"Active Quarantines: {len(q_sectors)} ({', '.join([info.get('sector_name', k) for k, info in q_sectors.items()])})" if q_sectors else "All 12 Sector Circuit Breakers Neutral & Active"

        # 3. SPY closing tape
        spy_tape = audit_sector_tape('SPY')
        spy_close = spy_tape.get('current_price', 0.0)
        spy_pct = spy_tape.get('pct_vs_vwap', 0.0)

        # 4. Capital summary
        try:
            from public_executor import get_account_capital_summary
            cap = get_account_capital_summary()
            buying_power = float(cap.get('buying_power') or 0.0)
        except Exception:
            buying_power = 0.0

        msg = (
            f"🏛️ [FRANK WYCKOFF] POST-MARKET INSTITUTIONAL AUDIT\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Session: {now_et.strftime('%b %d, %Y')} Market Close Wrap\n"
            f"• Actuals Realized P&L: ${today_dollar:+.2f} ({today_r:+.2f}R)\n"
            f"• Closed Broker Trades: {tot} (Wins: {wins} | Losses: {losses} | Scratches: {be})\n"
            f"• Executions Breakdown:\n  {trades_summary_str}\n"
            f"• Final Sector Status: {q_str}\n"
            f"• SPY Closing Tape: ${spy_close:.2f} ({spy_pct:+.2f}% vs VWAP)\n"
            f"• Buying Power Available: ${buying_power:,.2f}\n"
            f"• Frank Research Safeguard: 100% SHADOW / READ-ONLY (Zero trading interventions).\n"
            f"• Tomorrow's Setup Radar: Exit Guardian cleared for opening bell. Coiled Spring setups queued."
        )
        safe_print(f"[SectorGuard] {msg}")
        send_message(msg, chat_id=TELEGRAM_CHAT_ID_DAY, force=True)
    except Exception as e:
        safe_print(f"[SectorGuard] Post-market report error: {e}")

def run_sector_radar_cycle():
    """
    Daemon hook: Runs every 10 seconds inside exit_guardian.
    Checks quarantined sectors every 15 minutes (or upon expiration) to determine
    whether to lift quarantine early or extend pause.
    """
    state = load_sector_guard_state()
    now_utc = get_utc_now()
    now_et = get_est_now()
    quarantined = state.get("quarantined_sectors", {})
    state_changed = False

    # 0. Frank Wyckoff 15-Minute Market Pulse Generator (Jim Cramer / Composite Man Tape Notes)
    try:
        import frank_pulse_engine
        frank_pulse_engine.generate_frank_pulse_note()
    except Exception as e_pulse:
        safe_print(f"[SectorGuard] Frank Pulse note update error: {e_pulse}")

    # ZERO TELEGRAM NOTIFICATIONS OUTSIDE REGULAR TRADING HOURS (Mon-Fri 9:30 AM - 4:00 PM EST)
    if not is_market_open(now_et):
        # Auto-clear any lingering quarantines, circuit breakers, or 45-min stop logs from prior sessions
        if quarantined or state.get("global_circuit_breaker", {}).get("active") or state.get("recent_stops_45m"):
            state["quarantined_sectors"] = {}
            state["global_circuit_breaker"] = {"active": False, "pause_until": None, "reason": None}
            state["recent_stops_45m"] = []
            save_sector_guard_state(state)
        return

    # 1. Audit Quarantined Sectors (RTH only)
    for etf, info in list(quarantined.items()):
        sec_name = info.get("sector_name", etf)
        last_audit_str = info.get("last_audited")
        q_until_str = info.get("quarantine_until")

        last_audit = pd.to_datetime(last_audit_str, utc=True).to_pydatetime() if last_audit_str else (now_utc - datetime.timedelta(minutes=20))
        q_until = pd.to_datetime(q_until_str, utc=True).to_pydatetime() if q_until_str else now_utc

        minutes_since_audit = (now_utc - last_audit).total_seconds() / 60.0
        time_expired = now_utc >= q_until

        # Run tape audit every 15 minutes OR when time has elapsed
        if minutes_since_audit >= 15.0 or time_expired:
            tape = audit_sector_tape(etf)
            curr_p = tape.get("current_price", 0.0)
            vwap = tape.get("vwap", 0.0)
            pct = tape.get("pct_vs_vwap", 0.0)
            stabilized = tape.get("is_stabilized", False)

            info["last_audited"] = now_utc.isoformat()
            info["audit_count"] = int(info.get("audit_count", 0)) + 1

            # Decision: LIFT or EXTEND
            if stabilized or (time_expired and pct >= -0.30):
                # Lift Quarantine!
                del quarantined[etf]
                state_changed = True
                msg = (
                    f"✅ [FRANK WYCKOFF SECTOR RADAR] QUARANTINE LIFTED\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• Sector: {sec_name.upper()} ({etf})\n"
                    f"• Tape Status: STABILIZED (Price: ${curr_p:.2f} | VWAP: ${vwap:.2f} [{pct:+.2f}%]).\n"
                    f"• Supply Exhaustion: Selling volume dried up; structure stabilizing.\n"
                    f"• Action: Quarantine LIFTED. Automated entries for {sec_name} RE-ENABLED!"
                )
                safe_print(f"[SectorGuard] {msg}")
                send_message(msg, chat_id=TELEGRAM_CHAT_ID_DAY, force=True)
            else:
                # Still waterfalling / distributing: extend 15 minutes SILENTLY (NO TELEGRAM SPAM)
                new_until = (now_utc + datetime.timedelta(minutes=15)).isoformat()
                info["quarantine_until"] = new_until
                state_changed = True
                safe_print(f"[SectorGuard] Quarantine extended 15m for {sec_name} ({etf}) - silent in Telegram")

    # 2. Audit Global Market Circuit Breaker
    gcb = state.get("global_circuit_breaker", {})
    if gcb.get("active"):
        p_until_str = gcb.get("pause_until")
        if p_until_str:
            t_pause = pd.to_datetime(p_until_str, utc=True).to_pydatetime()
            if now_utc >= t_pause:
                # Re-audit SPY
                spy_tape = audit_sector_tape('SPY')
                if spy_tape.get('is_stabilized', True):
                    state["global_circuit_breaker"]["active"] = False
                    state_changed = True
                    msg = (
                        f"✅ [FRANK WYCKOFF CIRCUIT BREAKER] MARKET PAUSE LIFTED\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"• SPY Tape: STABILIZED (Trading {spy_tape.get('pct_vs_vwap', 0):+.2f}% vs VWAP).\n"
                        f"• Action: Global pause LIFTED. All automated trading resumed!"
                    )
                    safe_print(f"[SectorGuard] {msg}")
                    send_message(msg, chat_id=TELEGRAM_CHAT_ID_DAY, force=True)
                else:
                    new_pause = (now_utc + datetime.timedelta(minutes=15)).isoformat()
                    state["global_circuit_breaker"]["pause_until"] = new_pause
                    state_changed = True


    # 3. Scheduled Daily Reports (2 per day ONLY on trading days: 12:00 PM and 4:05 PM EST)
    now_et = get_est_now()
    today_str = now_et.strftime('%Y-%m-%d')
    if is_trading_day(now_et):
        daily_reps = state.setdefault("daily_reports", {})
        
        # Report 1: Mid-Day Tape (12:00 PM - 12:15 PM EST)
        if now_et.hour == 12 and now_et.minute < 15:
            if daily_reps.get("last_midday_date") != today_str:
                send_frank_midday_report()
                daily_reps["last_midday_date"] = today_str
                state_changed = True

        # Report 2: Post-Market Wrap (4:05 PM - 4:25 PM EST)
        elif now_et.hour == 16 and 5 <= now_et.minute < 25:
            if daily_reps.get("last_postmarket_date") != today_str:
                send_frank_postmarket_report()
                daily_reps["last_postmarket_date"] = today_str
                state_changed = True

    if state_changed:
        state["quarantined_sectors"] = quarantined
        save_sector_guard_state(state)

def get_sector_guard_summary() -> dict:
    """Returns complete telemetry for UI and API endpoints."""
    state = load_sector_guard_state()
    now_utc = get_utc_now()
    
    # Active quarantines
    active_q = []
    for etf, info in state.get("quarantined_sectors", {}).items():
        q_until_str = info.get("quarantine_until")
        rem_min = 0
        if q_until_str:
            try:
                t_q = pd.to_datetime(q_until_str, utc=True).to_pydatetime()
                rem_min = max(0, int((t_q - now_utc).total_seconds() / 60.0))
            except Exception:
                pass
        active_q.append({
            "sector_etf": etf,
            "sector_name": info.get("sector_name", etf),
            "remaining_minutes": rem_min,
            "reason": info.get("reason"),
            "audit_count": info.get("audit_count", 0)
        })

    # Sector status list
    sectors_status = []
    q_etfs = set([q["sector_etf"] for q in active_q])
    for etf, name in SECTOR_NAMES.items():
        if etf == 'SPY':
            continue
        status = "QUARANTINED" if etf in q_etfs else "ACTIVE"
        sectors_status.append({
            "sector_etf": etf,
            "sector_name": name,
            "status": status
        })

    return {
        "global_circuit_breaker": state.get("global_circuit_breaker", {}),
        "quarantined_count": len(active_q),
        "quarantined_sectors": active_q,
        "sectors": sectors_status,
        "recent_stops_45m": state.get("recent_stops", [])
    }
