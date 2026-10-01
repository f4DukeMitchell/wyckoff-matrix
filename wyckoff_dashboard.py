import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import concurrent.futures
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- TV THEME SETTINGS ---
st.set_page_config(page_title="Wyckoff Institutional Terminal", layout="wide", initial_sidebar_state="expanded")

TV_BG = "#131722"
TV_PANEL = "#2A2E39"
TV_TEXT = "#D1D4DC"
TV_GRID = "#1E222D"
TV_GREEN = "#089981"
TV_RED = "#F23645"

st.markdown(f"""
    <style>
    .stApp {{ background-color: {TV_BG}; color: {TV_TEXT}; font-family: 'Trebuchet MS', sans-serif; }}
    .stSidebar {{ background-color: {TV_BG} !important; border-right: 1px solid {TV_GRID}; }}
    .stTextInput>div>div>input, .stSelectbox>div>div>div, .stSlider>div>div>div {{ background-color: {TV_PANEL}; color: {TV_TEXT}; border: 1px solid {TV_GRID}; }}
    .stDataFrame {{ background-color: {TV_PANEL}; }}
    h1, h2, h3, p, span {{ color: #ffffff !important; }}
    
    .stButton>button {{ background-color: {TV_PANEL}; border: 1px solid {TV_GRID}; color: {TV_TEXT}; width: 100%; padding: 5px; text-align: left; font-weight: bold; }}
    .stButton>button:hover {{ border: 1px solid {TV_GREEN}; color: white; }}
    .opt-btn>button {{ background-color: #4A148C; border: 1px solid #7B1FA2; text-align: center; margin-bottom: 15px; }}
    .opt-btn>button:hover {{ background-color: #7B1FA2; border: 1px solid white; }}
    
    .intel-card {{ background-color: {TV_PANEL}; border-radius: 8px; padding: 15px; border: 1px solid {TV_GRID}; margin-bottom: 15px; }}
    </style>
""", unsafe_allow_html=True)

if 'selected_ticker' not in st.session_state: st.session_state['selected_ticker'] = "MSFT"

# --- SESSION STATE FOR AUTO-TUNING SLIDERS ---
if 'lb_val' not in st.session_state: st.session_state.lb_val = 100
if 'vol_val' not in st.session_state: st.session_state.vol_val = 1.0
if 'sl_val' not in st.session_state: st.session_state.sl_val = 0.2
if 'tp_val' not in st.session_state: st.session_state.tp_val = "Full Phase B Range"

# --- DATA HELPERS ---
@st.cache_data(ttl=86400)
def get_sp500_tickers():
    try:
        import json
        with open('all_tickers.json', 'r') as f:
            return json.load(f)
    except Exception:
        return ["AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","BRK-B","LLY","AVGO","JPM","V","UNH","MA","PG","JNJ","HD","MRK","ABBV","COST"]

# --- MATH ENGINE ---
def safe_col(df, col_name):
    return df[col_name].iloc[:, 0].values if isinstance(df.columns, pd.MultiIndex) else df[col_name].values

def get_supertrend(df_clean, length, multiplier):
    high, low, close = df_clean['High'].values, df_clean['Low'].values, df_clean['Close'].values
    tr0 = np.abs(high - low)
    tr1 = np.abs(high - np.roll(close, 1))
    tr2 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr0, np.maximum(tr1, tr2))
    tr[0] = 0
    atr = np.zeros_like(close)
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

def process_ticker(ticker, interval, period, sl_buffer, tp_target, lookback, vol_limit):
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False)
        if df.empty or len(df) < lookback: return None
        df_clean = pd.DataFrame({'High': safe_col(df, 'High'), 'Low': safe_col(df, 'Low'), 'Close': safe_col(df, 'Close'), 'Volume': safe_col(df, 'Volume')})
        u1, _ = get_supertrend(df_clean, 1, 1.0)
        u3, _ = get_supertrend(df_clean, 3, 3.0)
        u9, _ = get_supertrend(df_clean, 9, 9.0)
        u14, _ = get_supertrend(df_clean, 14, 14.0)
        vol = df_clean['Volume'].values
        vol_sma = pd.Series(vol).rolling(20, min_periods=1).mean().values
        rel_vol_arr = np.where(vol_sma > 0, vol / vol_sma, 1.0)
        
        c1, c3, c9, c14 = u1[-1], u3[-1], u9[-1], u14[-1]
        cascade = f"{'U' if c1 else 'D'} | {'U' if c3 else 'D'} | {'U' if c9 else 'D'} | {'U' if c14 else 'D'}"
        
        bars_in_regime = 0
        is_bull = c9 and c14
        is_bear = not c9 and not c14
        if is_bull or is_bear:
            for i in range(len(u9)-1, -1, -1):
                if (is_bull and u9[i] and u14[i]) or (is_bear and not u9[i] and not u14[i]): bars_in_regime += 1
                else: break
                
        actual_lookback = lookback if len(df_clean) > lookback else int(len(df_clean)/2)
        range_high = df_clean['High'].rolling(actual_lookback, min_periods=20).max().shift(1).values
        range_low = df_clean['Low'].rolling(actual_lookback, min_periods=20).min().shift(1).values
        
        l_wins, l_loss, s_wins, s_loss = 0, 0, 0, 0
        l_units, s_units = 0.0, 0.0
        
        highs, lows, closes = df_clean['High'].values, df_clean['Low'].values, df_clean['Close'].values
        sl_pct = sl_buffer / 100.0
        
        for i in range(1, len(df_clean)):
            if pd.isna(range_high[i]) or pd.isna(range_low[i]): continue
            c_below = (lows[i] < range_low[i]) or (lows[i-1] < range_low[i-1])
            c_above = (highs[i] > range_high[i]) or (highs[i-1] > range_high[i-1])
            vol_dry = rel_vol_arr[i] < vol_limit
            
            is_spring = c_below and u1[i] and not u1[i-1] and not u9[i] and vol_dry
            is_utad = c_above and not u1[i] and u1[i-1] and u9[i] and vol_dry
            
            if is_spring:
                entry = closes[i]
                sl = min(lows[i], lows[i-1]) * (1.0 - sl_pct)
                tp = range_low[i] + ((range_high[i] - range_low[i]) * 0.5) if tp_target == "50% Mid-Line" else range_high[i]
                sl_dist = abs(entry - sl)
                tp_dist = abs(tp - entry)
                rr = (tp_dist / sl_dist) if sl_dist > 0 else 0
                
                for j in range(i+1, len(df_clean)):
                    if highs[j] >= tp: 
                        l_wins+=1; l_units += rr; break
                    if lows[j] <= sl: 
                        l_loss+=1; l_units -= 1.0; break
            if is_utad:
                entry = closes[i]
                sl = max(highs[i], highs[i-1]) * (1.0 + sl_pct)
                tp = range_high[i] - ((range_high[i] - range_low[i]) * 0.5) if tp_target == "50% Mid-Line" else range_low[i]
                sl_dist = abs(sl - entry)
                tp_dist = abs(entry - tp)
                rr = (tp_dist / sl_dist) if sl_dist > 0 else 0
                
                for j in range(i+1, len(df_clean)):
                    if lows[j] <= tp: 
                        s_wins+=1; s_units += rr; break
                    if highs[j] >= sl: 
                        s_loss+=1; s_units -= 1.0; break
                    
        tot = l_wins + l_loss + s_wins + s_loss
        win_rate = round(((l_wins + s_wins) / tot * 100), 1) if tot > 0 else 0.0
        l_wr = round((l_wins / (l_wins + l_loss) * 100), 1) if (l_wins + l_loss) > 0 else 0.0
        s_wr = round((s_wins / (s_wins + s_loss) * 100), 1) if (s_wins + s_loss) > 0 else 0.0
        micro_flipped = u1[-1] != u1[-2]
        
        return {"Ticker": ticker, "Price": round(float(df_clean['Close'].values[-1]), 2), 
                "Cascade": cascade, "Regime": "BULL" if is_bull else "BEAR" if is_bear else "MIXED", 
                "Bars": bars_in_regime, "Vol": round(float(rel_vol_arr[-1]), 2), 
                "Spark": "FLIPPED!" if micro_flipped else ("HOLDING" if c1 == c9 else "FIGHTING"),
                "WinRate": win_rate, "Long_WR": l_wr, "Short_WR": s_wr, "Trades": tot, "NetUnits": round(l_units + s_units, 2)}
    except: return None

@st.cache_data(ttl=300)
def fetch_and_analyze(tickers, interval, period, sl_buffer, tp_target, lookback, vol_limit):
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        futures = [executor.submit(process_ticker, t, interval, period, sl_buffer, tp_target, lookback, vol_limit) for t in tickers]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res: results.append(res)
    return pd.DataFrame(results)

# --- CHARTING & BACKTEST ENGINE ---
def render_wyckoff_chart(ticker, interval, period, sl_buffer, tp_target, lookback, vol_limit):
    df = yf.download(ticker, period=period, interval=interval, progress=False)
    if df.empty: 
        st.error(f"No data found for {ticker} on {interval} timeframe.")
        return
    dates = df.index
    df_clean = pd.DataFrame({'Open': safe_col(df, 'Open'), 'High': safe_col(df, 'High'), 'Low': safe_col(df, 'Low'), 'Close': safe_col(df, 'Close'), 'Volume': safe_col(df, 'Volume')})
    
    u1, line1 = get_supertrend(df_clean, 1, 1.0)
    u3, line3 = get_supertrend(df_clean, 3, 3.0)
    u9, line9 = get_supertrend(df_clean, 9, 9.0)
    u14, line14 = get_supertrend(df_clean, 14, 14.0)
    
    actual_lookback = lookback if len(df_clean) > lookback else int(len(df_clean)/2)
    df_clean['Range_High'] = df_clean['High'].rolling(actual_lookback, min_periods=20).max().shift(1)
    df_clean['Range_Low'] = df_clean['Low'].rolling(actual_lookback, min_periods=20).min().shift(1)
    
    vol_sma = pd.Series(df_clean['Volume']).rolling(20, min_periods=1).mean()
    rel_vol = df_clean['Volume'] / vol_sma
    vol_colors = ['#FFD700' if rv < vol_limit else '#555555' for rv in rel_vol]
    
    spring_x, spring_y, utad_x, utad_y = [], [], [], []
    l_wins, l_loss, s_wins, s_loss = 0, 0, 0, 0
    l_units, s_units = 0.0, 0.0
    sl_pct = sl_buffer / 100.0
    
    for i in range(1, len(df_clean)):
        is_below = df_clean['Low'].iloc[i] < df_clean['Range_Low'].iloc[i] or df_clean['Low'].iloc[i-1] < df_clean['Range_Low'].iloc[i-1]
        is_above = df_clean['High'].iloc[i] > df_clean['Range_High'].iloc[i] or df_clean['High'].iloc[i-1] > df_clean['Range_High'].iloc[i-1]
        
        is_spring = is_below and u1[i] and not u1[i-1] and not u9[i] and rel_vol.iloc[i] < vol_limit
        is_utad = is_above and not u1[i] and u1[i-1] and u9[i] and rel_vol.iloc[i] < vol_limit
        
        if is_spring:
            spring_x.append(dates[i]); spring_y.append(df_clean['Low'].iloc[i] * 0.99)
            entry = df_clean['Close'].iloc[i]
            sl = min(df_clean['Low'].iloc[i], df_clean['Low'].iloc[i-1]) * (1.0 - sl_pct)
            tp = df_clean['Range_Low'].iloc[i] + ((df_clean['Range_High'].iloc[i] - df_clean['Range_Low'].iloc[i]) * 0.5) if tp_target == "50% Mid-Line" else df_clean['Range_High'].iloc[i]
            sl_dist = abs(entry - sl)
            tp_dist = abs(tp - entry)
            rr = (tp_dist / sl_dist) if sl_dist > 0 else 0
            
            for j in range(i+1, len(df_clean)):
                if df_clean['High'].iloc[j] >= tp: 
                    l_wins += 1; l_units += rr; break
                if df_clean['Low'].iloc[j] <= sl: 
                    l_loss += 1; l_units -= 1.0; break
                
        if is_utad:
            utad_x.append(dates[i]); utad_y.append(df_clean['High'].iloc[i] * 1.01)
            entry = df_clean['Close'].iloc[i]
            sl = max(df_clean['High'].iloc[i], df_clean['High'].iloc[i-1]) * (1.0 + sl_pct)
            tp = df_clean['Range_High'].iloc[i] - ((df_clean['Range_High'].iloc[i] - df_clean['Range_Low'].iloc[i]) * 0.5) if tp_target == "50% Mid-Line" else df_clean['Range_Low'].iloc[i]
            sl_dist = abs(sl - entry)
            tp_dist = abs(entry - tp)
            rr = (tp_dist / sl_dist) if sl_dist > 0 else 0
            
            for j in range(i+1, len(df_clean)):
                if df_clean['Low'].iloc[j] <= tp: 
                    s_wins += 1; s_units += rr; break
                if df_clean['High'].iloc[j] >= sl: 
                    s_loss += 1; s_units -= 1.0; break

    live_rec = {"action": "NEUTRAL", "color": TV_TEXT, "entry": 0, "sl": 0, "tp": 0, "msg": "Regime building cause..."}
    curr = len(df_clean) - 1
    if curr > 0:
        c_below = df_clean['Low'].iloc[curr] < df_clean['Range_Low'].iloc[curr] or df_clean['Low'].iloc[curr-1] < df_clean['Range_Low'].iloc[curr-1]
        c_above = df_clean['High'].iloc[curr] > df_clean['Range_High'].iloc[curr] or df_clean['High'].iloc[curr-1] > df_clean['Range_High'].iloc[curr-1]
        c_spring = c_below and u1[curr] and not u1[curr-1] and not u9[curr] and rel_vol.iloc[curr] < vol_limit
        c_utad = c_above and not u1[curr] and u1[curr-1] and u9[curr] and rel_vol.iloc[curr] < vol_limit
        bars_in_regime = 21 if ((u9[curr] and u14[curr]) or (not u9[curr] and not u14[curr])) else 0
            
        if c_spring:
            live_rec = {"action": "LONG (SPRING)", "color": TV_GREEN, "entry": df_clean['Close'].iloc[curr], 
                        "sl": min(df_clean['Low'].iloc[curr], df_clean['Low'].iloc[curr-1]) * (1.0 - sl_pct), 
                        "tp": df_clean['Range_Low'].iloc[curr] + ((df_clean['Range_High'].iloc[curr] - df_clean['Range_Low'].iloc[curr]) * 0.5) if tp_target == "50% Mid-Line" else df_clean['Range_High'].iloc[curr],
                        "msg": f"Buy signal triggered. Setup confirmed."}
        elif c_utad:
            live_rec = {"action": "SHORT (UTAD)", "color": TV_RED, "entry": df_clean['Close'].iloc[curr], 
                        "sl": max(df_clean['High'].iloc[curr], df_clean['High'].iloc[curr-1]) * (1.0 + sl_pct), 
                        "tp": df_clean['Range_High'].iloc[curr] - ((df_clean['Range_High'].iloc[curr] - df_clean['Range_Low'].iloc[curr]) * 0.5) if tp_target == "50% Mid-Line" else df_clean['Range_Low'].iloc[curr],
                        "msg": f"Sell signal triggered. Setup confirmed."}
        elif bars_in_regime >= 20:
            live_rec = {"action": "ARMED", "color": "#FFD700", "entry": 0, "sl": 0, "tp": 0, "msg": "Waiting for Micro Spark to flip."}

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
        
    add_st(u1, line1, width=1, opacity=0.9)                 # Micro
    add_st(u3, line3, width=1, opacity=0.5, dash='dot')     # Structural
    add_st(u9, line9, width=2, opacity=0.8)                 # Macro
    add_st(u14, line14, width=3, opacity=1.0)               # Anchor
    fig.add_trace(go.Bar(x=dates, y=df_clean['Volume'], marker_color=vol_colors, name="Volume"), row=2, col=1)
    c1, c3, c9, c14 = u1[-1], u3[-1], u9[-1], u14[-1]
    cascade = f"{'U' if c1 else 'D'} | {'U' if c3 else 'D'} | {'U' if c9 else 'D'} | {'U' if c14 else 'D'}"
    rangebreaks = [dict(bounds=["sat", "mon"])]
    if interval in ["5m", "15m", "1h"]: rangebreaks.append(dict(bounds=[16, 9.5], pattern="hour"))
    fig.update_layout(
        title=dict(text=f"<b>{ticker.upper()} ({interval})</b> <span style='font-size: 14px; color: {TV_TEXT};'>&nbsp;&nbsp; Wyckoff Matrix: [{cascade}] &nbsp;&nbsp; RV: {round(float(rel_vol.iloc[-1]), 2)}x</span>", font=dict(size=24, color='#FFFFFF')),
        template="plotly_dark", xaxis_rangeslider_visible=False, height=850, margin=dict(l=50, r=20, t=60, b=20),
        paper_bgcolor=TV_BG, plot_bgcolor=TV_BG, showlegend=False
    )
    
    end_date = dates[-1]
    if interval == "5m": start_date = dates[-min(len(dates), 200)]
    elif interval == "15m": start_date = dates[-min(len(dates), 250)]
    elif interval == "1h": start_date = dates[-min(len(dates), 150)]
    else: start_date = dates[0]
    
    fig.update_xaxes(
        showgrid=True, gridwidth=1, gridcolor=TV_GRID, rangebreaks=rangebreaks,
        range=[start_date, end_date],
        rangeselector=dict(
            buttons=list([
                dict(count=1, label="1D", step="day", stepmode="backward"),
                dict(count=3, label="3D", step="day", stepmode="backward"),
                dict(count=7, label="1W", step="day", stepmode="backward"),
                dict(step="all", label="ALL")
            ]),
            bgcolor=TV_PANEL, activecolor=TV_GREEN, font=dict(color="white"), y=1.02
        )
    )
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor=TV_GRID, tickprefix="$")
    
    col_chart, col_intel = st.columns([4, 1.2])
    with col_chart: st.plotly_chart(fig, use_container_width=True)
    with col_intel:
        st.markdown("<h3 style='margin-bottom: 5px;'>🤖 Live Intel</h3>", unsafe_allow_html=True)
        
        tot_longs = l_wins + l_loss
        tot_shorts = s_wins + s_loss
        long_wr = (l_wins / tot_longs * 100) if tot_longs > 0 else 0
        short_wr = (s_wins / tot_shorts * 100) if tot_shorts > 0 else 0
        
        bias_action = "NEUTRAL (Balanced Edge)"
        bias_color = TV_TEXT
        if short_wr >= long_wr + 20 and tot_shorts >= 2:
            bias_action = "SHORT ONLY"
            bias_color = TV_RED
            bias_msg = "Historically fails to hold Springs. Only trade UTADs."
        elif long_wr >= short_wr + 20 and tot_longs >= 2:
            bias_action = "LONG ONLY"
            bias_color = TV_GREEN
            bias_msg = "Naturally drifts upward. Shorting UTADs is dangerous."
        else:
            bias_msg = "Symmetrical win rates. Safe to trade in both directions."
            
        st.markdown(f"""
        <div class="intel-card" style='border: 1px solid {bias_color};'>
            <p style='color: #888; font-size: 12px; margin:0;'>ASSET PERSONALITY</p>
            <h3 style='color: {bias_color}; margin-top: 0; margin-bottom: 5px;'>{bias_action}</h3>
            <p style='font-size: 11px; margin-bottom: 0px;'>{bias_msg}</p>
        </div>
        """, unsafe_allow_html=True)
        
        st.markdown(f"""
        <div class="intel-card">
            <p style='color: #888; font-size: 12px; margin:0;'>CURRENT ACTION</p>
            <h2 style='color: {live_rec["color"]}; margin-top: 0;'>{live_rec["action"]}</h2>
            <p style='margin-bottom: 5px;'>{live_rec["msg"]}</p>
        </div>
        """, unsafe_allow_html=True)
        
        tot_units = l_units + s_units
        st.markdown(f"""
<div class="intel-card">
<p style='color: #888; font-size: 12px; margin:0;'>STRATEGY PERFORMANCE</p>
<p style='font-size: 11px; color: #666; margin-bottom: 12px;'>Historical 4-Pillar setups on this timeframe.</p>
<div style='display: flex; justify-content: space-between;'><span>LONG (Springs):</span> <strong style='color:{TV_GREEN if long_wr >= 50 else TV_TEXT};'>{long_wr:.1f}% Win</strong></div>
<div style='display: flex; justify-content: space-between; font-size: 11px; color: #888; margin-bottom: 8px;'><span>Gain/Loss:</span> <strong style='color:{TV_GREEN if l_units > 0 else TV_RED if l_units < 0 else TV_TEXT};'>{l_units:+.2f}R Units</strong></div>
<div style='display: flex; justify-content: space-between;'><span>SHORT (UTADs):</span> <strong style='color:{TV_RED if short_wr >= 50 else TV_TEXT};'>{short_wr:.1f}% Win</strong></div>
<div style='display: flex; justify-content: space-between; font-size: 11px; color: #888; margin-bottom: 8px;'><span>Gain/Loss:</span> <strong style='color:{TV_GREEN if s_units > 0 else TV_RED if s_units < 0 else TV_TEXT};'>{s_units:+.2f}R Units</strong></div>
<hr style='border-color: {TV_GRID}; margin: 8px 0;'>
<div style='display: flex; justify-content: space-between;'><span>Total L/S Setups:</span> <strong>{tot_longs} / {tot_shorts}</strong></div>
<div style='display: flex; justify-content: space-between;'><span>Total Net Profit:</span> <strong style='color:{TV_GREEN if tot_units > 0 else TV_RED if tot_units < 0 else TV_TEXT}; font-size: 16px;'>{tot_units:+.2f}R</strong></div>
</div>
""", unsafe_allow_html=True)

# --- MAIN UI ---
st.title("TradingView | Wyckoff Terminal")

def update_search():
    st.session_state['selected_ticker'] = st.session_state['search_input'].upper()

col_tk, col_tf, col_btn = st.columns([2, 1, 5])
with col_tk: st.text_input("🔍 Ticker:", value=st.session_state['selected_ticker'], key="search_input", on_change=update_search)
with col_tf: timeframe = st.selectbox("⏱️ Timeframe:", ["5m", "15m", "1h", "1d", "1wk"], index=3)

if timeframe in ["1d", "1wk"]: dl_period = "2y"
elif timeframe in ["1h"]: dl_period = "730d"
else: dl_period = "60d"

# Sidebar
with st.sidebar:
    
    st.markdown("<div class='opt-btn'>", unsafe_allow_html=True)
    if st.button("🧠 Auto-Tune for Timeframe", help="AI recommended optimal settings based on current timeframe"):
        if timeframe in ["5m", "15m"]:
            st.session_state.lb_val = 200
            st.session_state.sl_val = 1.0
            st.session_state.tp_val = "50% Mid-Line"
            st.session_state.vol_val = 1.2
        elif timeframe == "1h":
            st.session_state.lb_val = 150
            st.session_state.sl_val = 0.5
            st.session_state.tp_val = "50% Mid-Line"
            st.session_state.vol_val = 1.0
        else:
            st.session_state.lb_val = 100
            st.session_state.sl_val = 0.2
            st.session_state.tp_val = "Full Phase B Range"
            st.session_state.vol_val = 0.8
        st.rerun()
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("### 🧬 Algo Tuning")
    
    def on_slider_change():
        pass

    algo_lookback = st.slider("Phase B Lookback (Bars)", min_value=20, max_value=300, value=st.session_state.lb_val, step=10, key='lb_val', on_change=on_slider_change)
    algo_vol = st.slider("Vol Exhaustion Threshold", min_value=0.3, max_value=2.0, value=st.session_state.vol_val, step=0.1, key='vol_val', on_change=on_slider_change)

    st.markdown("### ⚙️ Backtest Risk Rules")
    sl_buffer = st.slider("Stop-Loss Buffer (%)", min_value=0.1, max_value=3.0, value=st.session_state.sl_val, step=0.1, key='sl_val', on_change=on_slider_change)
    tp_target = st.radio("Take Profit Target", ["Full Phase B Range", "50% Mid-Line"], index=0 if st.session_state.tp_val == "Full Phase B Range" else 1, key='tp_val', on_change=on_slider_change)
    
    st.markdown("### 📋 Market Radar")
    watchlist_choice = st.selectbox("Watchlist Profile:", ["Top 20 Mega-Cap Tech", "S&P 500 (Massive Sweep)"], index=0)
    sort_by = st.radio("Sort By:", ["Exhaustion (Bars)", "Historical Win Rate (%)", "Net Profit (Units)"], horizontal=True)
    
    if watchlist_choice == "S&P 500 (Massive Sweep)": tickers_to_scan = get_sp500_tickers()
    else: tickers_to_scan = ["AAPL","MSFT","NVDA","AMZN","META","GOOGL","TSLA","BRK-B","LLY","AVGO","JPM","V","UNH","MA","PG","COST","HD","MRK","ABBV","CVX"]
    
    if st.button("🔄 Rescan Market"):
        with st.spinner(f"Initiating {len(tickers_to_scan)} Ticker Sweep..."):
            st.session_state['scan_data'] = fetch_and_analyze(tickers_to_scan, timeframe, dl_period, sl_buffer, tp_target, algo_lookback, algo_vol)
        
    if 'scan_data' not in st.session_state or st.session_state.get('last_tf') != timeframe or st.session_state.get('last_wl') != watchlist_choice or st.session_state.get('last_sl') != sl_buffer or st.session_state.get('last_tp') != tp_target or st.session_state.get('last_lb') != algo_lookback or st.session_state.get('last_vol') != algo_vol:
        with st.spinner(f"Initiating {len(tickers_to_scan)} Ticker Sweep..."):
            st.session_state['scan_data'] = fetch_and_analyze(tickers_to_scan, timeframe, dl_period, sl_buffer, tp_target, algo_lookback, algo_vol)
            st.session_state['last_tf'] = timeframe
            st.session_state['last_wl'] = watchlist_choice
            st.session_state['last_sl'] = sl_buffer
            st.session_state['last_tp'] = tp_target
            st.session_state['last_lb'] = algo_lookback
            st.session_state['last_vol'] = algo_vol
        
    df_res = st.session_state['scan_data']
    if df_res is not None and not df_res.empty:
        if sort_by == "Historical Win Rate (%)": df_res = df_res.sort_values(by=["WinRate", "Trades"], ascending=[False, False])
        elif sort_by == "Net Profit (Units)": df_res = df_res.sort_values(by="NetUnits", ascending=False)
        else: df_res = df_res.sort_values(by="Bars", ascending=False)
            
        for _, row in df_res.iterrows():
            color = TV_GREEN if row['Regime'] == 'BULL' else TV_RED if row['Regime'] == 'BEAR' else TV_TEXT
            net_u = row.get("NetUnits", 0)
            u_color = TV_GREEN if net_u > 0 else TV_RED if net_u < 0 else TV_TEXT
            
            c1, c2 = st.columns([1.2, 2])
            with c1:
                if st.button(f"{row['Ticker']}", key=f"btn_{row['Ticker']}_{timeframe}_{watchlist_choice}_{sl_buffer}_{tp_target}_{algo_lookback}_{algo_vol}"):
                    st.session_state['selected_ticker'] = row['Ticker']
                    st.rerun()
            with c2:
                st.markdown(f"<div style='line-height: 1.2; margin-top: 5px;'>"
                            f"<span style='color: {color}; font-size: 14px;'>{row['Regime']} ({row['Bars']})</span><br>"
                            f"<span style='font-size: 11px; color: #888;'>Net: <strong style='color:{u_color};'>{net_u:+.1f}R</strong> | <strong style='color:{TV_GREEN};'>L:{row['Long_WR']}%</strong> / <strong style='color:{TV_RED};'>S:{row['Short_WR']}%</strong></span>"
                            f"</div>", unsafe_allow_html=True)
            st.markdown(f"<hr style='margin: 5px 0px; border-color: {TV_GRID};'>", unsafe_allow_html=True)

# =============================================================================
# MULTI-PAGE UPGRADE: Sector Heatmap, Multi-TF Confluence, Options Flow, Trade Log
# =============================================================================
try:
    from sector_data import get_sector, get_all_sectors, get_sector_summary, get_tickers_by_sector
    SECTOR_AVAILABLE = True
except: SECTOR_AVAILABLE = False

try:
    from mtf_confluence import scan_confluence
    MTF_AVAILABLE = True
except: MTF_AVAILABLE = False

try:
    from options_flow import get_options_flow
    OPTIONS_AVAILABLE = True
except: OPTIONS_AVAILABLE = False

try:
    from trade_tracker import get_stats, get_recent_trades
    TRACKER_AVAILABLE = True
except: TRACKER_AVAILABLE = False

# --- TABS ---
tab_scanner, tab_sector, tab_mtf, tab_options, tab_trades = st.tabs([
    "Scanner", "Sector Heatmap", "Multi-TF Confluence", "Options Flow", "Trade Log"
])

with tab_scanner:
    render_wyckoff_chart(st.session_state['selected_ticker'], timeframe, dl_period, sl_buffer, tp_target, algo_lookback, algo_vol)

with tab_sector:
    st.markdown("## Sector Rotation Heatmap")
    if not SECTOR_AVAILABLE:
        st.warning("sector_data.py module not found.")
    elif 'scan_data' in st.session_state and st.session_state['scan_data'] is not None:
        scan_df = st.session_state['scan_data']
        results_for_sector = []
        for _, row in scan_df.iterrows():
            results_for_sector.append({
                'ticker': row['Ticker'],
                'regime': row['Regime'],
                'exhaustion_bars': row['Bars'],
            })
        sector_summary = get_sector_summary(results_for_sector)
        
        cols = st.columns(3)
        idx = 0
        for sector_name in sorted(sector_summary.keys()):
            s = sector_summary[sector_name]
            with cols[idx % 3]:
                regime_color = TV_GREEN if s['dominant_regime'] == 'BULL' else TV_RED if s['dominant_regime'] == 'BEAR' else "#FFD600"
                st.markdown(f"""
                <div style="background-color: {TV_PANEL}; border-left: 4px solid {regime_color}; padding: 12px; margin: 6px 0; border-radius: 4px;">
                    <div style="font-size: 14px; font-weight: bold; color: white;">{sector_name}</div>
                    <div style="font-size: 12px; color: #888; margin-top: 4px;">
                        Stocks: {s['count']} | Avg Exhaust: {s['avg_exhaustion']:.0f} bars<br>
                        Regime: <span style="color: {regime_color}; font-weight: bold;">{s['dominant_regime']}</span>
                    </div>
                    <div style="font-size: 11px; color: #aaa; margin-top: 6px;">
                        Top Exhausted: {', '.join([f"{t[0]} ({t[1]})" for t in s.get('top_exhausted', [])[:3]])}
                    </div>
                </div>
                """, unsafe_allow_html=True)
            idx += 1
    else:
        st.info("Run a Market Radar scan first to populate sector data.")

with tab_mtf:
    st.markdown("## Multi-Timeframe Confluence Scanner")
    if not MTF_AVAILABLE:
        st.warning("mtf_confluence.py module not found.")
    else:
        mtf_tickers = st.text_input("Tickers (comma-separated)", value="AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, JPM, AVGO")
        if st.button("Scan Confluence"):
            ticker_list = [t.strip().upper() for t in mtf_tickers.split(",") if t.strip()]
            with st.spinner(f"Scanning {len(ticker_list)} tickers across Daily + Intraday..."):
                mtf_results = scan_confluence(ticker_list)
                st.session_state['mtf_results'] = mtf_results
        
        if 'mtf_results' in st.session_state and st.session_state['mtf_results']:
            for r in st.session_state['mtf_results']:
                score = r['confluence_score']
                score_color = TV_GREEN if score >= 75 else "#FFD600" if score >= 50 else TV_RED
                rev_color = TV_GREEN if r['reversal_quality'] == 'HIGH' else "#FFD600" if r['reversal_quality'] == 'MEDIUM' else "#888"
                
                c1, c2, c3, c4 = st.columns([1, 1.5, 1.5, 1])
                with c1:
                    st.markdown(f"<div style='font-size: 18px; font-weight: bold; color: white; padding: 8px;'>{r['ticker']}</div>", unsafe_allow_html=True)
                with c2:
                    daily_col = TV_GREEN if r['daily_regime'] == 'BULL' else TV_RED if r['daily_regime'] == 'BEAR' else "#FFD600"
                    intra_col = TV_GREEN if r['intraday_regime'] == 'BULL' else TV_RED if r['intraday_regime'] == 'BEAR' else "#FFD600"
                    st.markdown(f"<div style='padding: 8px;'>Daily: <span style='color:{daily_col}; font-weight:bold;'>{r['daily_regime']}</span> | 5m: <span style='color:{intra_col}; font-weight:bold;'>{r['intraday_regime']}</span></div>", unsafe_allow_html=True)
                with c3:
                    st.markdown(f"<div style='padding: 8px;'>Score: <span style='color:{score_color}; font-weight:bold; font-size: 18px;'>{score}/100</span> ({r['confluence_label']})</div>", unsafe_allow_html=True)
                with c4:
                    st.markdown(f"<div style='padding: 8px;'>Reversal: <span style='color:{rev_color}; font-weight:bold;'>{r['reversal_quality']}</span></div>", unsafe_allow_html=True)
                st.markdown(f"<hr style='margin: 2px 0; border-color: {TV_GRID};'>", unsafe_allow_html=True)

with tab_options:
    st.markdown("## Options Flow Detector")
    if not OPTIONS_AVAILABLE:
        st.warning("options_flow.py module not found.")
    else:
        opt_ticker = st.text_input("Ticker Symbol", value=st.session_state.get('selected_ticker', 'AAPL'), key='opt_ticker_input')
        if st.button("Scan Options Flow"):
            with st.spinner(f"Fetching {opt_ticker} options chain..."):
                flow = get_options_flow(opt_ticker.strip().upper())
                st.session_state['options_flow'] = flow
        
        if 'options_flow' in st.session_state and st.session_state['options_flow']:
            f = st.session_state['options_flow']
            
            # Main metrics
            mc1, mc2, mc3, mc4 = st.columns(4)
            pcr_color = TV_RED if f['put_call_label'] == 'BEARISH SKEW' else TV_GREEN if f['put_call_label'] == 'BULLISH SKEW' else "#FFD600"
            sent_color = TV_GREEN if 'BULLISH' in f['net_sentiment'] else TV_RED if 'BEARISH' in f['net_sentiment'] else "#FFD600"
            
            with mc1:
                st.markdown(f"""<div class='intel-card'>
                    <div style='font-size: 11px; color: #888;'>PUT/CALL RATIO</div>
                    <div style='font-size: 24px; font-weight: bold; color: {pcr_color};'>{f['put_call_ratio']}</div>
                    <div style='font-size: 12px; color: {pcr_color};'>{f['put_call_label']}</div>
                </div>""", unsafe_allow_html=True)
            with mc2:
                st.markdown(f"""<div class='intel-card'>
                    <div style='font-size: 11px; color: #888;'>MAX PAIN</div>
                    <div style='font-size: 24px; font-weight: bold; color: white;'>${f['max_pain']:,.2f}</div>
                    <div style='font-size: 12px; color: #888;'>Exp: {f['nearest_expiry']}</div>
                </div>""", unsafe_allow_html=True)
            with mc3:
                st.markdown(f"""<div class='intel-card'>
                    <div style='font-size: 11px; color: #888;'>GAMMA WALL</div>
                    <div style='font-size: 24px; font-weight: bold; color: #AB47BC;'>${f['gamma_wall']:,.2f}</div>
                    <div style='font-size: 12px; color: #888;'>Price Magnet</div>
                </div>""", unsafe_allow_html=True)
            with mc4:
                st.markdown(f"""<div class='intel-card'>
                    <div style='font-size: 11px; color: #888;'>NET SENTIMENT</div>
                    <div style='font-size: 24px; font-weight: bold; color: {sent_color};'>{f['net_sentiment']}</div>
                    <div style='font-size: 12px; color: #888;'>Call OI: {f['total_call_oi']:,} | Put OI: {f['total_put_oi']:,}</div>
                </div>""", unsafe_allow_html=True)
            
            # Unusual activity tables
            uc1, uc2 = st.columns(2)
            with uc1:
                st.markdown(f"<div style='color: {TV_GREEN}; font-weight: bold;'>Unusual Call Activity ({len(f['unusual_calls'])} strikes)</div>", unsafe_allow_html=True)
                if f['unusual_calls']:
                    call_df = pd.DataFrame(f['unusual_calls'])
                    st.dataframe(call_df, use_container_width=True, hide_index=True)
                else:
                    st.caption("No unusual call volume detected.")
            with uc2:
                st.markdown(f"<div style='color: {TV_RED}; font-weight: bold;'>Unusual Put Activity ({len(f['unusual_puts'])} strikes)</div>", unsafe_allow_html=True)
                if f['unusual_puts']:
                    put_df = pd.DataFrame(f['unusual_puts'])
                    st.dataframe(put_df, use_container_width=True, hide_index=True)
                else:
                    st.caption("No unusual put volume detected.")

with tab_trades:
    st.markdown("## Trade Performance Tracker")
    if not TRACKER_AVAILABLE:
        st.warning("trade_tracker.py module not found. The alert bot will automatically populate trades here.")
    else:
        stats = get_stats()
        
        sc1, sc2, sc3, sc4 = st.columns(4)
        wr_color = TV_GREEN if stats['win_rate'] >= 50 else TV_RED
        pnl_color = TV_GREEN if stats['avg_pnl_r'] >= 0 else TV_RED
        
        with sc1:
            st.markdown(f"""<div class='intel-card'>
                <div style='font-size: 11px; color: #888;'>TOTAL TRADES</div>
                <div style='font-size: 28px; font-weight: bold; color: white;'>{stats['total_trades']}</div>
                <div style='font-size: 12px; color: #888;'>Open: {stats['open_count']}</div>
            </div>""", unsafe_allow_html=True)
        with sc2:
            st.markdown(f"""<div class='intel-card'>
                <div style='font-size: 11px; color: #888;'>WIN RATE</div>
                <div style='font-size: 28px; font-weight: bold; color: {wr_color};'>{stats['win_rate']:.1f}%</div>
                <div style='font-size: 12px; color: #888;'>W: {stats['wins']} | L: {stats['losses']}</div>
            </div>""", unsafe_allow_html=True)
        with sc3:
            st.markdown(f"""<div class='intel-card'>
                <div style='font-size: 11px; color: #888;'>AVG PnL (R)</div>
                <div style='font-size: 28px; font-weight: bold; color: {pnl_color};'>{stats['avg_pnl_r']:+.2f}R</div>
                <div style='font-size: 12px; color: #888;'>Per Trade</div>
            </div>""", unsafe_allow_html=True)
        with sc4:
            best = stats.get('best_trade', ('N/A', 0))
            worst = stats.get('worst_trade', ('N/A', 0))
            st.markdown(f"""<div class='intel-card'>
                <div style='font-size: 11px; color: #888;'>BEST / WORST</div>
                <div style='font-size: 14px; color: {TV_GREEN};'>Best: {best[0]} ({best[1]:+.1f}R)</div>
                <div style='font-size: 14px; color: {TV_RED};'>Worst: {worst[0]} ({worst[1]:+.1f}R)</div>
            </div>""", unsafe_allow_html=True)
        
        recent = get_recent_trades(20)
        if recent:
            st.markdown("### Recent Trades")
            trade_df = pd.DataFrame(recent)
            st.dataframe(trade_df, use_container_width=True, hide_index=True)
        else:
            st.info("No trades logged yet. The alert bot will automatically record trades as signals fire.")

