import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import concurrent.futures
import datetime
import json

# --- Constants & Themes ---
TV_BG = "#131722"
TV_PANEL = "#2A2E39"
TV_TEXT = "#D1D4DC"
TV_GRID = "#1E222D"
TV_GREEN = "#089981"
TV_RED = "#F23645"

TOP_20_TICKERS = ['AAPL','MSFT','GOOGL','AMZN','NVDA','META','BRK-B','TSLA','LLY','V',
                  'UNH','JPM','JNJ','XOM','WMT','MA','PG','AVGO','HD','CVX']

st.set_page_config(page_title="Wyckoff Matrix", layout="wide", initial_sidebar_state="expanded")

st.markdown(f"""
<style>
.stApp {{ background-color: {TV_BG}; color: {TV_TEXT}; font-family: 'Trebuchet MS', sans-serif; }}
.stSidebar {{ background-color: {TV_BG} !important; border-right: 1px solid {TV_GRID}; }}
h1, h2, h3, p, span {{ color: #ffffff !important; }}
.stButton>button {{ background-color: {TV_PANEL}; border: 1px solid {TV_GRID}; color: {TV_TEXT}; width: 100%; }}
.stButton>button:hover {{ border: 1px solid {TV_GREEN}; color: white; }}
.intel-card {{ background-color: {TV_PANEL}; border-radius: 8px; padding: 15px; border: 1px solid {TV_GRID}; margin-bottom: 15px; }}
</style>
""", unsafe_allow_html=True)

# --- Imports ---
try:
    from trade_tracker import get_stats, get_recent_trades
    TRACKER_AVAILABLE = True
except:
    TRACKER_AVAILABLE = False

try:
    from options_flow import get_options_flow
    OPTIONS_AVAILABLE = True
except:
    OPTIONS_AVAILABLE = False

# --- Session State ---
if 'selected_ticker' not in st.session_state: st.session_state['selected_ticker'] = "MSFT"
if 'active_tab' not in st.session_state: st.session_state['active_tab'] = 0

# --- DATA HELPERS ---
@st.cache_data(ttl=86400)
def get_all_tickers():
    try:
        with open('all_tickers.json', 'r') as f:
            return json.load(f)
    except:
        return TOP_20_TICKERS

def safe_col(df, col_name):
    return df[col_name].iloc[:, 0].values if isinstance(df.columns, pd.MultiIndex) else df[col_name].values

# --- MATH ENGINE ---
def get_supertrend(high, low, close, length, multiplier):
    tr0 = np.abs(high - low)
    tr1 = np.abs(high - np.roll(close, 1))
    tr2 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr0, np.maximum(tr1, tr2))
    tr[0] = 0
    atr = np.zeros_like(close, dtype=float)
    if len(close) > length:
        atr[length] = np.mean(tr[1:length+1])
        for i in range(length+1, len(close)): atr[i] = (atr[i-1] * (length - 1) + tr[i]) / length
    hl2 = (high + low) / 2
    upperband = hl2 + (multiplier * atr)
    lowerband = hl2 - (multiplier * atr)
    in_uptrend = np.ones(len(close), dtype=bool)
    supertrend_line = np.zeros(len(close))
    for i in range(1, len(close)):
        if close[i] > upperband[i-1]: in_uptrend[i] = True
        elif close[i] < lowerband[i-1]: in_uptrend[i] = False
        else:
            in_uptrend[i] = in_uptrend[i-1]
            if in_uptrend[i] and lowerband[i] < lowerband[i-1]: lowerband[i] = lowerband[i-1]
            if not in_uptrend[i] and upperband[i] > upperband[i-1]: upperband[i] = upperband[i-1]
        supertrend_line[i] = lowerband[i] if in_uptrend[i] else upperband[i]
    supertrend_line[0] = np.nan
    return in_uptrend, supertrend_line

# =============================================================================
# TRADE IDEAS SCANNER
# =============================================================================
def scan_ticker_for_ideas(ticker, df_data):
    """Analyze a single ticker and generate a trade idea card."""
    try:
        if isinstance(df_data.columns, pd.MultiIndex):
            highs = df_data['High'].iloc[:, 0].values.astype(float)
            lows = df_data['Low'].iloc[:, 0].values.astype(float)
            closes = df_data['Close'].iloc[:, 0].values.astype(float)
            vols = df_data['Volume'].iloc[:, 0].values.astype(float)
        else:
            highs = df_data['High'].values.astype(float)
            lows = df_data['Low'].values.astype(float)
            closes = df_data['Close'].values.astype(float)
            vols = df_data['Volume'].values.astype(float)

        if len(closes) < 50: return None

        u1, _ = get_supertrend(highs, lows, closes, 1, 1.0)
        u3, _ = get_supertrend(highs, lows, closes, 3, 3.0)
        u9, _ = get_supertrend(highs, lows, closes, 9, 9.0)
        u14, _ = get_supertrend(highs, lows, closes, 14, 14.0)

        # Bars in regime
        c9, c14 = u9[-1], u14[-1]
        is_bull = c9 and c14
        is_bear = not c9 and not c14
        bars_in_regime = 0
        if is_bull or is_bear:
            for i in range(len(u9)-1, -1, -1):
                if (is_bull and u9[i] and u14[i]) or (is_bear and not u9[i] and not u14[i]):
                    bars_in_regime += 1
                else: break

        # Volume exhaustion
        vol_sma = pd.Series(vols).rolling(20, min_periods=1).mean().values
        with np.errstate(divide='ignore', invalid='ignore'):
            rel_vol = np.where(vol_sma > 0, vols / vol_sma, 1.0)
        curr_rv = float(rel_vol[-1])

        # Range channel
        lookback = 100
        range_high = pd.Series(highs).rolling(lookback, min_periods=20).max().shift(1).values
        range_low = pd.Series(lows).rolling(lookback, min_periods=20).min().shift(1).values
        curr = len(closes) - 1
        if pd.isna(range_high[curr]) or pd.isna(range_low[curr]): return None

        price = float(closes[curr])
        rh = float(range_high[curr])
        rl = float(range_low[curr])

        # Check for Phase C conditions
        c_below = (lows[curr] < rl) or (lows[curr-1] < rl)
        c_above = (highs[curr] > rh) or (highs[curr-1] > rh)
        vol_dry = curr_rv < 1.2
        micro_flipped = u1[-1] != u1[-2]

        is_spring = c_below and u1[curr] and not u1[curr-1] and not u9[curr] and vol_dry
        is_utad = c_above and not u1[curr] and u1[curr-1] and u9[curr] and vol_dry

        # Determine direction
        if is_spring:
            direction = "LONG (SPRING)"
            entry = price
            sl = min(lows[curr], lows[curr-1]) * 0.99
            tp = rl + ((rh - rl) * 0.5)
        elif is_utad:
            direction = "SHORT (UTAD)"
            entry = price
            sl = max(highs[curr], highs[curr-1]) * 1.01
            tp = rh - ((rh - rl) * 0.5)
        elif bars_in_regime >= 20:
            direction = "STALKING"
            entry = price
            if is_bear:
                sl = rl * 0.99
                tp = rl + ((rh - rl) * 0.5)
            else:
                sl = rh * 1.01
                tp = rh - ((rh - rl) * 0.5)
        else:
            return None  # No actionable setup

        # --- ML CONFIDENCE SCORE ---
        score = 0
        # Bars in regime (max 35 pts)
        score += min(35, bars_in_regime * 1.5)
        # Volume exhaustion (max 25 pts)
        if curr_rv < 1.2: score += (1.2 - curr_rv) * 40
        # Cascade alignment (max 20 pts)
        cascade = f"{'U' if u1[curr] else 'D'}{'U' if u3[curr] else 'D'}{'U' if u9[curr] else 'D'}{'U' if u14[curr] else 'D'}"
        if cascade in ("DDDD", "UUUU"): score += 20
        elif cascade in ("DUDD", "UDUU"): score += 15  # Micro flipped against macro = reversal setup
        # Triggered bonus (max 20 pts)
        if is_spring or is_utad: score += 20
        elif bars_in_regime >= 30: score += 10

        score = min(100, int(score))

        # Build reason string
        regime_str = "bullish" if is_bull else "bearish" if is_bear else "mixed"
        reasons = []
        reasons.append(f"{bars_in_regime}-bar {regime_str} regime")
        reasons.append(f"vol {curr_rv:.1f}x")
        if is_spring: reasons.append("Micro ST flipped LONG")
        elif is_utad: reasons.append("Micro ST flipped SHORT")
        elif bars_in_regime >= 20: reasons.append("waiting for Micro flip")
        reason = ", ".join(reasons)

        return {
            "Ticker": ticker, "Price": price, "Direction": direction,
            "ML_Confidence": score, "Entry": entry, "Stop": sl, "Target": tp,
            "Reason": reason, "Bars": bars_in_regime, "Cascade": cascade,
            "RelVol": curr_rv, "Regime": "BULL" if is_bull else "BEAR" if is_bear else "MIXED"
        }
    except:
        return None

@st.cache_data(ttl=300)
def scan_trade_ideas():
    """Bulk download top 20 tickers and scan for trade ideas."""
    data = yf.download(TOP_20_TICKERS, period="2y", interval="1d", group_by='ticker', progress=False)
    results = []
    for ticker in TOP_20_TICKERS:
        try:
            df = data[ticker].dropna() if len(TOP_20_TICKERS) > 1 else data.dropna()
            res = scan_ticker_for_ideas(ticker, df)
            if res: results.append(res)
        except: pass
    return sorted(results, key=lambda x: x['ML_Confidence'], reverse=True)

# =============================================================================
# CHART RENDERING ENGINE
# =============================================================================
def render_wyckoff_chart(ticker, interval, period):
    df = yf.download(ticker, period=period, interval=interval, progress=False)
    if df.empty:
        st.error(f"No data found for {ticker} on {interval}.")
        return None
    dates = df.index
    df_clean = pd.DataFrame({
        'Open': safe_col(df, 'Open'), 'High': safe_col(df, 'High'),
        'Low': safe_col(df, 'Low'), 'Close': safe_col(df, 'Close'),
        'Volume': safe_col(df, 'Volume')
    })

    u1, line1 = get_supertrend(df_clean['High'].values, df_clean['Low'].values, df_clean['Close'].values, 1, 1.0)
    u3, line3 = get_supertrend(df_clean['High'].values, df_clean['Low'].values, df_clean['Close'].values, 3, 3.0)
    u9, line9 = get_supertrend(df_clean['High'].values, df_clean['Low'].values, df_clean['Close'].values, 9, 9.0)
    u14, line14 = get_supertrend(df_clean['High'].values, df_clean['Low'].values, df_clean['Close'].values, 14, 14.0)

    lookback = 100
    df_clean['Range_High'] = df_clean['High'].rolling(lookback, min_periods=20).max().shift(1)
    df_clean['Range_Low'] = df_clean['Low'].rolling(lookback, min_periods=20).min().shift(1)

    vol_sma = pd.Series(df_clean['Volume']).rolling(20, min_periods=1).mean()
    rel_vol = df_clean['Volume'] / vol_sma
    vol_colors = ['#FFD700' if rv < 1.2 else '#555555' for rv in rel_vol]

    # Find Springs and UTADs
    spring_x, spring_y, utad_x, utad_y = [], [], [], []
    l_wins, l_loss, s_wins, s_loss = 0, 0, 0, 0
    l_units, s_units = 0.0, 0.0
    backtest_trades = []

    for i in range(1, len(df_clean)):
        is_below = df_clean['Low'].iloc[i] < df_clean['Range_Low'].iloc[i] or df_clean['Low'].iloc[i-1] < df_clean['Range_Low'].iloc[i-1] if not pd.isna(df_clean['Range_Low'].iloc[i]) else False
        is_above = df_clean['High'].iloc[i] > df_clean['Range_High'].iloc[i] or df_clean['High'].iloc[i-1] > df_clean['Range_High'].iloc[i-1] if not pd.isna(df_clean['Range_High'].iloc[i]) else False

        is_spring = is_below and u1[i] and not u1[i-1] and not u9[i] and rel_vol.iloc[i] < 1.2
        is_utad = is_above and not u1[i] and u1[i-1] and u9[i] and rel_vol.iloc[i] < 1.2

        if is_spring:
            spring_x.append(dates[i]); spring_y.append(df_clean['Low'].iloc[i] * 0.99)
            entry = df_clean['Close'].iloc[i]
            sl = min(df_clean['Low'].iloc[i], df_clean['Low'].iloc[i-1]) * 0.998
            tp = df_clean['Range_Low'].iloc[i] + ((df_clean['Range_High'].iloc[i] - df_clean['Range_Low'].iloc[i]) * 0.5)
            sl_dist = abs(entry - sl); tp_dist = abs(tp - entry)
            rr = (tp_dist / sl_dist) if sl_dist > 0 else 0
            for j in range(i+1, len(df_clean)):
                if df_clean['High'].iloc[j] >= tp:
                    l_wins+=1; l_units += rr
                    backtest_trades.append({'date': dates[i].strftime('%Y-%m-%d'), 'direction': 'LONG', 'outcome': 'WIN', 'pnl_r': round(rr, 2)})
                    break
                if df_clean['Low'].iloc[j] <= sl:
                    l_loss+=1; l_units -= 1.0
                    backtest_trades.append({'date': dates[i].strftime('%Y-%m-%d'), 'direction': 'LONG', 'outcome': 'LOSS', 'pnl_r': -1.0})
                    break

        if is_utad:
            utad_x.append(dates[i]); utad_y.append(df_clean['High'].iloc[i] * 1.01)
            entry = df_clean['Close'].iloc[i]
            sl = max(df_clean['High'].iloc[i], df_clean['High'].iloc[i-1]) * 1.002
            tp = df_clean['Range_High'].iloc[i] - ((df_clean['Range_High'].iloc[i] - df_clean['Range_Low'].iloc[i]) * 0.5)
            sl_dist = abs(sl - entry); tp_dist = abs(entry - tp)
            rr = (tp_dist / sl_dist) if sl_dist > 0 else 0
            for j in range(i+1, len(df_clean)):
                if df_clean['Low'].iloc[j] <= tp:
                    s_wins+=1; s_units += rr
                    backtest_trades.append({'date': dates[i].strftime('%Y-%m-%d'), 'direction': 'SHORT', 'outcome': 'WIN', 'pnl_r': round(rr, 2)})
                    break
                if df_clean['High'].iloc[j] >= sl:
                    s_loss+=1; s_units -= 1.0
                    backtest_trades.append({'date': dates[i].strftime('%Y-%m-%d'), 'direction': 'SHORT', 'outcome': 'LOSS', 'pnl_r': -1.0})
                    break

    # --- PLOTTING ---
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.8, 0.2])
    fig.add_trace(go.Candlestick(x=dates, open=df_clean['Open'], high=df_clean['High'], low=df_clean['Low'], close=df_clean['Close'], increasing_line_color=TV_GREEN, decreasing_line_color=TV_RED, increasing_fillcolor=TV_GREEN, decreasing_fillcolor=TV_RED, name="Price"), row=1, col=1)
    fig.add_trace(go.Scatter(x=dates, y=df_clean['Range_High'], mode='lines', line=dict(color='#787B86', dash='dot', width=1), name='Phase B High'), row=1, col=1)
    fig.add_trace(go.Scatter(x=dates, y=df_clean['Range_Low'], mode='lines', line=dict(color='#787B86', dash='dot', width=1), name='Phase B Low'), row=1, col=1)
    if spring_x: fig.add_trace(go.Scatter(x=spring_x, y=spring_y, mode='markers+text', marker=dict(symbol='triangle-up', size=16, color=TV_GREEN), text=["[C] SPRING"] * len(spring_x), textposition="bottom center", textfont=dict(color=TV_GREEN, size=14)), row=1, col=1)
    if utad_x: fig.add_trace(go.Scatter(x=utad_x, y=utad_y, mode='markers+text', marker=dict(symbol='triangle-down', size=16, color=TV_RED), text=["[C] UTAD"] * len(utad_x), textposition="top center", textfont=dict(color=TV_RED, size=14)), row=1, col=1)

    def add_st(u_dir, line_val, width, opacity=1.0, dash='solid'):
        up_vals = np.where(u_dir, line_val, np.nan); dn_vals = np.where(~u_dir, line_val, np.nan)
        fig.add_trace(go.Scatter(x=dates, y=up_vals, mode='lines', line=dict(color=TV_GREEN, width=width, dash=dash), opacity=opacity, showlegend=False), row=1, col=1)
        fig.add_trace(go.Scatter(x=dates, y=dn_vals, mode='lines', line=dict(color=TV_RED, width=width, dash=dash), opacity=opacity, showlegend=False), row=1, col=1)
    add_st(u1, line1, width=1, opacity=0.9)
    add_st(u3, line3, width=1, opacity=0.5, dash='dot')
    add_st(u9, line9, width=2, opacity=0.8)
    add_st(u14, line14, width=3, opacity=1.0)
    fig.add_trace(go.Bar(x=dates, y=df_clean['Volume'], marker_color=vol_colors, name="Volume"), row=2, col=1)

    cascade = f"{'U' if u1[-1] else 'D'} | {'U' if u3[-1] else 'D'} | {'U' if u9[-1] else 'D'} | {'U' if u14[-1] else 'D'}"
    rangebreaks = [dict(bounds=["sat", "mon"])]
    if interval in ["5m", "15m", "1h"]: rangebreaks.append(dict(bounds=[16, 9.5], pattern="hour"))
    fig.update_layout(
        title=dict(text=f"<b>{ticker.upper()} ({interval})</b> <span style='font-size: 14px; color: {TV_TEXT};'>&nbsp;&nbsp; [{cascade}] &nbsp;&nbsp; RV: {round(float(rel_vol.iloc[-1]), 2)}x</span>", font=dict(size=24, color='#FFFFFF')),
        template="plotly_dark", xaxis_rangeslider_visible=False, height=750, margin=dict(l=50, r=20, t=60, b=20),
        paper_bgcolor=TV_BG, plot_bgcolor=TV_BG, showlegend=False
    )
    end_date = dates[-1]
    if interval == "5m": start_date = dates[-min(len(dates), 200)]
    elif interval == "15m": start_date = dates[-min(len(dates), 250)]
    elif interval == "1h": start_date = dates[-min(len(dates), 150)]
    else: start_date = dates[0]
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor=TV_GRID, rangebreaks=rangebreaks, range=[start_date, end_date])
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor=TV_GRID, tickprefix="$")

    return {"fig": fig, "df": df_clean, "backtest_trades": backtest_trades,
            "l_wins": l_wins, "l_loss": l_loss, "s_wins": s_wins, "s_loss": s_loss,
            "l_units": l_units, "s_units": s_units, "tot_units": l_units + s_units,
            "long_wr": (l_wins / (l_wins + l_loss) * 100) if (l_wins + l_loss) > 0 else 0,
            "short_wr": (s_wins / (s_wins + s_loss) * 100) if (s_wins + s_loss) > 0 else 0}

# =============================================================================
# TICKER TAPE & LIVE SYSTEM STATUS
# =============================================================================
@st.cache_data(ttl=30)
def get_tape_and_status_data():
    import sqlite3
    stats = {
        'open_count': 0, 'win_count': 0, 'loss_count': 0, 'user_active_count': 0,
        'recent_alerts': [], 'macro': []
    }
    try:
        conn = sqlite3.connect("wyckoff_trades.db")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM alerts WHERE outcome = 'OPEN'")
        stats['open_count'] = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM alerts WHERE outcome = 'WIN'")
        stats['win_count'] = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM alerts WHERE outcome = 'LOSS'")
        stats['loss_count'] = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM alerts WHERE user_active = 1")
        stats['user_active_count'] = c.fetchone()[0]
        c.execute("SELECT ticker, direction, timeframe, entry_price, stop_loss, take_profit, user_active, breakeven_set FROM alerts WHERE outcome = 'OPEN' ORDER BY id DESC LIMIT 10")
        stats['recent_alerts'] = [dict(r) for r in c.fetchall()]
        conn.close()
    except Exception as e:
        pass
        
    try:
        macro_df = yf.download(["SPY", "QQQ", "^VIX"], period="2d", interval="1d", progress=False)
        for sym, label in [("SPY", "SPY"), ("QQQ", "QQQ"), ("^VIX", "VIX")]:
            if sym in macro_df['Close']:
                s = macro_df['Close'][sym].dropna()
                if len(s) >= 2:
                    p0, p1 = float(s.iloc[-2]), float(s.iloc[-1])
                    chg = ((p1 - p0) / p0) * 100
                    stats['macro'].append((label, p1, chg))
                elif len(s) == 1:
                    stats['macro'].append((label, float(s.iloc[-1]), 0.0))
    except:
        pass
    return stats

def render_live_ticker_tape():
    data = get_tape_and_status_data()
    
    # 1. Status Row Cards
    now = datetime.datetime.now()
    is_shield = (now.hour == 9 and 30 <= now.minute < 45)
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f"""
        <div style="background:{TV_PANEL}; border:1px solid {TV_GRID}; border-left:4px solid {TV_GREEN}; border-radius:6px; padding:8px 12px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase; font-weight:bold;">🤖 Scanner Engine</div>
            <div style="font-size:14px; font-weight:bold; color:#FFF; margin-top:2px;">🟢 SCANNING LIVE</div>
            <div style="font-size:10px; color:{TV_GREEN};">5m, 15m, 1h, 1d Active</div>
        </div>
        """, unsafe_allow_html=True)
    with col2:
        st.markdown(f"""
        <div style="background:{TV_PANEL}; border:1px solid {TV_GRID}; border-left:4px solid #3b82f6; border-radius:6px; padding:8px 12px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase; font-weight:bold;">⚡ Protection Shield</div>
            <div style="font-size:14px; font-weight:bold; color:#FFF; margin-top:2px;">{"🛡️ OPENING SHIELD" if is_shield else "🛡️ WHIPSAW GUARD"}</div>
            <div style="font-size:10px; color:#3b82f6;">Anti-Spam & BE Live</div>
        </div>
        """, unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
        <div style="background:{TV_PANEL}; border:1px solid {TV_GRID}; border-left:4px solid #a855f7; border-radius:6px; padding:8px 12px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase; font-weight:bold;">🔗 Public.com Sync</div>
            <div style="font-size:14px; font-weight:bold; color:#FFF; margin-top:2px;">🟢 ZERO-CLICK SYNC</div>
            <div style="font-size:10px; color:#a855f7;">{data['user_active_count']} Active Monitored</div>
        </div>
        """, unsafe_allow_html=True)
    with col4:
        tot_closed = data['win_count'] + data['loss_count']
        wr = (data['win_count'] / tot_closed * 100) if tot_closed > 0 else 0
        st.markdown(f"""
        <div style="background:{TV_PANEL}; border:1px solid {TV_GRID}; border-left:4px solid #eab308; border-radius:6px; padding:8px 12px;">
            <div style="font-size:10px; color:#888; text-transform:uppercase; font-weight:bold;">📈 Portfolio Tracker</div>
            <div style="font-size:14px; font-weight:bold; color:#FFF; margin-top:2px;">{data['open_count']} Setups Watched</div>
            <div style="font-size:10px; color:#eab308;">{data['win_count']}W / {data['loss_count']}L ({wr:.0f}% WR)</div>
        </div>
        """, unsafe_allow_html=True)

    # 2. Continuous Scrolling Ticker Tape
    tape_items = []
    for label, val, chg in data['macro']:
        color = TV_GREEN if chg >= 0 else TV_RED
        symbol = "▲" if chg >= 0 else "▼"
        tape_items.append(f"<span style='color:#FFF; font-weight:bold;'>{label}</span> ${val:.2f} <span style='color:{color}; font-weight:bold;'>{symbol} {chg:+.2f}%</span>")
        
    for a in data['recent_alerts']:
        sym = a['ticker']
        d = a['direction']
        tf = a.get('timeframe', '5m')
        b_tag = " <span style='color:#3b82f6;'>[🛡️BE]</span>" if a.get('breakeven_set') else ""
        u_tag = " <span style='color:#a855f7;'>[👤ACTIVE]</span>" if a.get('user_active') else ""
        
        try:
            risk = abs(float(a['entry_price']) - float(a['stop_loss']))
            reward = abs(float(a['take_profit']) - float(a['entry_price']))
            r_val = (reward / risk) if risk > 0 else 0
            r_str = f"{r_val:.1f}R"
        except:
            r_str = ""
            
        color = TV_GREEN if d == "LONG" else TV_RED
        badge = "🟢 LONG" if d == "LONG" else "🔴 SHORT"
        tape_items.append(f"<span style='color:#FFF; font-weight:bold;'>${sym}</span> <span style='color:{color};'>{badge} ({tf})</span> <span style='color:#FFD700;'>{r_str}</span>{b_tag}{u_tag}")

    tape_content = " &nbsp;&nbsp;&nbsp;&nbsp;•&nbsp;&nbsp;&nbsp;&nbsp; ".join(tape_items) if tape_items else "Scanning market for Wyckoff setups..."
    
    marquee_html = f"""
    <style>
    @keyframes marquee {{
      0%   {{ transform: translateX(0%); }}
      100% {{ transform: translateX(-50%); }}
    }}
    .tape-container {{
      width: 100%;
      overflow: hidden;
      background: #181b24;
      border: 1px solid #2a2e39;
      border-radius: 6px;
      padding: 7px 0;
      margin: 10px 0 16px 0;
      box-shadow: inset 0 1px 3px rgba(0,0,0,0.4);
    }}
    .tape-inner {{
      display: inline-block;
      white-space: nowrap;
      animation: marquee 40s linear infinite;
    }}
    .tape-inner:hover {{
      animation-play-state: paused;
    }}
    </style>
    <div class="tape-container">
      <div class="tape-inner">
        {tape_content} &nbsp;&nbsp;&nbsp;&nbsp;•&nbsp;&nbsp;&nbsp;&nbsp; {tape_content}
      </div>
    </div>
    """
    st.markdown(marquee_html, unsafe_allow_html=True)

# =============================================================================
# MAIN UI
# =============================================================================
st.title("Wyckoff Matrix")
render_live_ticker_tape()

# --- SIDEBAR ---
with st.sidebar:
    st.markdown(f"<div style='background:{TV_GREEN}; color:white; padding:8px 12px; border-radius:6px; text-align:center; margin-bottom:20px; font-weight:bold;'>🤖 ML Managing All Parameters</div>", unsafe_allow_html=True)

    st.markdown("### 📋 Market Radar")
    watchlist = st.selectbox("Watchlist:", ["Top 20 Mega-Cap", "Full Universe (510)"], index=0)
    if watchlist == "Full Universe (510)":
        radar_tickers = get_all_tickers()[:25]
    else:
        radar_tickers = TOP_20_TICKERS

    if st.button("🔄 Rescan Market"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("<p style='font-size: 11px; color: #888;'>Quick Jump:</p>", unsafe_allow_html=True)
    for t in ['SPY', 'QQQ', 'AAPL', 'NVDA', 'TSLA', 'MSFT']:
        if st.button(t, key=f"sidebar_{t}"):
            st.session_state['selected_ticker'] = t
            st.rerun()

# --- TABS (Programmatic Navigation) ---
TABS = ["🎯 Trade Ideas", "📊 Chart Terminal", "📈 Performance", "🧠 ML Brain"]

# Clean up stale session state from old version where active_tab was an integer
if 'active_tab' not in st.session_state or st.session_state['active_tab'] not in TABS:
    st.session_state['active_tab'] = TABS[0]

# Use a horizontal radio button to simulate tabs that we can control via session state
selected_tab = st.radio("Navigation", TABS, horizontal=True, label_visibility="collapsed", index=TABS.index(st.session_state['active_tab']))
st.session_state['active_tab'] = selected_tab

def get_live_db_trades():
    import sqlite3
    try:
        conn = sqlite3.connect("wyckoff_trades.db")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM alerts WHERE outcome = 'OPEN' ORDER BY id DESC")
        rows = c.fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except:
        return []

# ===== TAB 1: TRADE IDEAS =====
if selected_tab == TABS[0]:
    st.markdown("### ⚡ Live Trade Ideas — ML Ranked")
    st.caption("Scanning Top 20 Mega-Caps for Wyckoff Phase C setups. Ranked by ML Confidence Score.")

    # Pull actual live triggered trades directly from the bot's database
    db_trades = get_live_db_trades()
    
    if db_trades:
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
        filtered_db_trades = []
        for t in db_trades:
            try:
                risk = abs(float(t["entry_price"]) - float(t["stop_loss"]))
                reward = abs(float(t["take_profit"]) - float(t["entry_price"]))
                r_units = (reward / risk) if risk > 0 else 0
                if r_units >= 1.5: filtered_db_trades.append(t)
            except: pass
            
        st.markdown(f"#### ? TRIGGERED ({len(filtered_db_trades)} Live Bot Alerts)")
        for t in filtered_db_trades:
            # Reconstruct the card format from the DB record
            border_color = TV_GREEN if "LONG" in t['direction'] else TV_RED
            dir_emoji = "🟢" if "LONG" in t['direction'] else "🔴"
            bars = t.get('bars_in_regime', 0)
            conf = min(100, 50 + (bars * 2)) # estimate confidence for DB trades
            conf_color = TV_GREEN if conf > 70 else ("#E6A23C" if conf > 50 else TV_RED)
            
            tf = t.get('timeframe', '5m')
            trade_style = styles.get(tf, "Unknown")
            exp_time = expected_times.get(tf, "Unknown")
            reason = f"TF: {tf} | Style: {trade_style} ({exp_time}) | Context: {t.get('regime', 'Unknown')}"
            
            try:
                risk = abs(float(t['entry_price']) - float(t['stop_loss']))
                reward = abs(float(t['take_profit']) - float(t['entry_price']))
                r_units = round(reward / risk, 2) if risk > 0 else 0.0
            except:
                r_units = 0.0
            
            st.markdown(f"""
            <div style="background-color:{TV_PANEL}; border-left: 5px solid {border_color}; padding: 20px; border-radius: 10px; margin-bottom: 15px;">
                <div style="display:flex; justify-content:space-between; align-items:center;">
                    <h3 style="margin:0;">{dir_emoji} {t['ticker']} <span style="font-size:0.6em; color:#888;">@ ${t['entry_price']:.2f}</span></h3>
                    <span style="background:{border_color}; color:white; padding:4px 12px; border-radius:15px; font-weight:bold; font-size:13px;">{t['direction']}</span>
                </div>
                <div style="margin-top:12px;">
                    <strong>ML Confidence: ~{conf}%</strong>
                    <div style="width:100%; background:#333; border-radius:5px; height:8px; margin-top:4px;">
                        <div style="width:{conf}%; background:{conf_color}; height:100%; border-radius:5px;"></div>
                    </div>
                </div>
                <div style="display:flex; justify-content:space-between; margin-top:15px; font-size:14px;">
                    <div>🟢 Entry: <strong>${t['entry_price']:.2f}</strong></div>
                    <div>🔴 Stop: <strong>${t['stop_loss']:.2f}</strong></div>
                    <div>🎯 Target: <strong>${t['take_profit']:.2f}</strong></div>
                    <div style="color:#2962FF; font-weight:bold;">⚡ {r_units}R Units</div>
                </div>
                <p style="margin-top:12px; font-style:italic; color:#aaa; font-size:13px;">🧠 WHY: {reason}</p>
            </div>
            """, unsafe_allow_html=True)
            if st.button(f"📊 Open {t['ticker']} Chart", key=f"db_{t['id']}_{t['ticker']}"):
                st.session_state['selected_ticker'] = t['ticker']
                st.session_state['active_tab'] = TABS[1]
                st.rerun()
                
    st.markdown("---")

    with st.spinner("Scanning 1D macro market for building setups..."):
        ideas = scan_trade_ideas()

    if not ideas and not db_trades:
        st.info("No actionable setups detected right now. The market may be in a consolidation phase.")
    elif ideas:
        stalking = [i for i in ideas if i['Direction'] == "STALKING"]
        if stalking:
            st.markdown(f"#### 👁️ STALKING ({len(stalking)} Building 1D Setups)")
            st.caption("These are exhausted regimes waiting for the Micro SuperTrend to flip. Do NOT enter yet.")
            cols = st.columns(3)
            for idx, idea in enumerate(stalking):
                regime_color = TV_RED if idea['Regime'] == 'BEAR' else TV_GREEN if idea['Regime'] == 'BULL' else TV_TEXT
                conf_color = TV_GREEN if idea['ML_Confidence'] > 70 else ("#E6A23C" if idea['ML_Confidence'] > 50 else TV_RED)
                with cols[idx % 3]:
                    st.markdown(f"""
                    <div style="background:{TV_PANEL}; border-left: 4px solid #E6A23C; padding: 15px; border-radius: 8px; margin-bottom: 10px;">
                        <div style="display:flex; justify-content:space-between;">
                            <strong>{idea['Ticker']}</strong>
                            <span style="color:{regime_color}; font-size:12px;">{idea['Regime']} ({idea['Bars']} bars)</span>
                        </div>
                        <div style="font-size:12px; color:#888; margin-top:5px;">Vol: {idea['RelVol']:.1f}x | Conf: <span style="color:{conf_color};">{idea['ML_Confidence']}%</span></div>
                        <div style="font-size:11px; color:#666; margin-top:4px;">{idea['Reason']}</div>
                    </div>
                    """, unsafe_allow_html=True)
                    if st.button(f"Chart {idea['Ticker']}", key=f"stalk_{idea['Ticker']}"):
                        st.session_state['selected_ticker'] = idea['Ticker']
                        st.session_state['active_tab'] = TABS[1]
                        st.rerun()

# ===== TAB 2: CHART TERMINAL =====
elif selected_tab == TABS[1]:
    col_tk, col_tf = st.columns([2, 1])
    with col_tk:
        def update_ticker():
            st.session_state['selected_ticker'] = st.session_state['chart_search'].upper()
        st.text_input("🔍 Ticker:", value=st.session_state['selected_ticker'], key="chart_search", on_change=update_ticker)
    with col_tf:
        timeframe = st.selectbox("⏱️ Timeframe:", ["5m", "15m", "1h", "1d", "1wk"], index=3)

    if timeframe in ["1d", "1wk"]: dl_period = "2y"
    elif timeframe == "1h": dl_period = "730d"
    else: dl_period = "60d"

    chart_data = render_wyckoff_chart(st.session_state['selected_ticker'], timeframe, dl_period)
    if chart_data:
        c_chart, c_cards = st.columns([3.5, 1])
        with c_chart:
            st.plotly_chart(chart_data['fig'], use_container_width=True)
        with c_cards:
            with st.expander("🌊 Options Flow", expanded=True):
                if OPTIONS_AVAILABLE:
                    try:
                        flow = get_options_flow(st.session_state['selected_ticker'])
                        if flow and flow.get('total_call_oi', 0) > 0:
                            st.markdown(f"""
                            <div style='font-size:13px;'>
                                <div style='display:flex; justify-content:space-between;'><span>Sentiment:</span> <strong>{flow.get('net_sentiment')}</strong></div>
                                <div style='display:flex; justify-content:space-between;'><span>Put/Call:</span> <strong>{flow.get('put_call_ratio')}</strong></div>
                                <div style='display:flex; justify-content:space-between;'><span>Gamma Wall:</span> <strong style='color:#2962FF;'>${flow.get('gamma_wall')}</strong></div>
                                <div style='display:flex; justify-content:space-between;'><span>Max Pain:</span> <strong>${flow.get('max_pain')}</strong></div>
                            </div>
                            """, unsafe_allow_html=True)
                        else:
                            st.write("No institutional options chain data available right now.")
                    except: st.write("Error fetching options flow.")
                else: st.write("Module offline.")

            with st.expander("📊 Volume Profile", expanded=True):
                try:
                    vol_bins = pd.cut(chart_data['df']['Close'], bins=30)
                    vol_profile = chart_data['df'].groupby(vol_bins, observed=False)['Volume'].sum()
                    vp_fig = go.Figure(go.Bar(x=vol_profile.values, y=[f"${v.mid:.2f}" for v in vol_profile.index], orientation='h', marker_color='rgba(41, 98, 255, 0.6)'))
                    vp_fig.update_layout(margin=dict(l=0,r=0,t=0,b=0), height=200, paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', xaxis=dict(visible=False), yaxis=dict(tickfont=dict(size=10, color='#888')))
                    st.plotly_chart(vp_fig, use_container_width=True, config={'displayModeBar': False})
                except: st.write("Error rendering profile.")

            with st.expander("📝 Backtest Log", expanded=True):
                bt = chart_data.get('backtest_trades', [])
                if bt:
                    bt.reverse()
                    st.dataframe(pd.DataFrame(bt[:10])[['date','direction','outcome','pnl_r']], use_container_width=True, hide_index=True)
                    st.markdown(f"**Long WR:** {chart_data['long_wr']:.1f}% | **Short WR:** {chart_data['short_wr']:.1f}% | **Net: {chart_data['tot_units']:+.2f}R**")
                else:
                    st.write("No backtest trades.")

# ===== TAB 3: PERFORMANCE =====
elif selected_tab == TABS[2]:
    st.markdown("### 📈 Bot Performance & Trade Log")
    if not TRACKER_AVAILABLE:
        st.warning("trade_tracker.py module not found.")
    else:
        stats = get_stats()
        r1, r2, r3, r4 = st.columns(4)
        with r1: st.metric("Total Bot Alerts", stats['total_trades'])
        with r2: st.metric("Win Rate", f"{stats['win_rate']:.1f}%")
        with r3: st.metric("Avg PnL", f"{stats['avg_pnl_r']:+.2f}R")
        with r4: st.metric("Open Trades", stats['open_count'])

        st.markdown("---")
        st.markdown("### Live Alert Log")
        recent = get_recent_trades(100)
        if recent:
            df_trades = pd.DataFrame(recent)
            tf_filter = st.selectbox("Filter Timeframe:", ["ALL", "5m", "15m", "1h", "1d"], key="perf_tf")
            if tf_filter != "ALL" and 'timeframe' in df_trades.columns:
                df_trades = df_trades[df_trades['timeframe'] == tf_filter]

            display_cols = ['timestamp', 'ticker', 'direction', 'entry_price', 'stop_loss', 'take_profit', 'outcome', 'pnl_r']
            if 'timeframe' in df_trades.columns: display_cols.insert(2, 'timeframe')
            if 'bars_in_regime' in df_trades.columns: display_cols.append('bars_in_regime')
            if 'vwap_distance' in df_trades.columns: display_cols.append('vwap_distance')
            if 'hour_of_day' in df_trades.columns: display_cols.append('hour_of_day')
            if 'spy_bullish' in df_trades.columns: display_cols.append('spy_bullish')
            if 'atr_expansion' in df_trades.columns: display_cols.append('atr_expansion')
            available_cols = [c for c in display_cols if c in df_trades.columns]
            st.dataframe(df_trades[available_cols], use_container_width=True, hide_index=True)

            if 'pnl_r' in df_trades.columns and 'timestamp' in df_trades.columns:
                pnl_trades = df_trades[df_trades['pnl_r'].notna()].copy()
                if not pnl_trades.empty:
                    st.markdown("### Cumulative PnL Curve (R-Units)")
                    pnl_trades = pnl_trades.sort_values('timestamp')
                    pnl_trades['cumulative_r'] = pnl_trades['pnl_r'].cumsum()
                    pnl_fig = go.Figure()
                    pnl_fig.add_trace(go.Scatter(x=pnl_trades['timestamp'], y=pnl_trades['cumulative_r'], mode='lines+markers', line=dict(color='#2962FF', width=2), fill='tozeroy', fillcolor='rgba(41, 98, 255, 0.1)'))
                    pnl_fig.update_layout(template='plotly_dark', height=350, margin=dict(l=40, r=20, t=20, b=40), paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)')
                    st.plotly_chart(pnl_fig, use_container_width=True)
        else:
            st.info("No bot alerts logged yet. Leave the bot running during market hours!")

# ===== TAB 4: ML BRAIN =====
elif selected_tab == TABS[3]:
    try:
        with open('algo_documentation.md', 'r', encoding='utf-8') as md_file:
            st.markdown(md_file.read())
    except:
        st.warning('algo_documentation.md not found.')
