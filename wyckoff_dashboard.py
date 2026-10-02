import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import concurrent.futures
import datetime
import os
import json

# --- Constants & Themes ---
TV_BG = "#131722"
TV_PANEL = "#2A2E39" 
TV_TEXT = "#D1D4DC"
TV_GRID = "#1E222D"
TV_GREEN = "#089981"
TV_RED = "#F23645"

TOP_20_TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'BRK-B', 'TSLA', 'LLY', 'V', 
                  'UNH', 'JPM', 'JNJ', 'XOM', 'WMT', 'MA', 'PG', 'AVGO', 'HD', 'CVX']

st.set_page_config(page_title="Wyckoff Matrix", layout="wide", initial_sidebar_state="expanded")

# Custom CSS
st.markdown(f"""
<style>
.stApp {{ background-color: {TV_BG}; color: {TV_TEXT}; }}
.stSelectbox div[data-baseweb="select"] {{ background-color: {TV_PANEL}; color: {TV_TEXT}; }}
.stTextInput input {{ background-color: {TV_PANEL}; color: {TV_TEXT}; }}
h1, h2, h3, h4, h5, h6, p, span, div {{ color: {TV_TEXT}; }}
.metric-card {{
    background-color: {TV_PANEL};
    padding: 15px;
    border-radius: 8px;
    border: 1px solid {TV_GRID};
    margin-bottom: 15px;
}}
.trade-card {{
    background-color: {TV_PANEL};
    padding: 20px;
    border-radius: 10px;
    border-left: 5px solid {TV_GREEN};
    margin-bottom: 15px;
}}
.trade-card.short {{ border-left-color: {TV_RED}; }}
.trade-card.stalking {{ border-left-color: #E6A23C; }}
</style>
""", unsafe_allow_html=True)

# --- Imports ---
try:
    from trade_tracker import get_stats, get_recent_trades, init_db
except ImportError:
    def get_stats(): return {"total": 0, "win_rate": 0, "avg_pnl": 0, "open": 0}
    def get_recent_trades(limit=50): return pd.DataFrame()
    def init_db(): pass

try:
    import sector_data
except ImportError:
    sector_data = None

try:
    import mtf_confluence
except ImportError:
    mtf_confluence = None

try:
    import options_flow
except ImportError:
    options_flow = None

# Init State
init_db()
if 'selected_ticker' not in st.session_state:
    st.session_state.selected_ticker = 'SPY'
if 'chart_tf' not in st.session_state:
    st.session_state.chart_tf = '1h'

# --- Indicators ---
def calculate_supertrend(df, period, multiplier):
    if df is None or len(df) < period:
        return df
    
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    # Calculate ATR
    tr1 = pd.DataFrame(high - low)
    tr2 = pd.DataFrame(abs(high - close.shift(1)))
    tr3 = pd.DataFrame(abs(low - close.shift(1)))
    frames = [tr1, tr2, tr3]
    tr = pd.concat(frames, axis=1, join='inner').max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    
    hl2 = (high + low) / 2
    final_upperband = hl2 + (multiplier * atr)
    final_lowerband = hl2 - (multiplier * atr)
    
    supertrend = [0.0] * len(df)
    trend = [0] * len(df)
    
    for i in range(1, len(df)):
        curr_c = close.iloc[i]
        prev_upper = final_upperband.iloc[i-1]
        prev_lower = final_lowerband.iloc[i-1]
        curr_upper = final_upperband.iloc[i]
        curr_lower = final_lowerband.iloc[i]
        
        if curr_c > prev_upper:
            trend[i] = 1
        elif curr_c < prev_lower:
            trend[i] = -1
        else:
            trend[i] = trend[i-1]
            
        if trend[i] == 1 and curr_lower < prev_lower:
            final_lowerband.iloc[i] = prev_lower
        if trend[i] == -1 and curr_upper > prev_upper:
            final_upperband.iloc[i] = prev_upper
            
        if trend[i] == 1:
            supertrend[i] = final_lowerband.iloc[i]
        else:
            supertrend[i] = final_upperband.iloc[i]
            
    df_out = df.copy()
    df_out[f'ST_{period}_{multiplier}'] = supertrend
    df_out[f'ST_DIR_{period}_{multiplier}'] = trend
    
    # Calculate Bars in Regime
    bars_in_regime = []
    current_trend = trend[0]
    count = 0
    for t in trend:
        if t == current_trend:
            count += 1
        else:
            current_trend = t
            count = 1
        bars_in_regime.append(count)
        
    df_out[f'BARS_IN_REGIME_{period}_{multiplier}'] = bars_in_regime
    return df_out

# --- Data Fetching ---
@st.cache_data(ttl=300)
def fetch_data(ticker, period='6mo', interval='1d'):
    try:
        df = yf.download(ticker, period=period, interval=interval, progress=False)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)
        return df
    except Exception as e:
        return None

# --- Scanning Logic ---
def scan_ticker(ticker):
    df = fetch_data(ticker, period='1y', interval='1d')
    if df is None or len(df) < 50:
        return None
        
    # Calc SuperTrends
    df = calculate_supertrend(df, 1, 1)
    df = calculate_supertrend(df, 3, 3)
    df = calculate_supertrend(df, 9, 9)
    df = calculate_supertrend(df, 14, 14)
    
    last_row = df.iloc[-1]
    
    # Volume relative to 20sma
    df['Vol_SMA'] = df['Volume'].rolling(20).mean()
    if df['Vol_SMA'].iloc[-1] > 0:
        rel_vol = last_row['Volume'] / df['Vol_SMA'].iloc[-1]
    else:
        rel_vol = 1.0
    
    # Base stats
    dir_14 = last_row['ST_DIR_14_14']
    dir_9 = last_row['ST_DIR_9_9']
    dir_3 = last_row['ST_DIR_3_3']
    dir_1 = last_row['ST_DIR_1_1']
    
    bars_regime_14 = last_row['BARS_IN_REGIME_14_14']
    
    # Determine Condition & ML Score
    score = 0
    condition = "STALKING"
    
    # ML Scoring system
    score += min(40, bars_regime_14 * 1.5)
    
    # Vol exhaustion
    if rel_vol < 1.0:
        score += (1.0 - rel_vol) * 50
        
    # Alignment
    if dir_14 == dir_9 == dir_3 == dir_1:
        score += 30
    elif dir_14 == dir_9 == dir_3:
        score += 15
        
    if dir_14 == 1:
        if dir_1 == 1:
            condition = "LONG (SPRING)"
        else:
            condition = "STALKING"
    else:
        if dir_1 == -1:
            condition = "SHORT (UTAD)"
        else:
            condition = "STALKING"
            
    reason = f"{int(bars_regime_14)}-bar {'bullish' if dir_14==1 else 'bearish'} regime, vol {rel_vol:.1f}x, ST alignment."
    
    return {
        'Ticker': ticker,
        'Price': last_row['Close'],
        'Direction': condition,
        'ML_Confidence': min(100, int(score)),
        'Entry': last_row['Close'],
        'Stop': last_row['ST_14_14'],
        'Target': last_row['Close'] * (1.1 if dir_14==1 else 0.9),
        'Reason': reason,
        'Bars_in_Regime': bars_regime_14
    }

@st.cache_data(ttl=300)
def scan_market():
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(scan_ticker, t): t for t in TOP_20_TICKERS}
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
                
    return sorted(results, key=lambda x: x['ML_Confidence'], reverse=True)

# --- Plotly Chart ---
def render_wyckoff_chart(df, ticker, interval):
    df = calculate_supertrend(df, 3, 3)
    df = calculate_supertrend(df, 14, 14)
    
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, 
                        vertical_spacing=0.03, subplot_titles=(f'{ticker} Price', 'Volume'), 
                        row_width=[0.2, 0.7])
                        
    # Candlestick
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], 
                                 low=df['Low'], close=df['Close'], name='Price',
                                 increasing_line_color=TV_GREEN, decreasing_line_color=TV_RED), 
                  row=1, col=1)
                  
    # SuperTrends
    fig.add_trace(go.Scatter(x=df.index, y=df['ST_14_14'], mode='lines', name='ST(14,14)',
                             line=dict(color='yellow', width=2)), row=1, col=1)
                             
    # Volume
    colors = [TV_GREEN if close >= open_ else TV_RED for close, open_ in zip(df['Close'], df['Open'])]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name='Volume', marker_color=colors), row=2, col=1)
    
    fig.update_layout(
        template='plotly_dark',
        plot_bgcolor=TV_BG,
        paper_bgcolor=TV_BG,
        margin=dict(l=20, r=20, t=40, b=20),
        xaxis_rangeslider_visible=False,
        height=600,
        showlegend=False
    )
    
    fig.update_xaxes(showgrid=True, gridcolor=TV_GRID)
    fig.update_yaxes(showgrid=True, gridcolor=TV_GRID)
    
    return fig

# --- Render Pages ---
def render_trade_ideas():
    st.header("⚡ Trade Ideas Scanner")
    st.markdown("Scans Top 20 Mega-Caps across multi-timeframe SuperTrends and volume exhaustion.")
    
    with st.spinner("Scanning Market..."):
        ideas = scan_market()
        
    if not ideas:
        st.warning("No ideas generated.")
        return
        
    for idea in ideas:
        color_class = "stalking"
        if "LONG" in idea['Direction']: color_class = "long"
        elif "SHORT" in idea['Direction']: color_class = "short"
        
        conf_color = TV_GREEN if idea['ML_Confidence'] > 70 else ("#E6A23C" if idea['ML_Confidence'] > 50 else TV_RED)
        
        html = f"""
        <div class="trade-card {color_class}">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <h3 style="margin:0;">{idea['Ticker']} <span style="font-size:0.6em; color:gray;">@ ${idea['Price']:.2f}</span></h3>
                <span style="background-color:{TV_PANEL}; padding:5px 10px; border-radius:15px; font-weight:bold;">
                    {idea['Direction']}
                </span>
            </div>
            <div style="margin-top:10px;">
                <strong>ML Confidence:</strong> 
                <div style="width:100%; background-color:#333; border-radius:5px; height:10px; margin-top:5px;">
                    <div style="width:{idea['ML_Confidence']}%; background-color:{conf_color}; height:100%; border-radius:5px;"></div>
                </div>
            </div>
            <div style="display:flex; justify-content:space-between; margin-top:15px; font-size:0.9em;">
                <div>🟢 Entry: ${idea['Entry']:.2f}</div>
                <div>🔴 Stop: ${idea['Stop']:.2f}</div>
                <div>🎯 Target: ${idea['Target']:.2f}</div>
            </div>
            <p style="margin-top:15px; font-style:italic; color:#aaa;">🧠 WHY: {idea['Reason']}</p>
        </div>
        """
        st.markdown(html, unsafe_allow_html=True)
        if st.button(f"Chart {idea['Ticker']}", key=f"btn_{idea['Ticker']}"):
            st.session_state.selected_ticker = idea['Ticker']
            st.rerun()

def render_chart_terminal():
    st.header("📈 Chart Terminal")
    
    col1, col2, col3 = st.columns([1, 1, 3])
    with col1:
        ticker = st.text_input("Ticker", value=st.session_state.selected_ticker).upper()
        st.session_state.selected_ticker = ticker
    with col2:
        tf = st.selectbox("Timeframe", ['5m', '15m', '1h', '1d', '1wk'], index=3)
        st.session_state.chart_tf = tf
        
    period_map = {'5m': '5d', '15m': '1mo', '1h': '1mo', '1d': '1y', '1wk': '5y'}
    
    df = fetch_data(ticker, period=period_map.get(tf, '1y'), interval=tf)
    if df is None:
        st.error("Failed to load data for this ticker.")
        return
        
    chart_col, info_col = st.columns([3, 1])
    with chart_col:
        fig = render_wyckoff_chart(df, ticker, tf)
        st.plotly_chart(fig, use_container_width=True)
        
    with info_col:
        st.markdown("### Asset Intel")
        st.markdown(f"**Asset:** {ticker}")
        st.markdown(f"**Current Price:** ${df['Close'].iloc[-1]:.2f}")
        st.markdown(f"**Current Vol:** {int(df['Volume'].iloc[-1]):,}")
        st.markdown("---")
        if options_flow:
            try:
                flow = options_flow.get_options_flow(ticker)
                st.markdown("**Options Flow**")
                st.write(flow)
            except:
                st.markdown("Options Flow: N/A")
        else:
            st.markdown("Options Flow Module Offline")

def render_performance():
    st.header("📊 Performance")
    stats = get_stats()
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Alerts", stats.get('total', 0))
    col2.metric("Win Rate", f"{stats.get('win_rate', 0)}%")
    col3.metric("Avg PnL", f"${stats.get('avg_pnl', 0)}")
    col4.metric("Open Trades", stats.get('open', 0))
    
    st.markdown("### Trade Log")
    trades_df = get_recent_trades(100)
    if not trades_df.empty:
        st.dataframe(trades_df)
        
        if 'pnl' in trades_df.columns:
            trades_df['Cumulative PnL'] = trades_df['pnl'].cumsum()
            st.line_chart(trades_df['Cumulative PnL'])
    else:
        st.info("No trades in database.")

def render_ml_brain():
    st.header("🧠 ML Brain Documentation")
    try:
        with open("algo_documentation.md", "r") as f:
            content = f.read()
        st.markdown(content)
    except FileNotFoundError:
        st.warning("algo_documentation.md not found.")

# --- Main App & Sidebar ---
with st.sidebar:
    st.title("Wyckoff Matrix")
    st.markdown(f"<div style='background:{TV_GREEN}; color:white; padding:5px 10px; border-radius:5px; text-align:center; margin-bottom:20px;'>🤖 ML Managing All Parameters</div>", unsafe_allow_html=True)
    
    st.markdown("### Live Intel")
    if st.session_state.selected_ticker:
        st.info(f"Asset Personality: High Beta / Trend Follower\n\nCurrent Action: Monitoring {st.session_state.selected_ticker}")

    st.markdown("### Market Radar")
    for t in ['SPY', 'QQQ', 'IWM', 'VIX']:
        if st.button(t, use_container_width=True):
            st.session_state.selected_ticker = t
            st.rerun()
            
    st.markdown("---")
    if st.button("🔄 Rescan Market", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

tab1, tab2, tab3, tab4 = st.tabs(["Trade Ideas", "Chart Terminal", "Performance", "ML Brain"])

with tab1:
    render_trade_ideas()
with tab2:
    render_chart_terminal()
with tab3:
    render_performance()
with tab4:
    render_ml_brain()
