import sqlite3
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime
import os
import sys

SECTOR_MAP = {
    'AAPL': 'XLK', 'MSFT': 'XLK', 'NVDA': 'XLK', 'AMD': 'XLK', 'AVGO': 'XLK',
    'CRM': 'XLK', 'ADBE': 'XLK', 'INTC': 'XLK', 'CSCO': 'XLK', 'QCOM': 'XLK',
    'AMAT': 'XLK', 'MU': 'XLK', 'LRCX': 'XLK', 'NOW': 'XLK', 'PANW': 'XLK',
    'SNPS': 'XLK', 'CDNS': 'XLK', 'KLAC': 'XLK', 'ANET': 'XLK', 'CRWD': 'XLK',
    'PLTR': 'XLK', 'DELL': 'XLK', 'SMCI': 'XLK',
    'GOOGL': 'XLC', 'GOOG': 'XLC', 'META': 'XLC', 'NFLX': 'XLC', 'DIS': 'XLC',
    'CMCSA': 'XLC', 'TMUS': 'XLC', 'CHTR': 'XLC', 'VZ': 'XLC', 'T': 'XLC',
    'AMZN': 'XLY', 'TSLA': 'XLY', 'HD': 'XLY', 'MCD': 'XLY', 'NKE': 'XLY',
    'SBUX': 'XLY', 'BKNG': 'XLY', 'LOW': 'XLY', 'TJX': 'XLY', 'TGT': 'XLY',
    'LULU': 'XLY', 'ROST': 'XLY', 'ABNB': 'XLY', 'MAR': 'XLY', 'ORLY': 'XLY',
    'JPM': 'XLF', 'BAC': 'XLF', 'WFC': 'XLF', 'C': 'XLF', 'GS': 'XLF',
    'MS': 'XLF', 'BLK': 'XLF', 'SCHW': 'XLF', 'V': 'XLF', 'MA': 'XLF',
    'AXP': 'XLF', 'PGR': 'XLF', 'CB': 'XLF', 'MMC': 'XLF', 'AON': 'XLF',
    'UNH': 'XLV', 'JNJ': 'XLV', 'LLY': 'XLV', 'ABBV': 'XLV', 'MRK': 'XLV',
    'PFE': 'XLV', 'TMO': 'XLV', 'ABT': 'XLV', 'DHR': 'XLV', 'BMY': 'XLV',
    'AMGN': 'XLV', 'ISRG': 'XLV', 'VRTX': 'XLV', 'GILD': 'XLV', 'REGN': 'XLV',
    'CAT': 'XLI', 'GE': 'XLI', 'UNP': 'XLI', 'HON': 'XLI', 'BA': 'XLI',
    'UPS': 'XLI', 'RTX': 'XLI', 'DE': 'XLI', 'LMT': 'XLI', 'ETN': 'XLI',
    'WM': 'XLI', 'GD': 'XLI', 'TDG': 'XLI', 'EMR': 'XLI', 'PCAR': 'XLI',
    'XOM': 'XLE', 'CVX': 'XLE', 'COP': 'XLE', 'SLB': 'XLE', 'EOG': 'XLE',
    'MPC': 'XLE', 'PSX': 'XLE', 'VLO': 'XLE', 'OXY': 'XLE', 'WMB': 'XLE',
    'PG': 'XLP', 'COST': 'XLP', 'PEP': 'XLP', 'KO': 'XLP', 'WMT': 'XLP',
    'PM': 'XLP', 'MDLZ': 'XLP', 'CL': 'XLP', 'MO': 'XLP', 'KMB': 'XLP',
    'LIN': 'XLB', 'APD': 'XLB', 'SHW': 'XLB', 'FCX': 'XLB', 'NEM': 'XLB',
    'NEE': 'XLU', 'SO': 'XLU', 'DUK': 'XLU', 'CEG': 'XLU', 'SRE': 'XLU',
    'PLD': 'XLRE', 'AMT': 'XLRE', 'EQIX': 'XLRE', 'CCI': 'XLRE', 'SPG': 'XLRE'
}

def calc_vwap(highs, lows, closes, vols):
    typical = (highs + lows + closes) / 3.0
    cum_vol = np.cumsum(vols)
    cum_pv = np.cumsum(typical * vols)
    with np.errstate(divide='ignore', invalid='ignore'):
        vwap = np.where(cum_vol > 0, cum_pv / cum_vol, typical)
    return vwap

def backfill():
    conn = sqlite3.connect('wyckoff_trades.db')
    c = conn.cursor()
    c.execute('''
        SELECT id, ticker, timestamp, direction, entry_price
        FROM alerts
        WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN', 'STOPPED')
        ORDER BY id ASC
    ''')
    trades = c.fetchall()
    conn.close()

    print(f"Loaded {len(trades)} trades to backfill.")
    tickers = list(set([t[1] for t in trades]))
    print(f"Unique tickers to process: {len(tickers)}")

    # Fetch macro benchmarks
    print("Downloading SPY and ^VIX 60d 5m bars...")
    try:
        spy_df = yf.download("SPY", period="60d", interval="5m", progress=False)
        spy_idx = spy_df.index.tz_localize(None) if spy_df.index.tz is None else spy_df.index.tz_convert('America/New_York').tz_localize(None)
        if isinstance(spy_df.columns, pd.MultiIndex):
            spy_closes = spy_df['Close'].iloc[:, 0].values.astype(float)
        else:
            spy_closes = spy_df['Close'].values.astype(float)
    except Exception as e:
        print(f"Error fetching SPY: {e}")
        spy_df, spy_idx, spy_closes = None, None, None

    try:
        vix_df = yf.download("^VIX", period="60d", interval="5m", progress=False)
        vix_idx = vix_df.index.tz_localize(None) if vix_df.index.tz is None else vix_df.index.tz_convert('America/New_York').tz_localize(None)
        if isinstance(vix_df.columns, pd.MultiIndex):
            vix_closes = vix_df['Close'].iloc[:, 0].values.astype(float)
        else:
            vix_closes = vix_df['Close'].values.astype(float)
    except Exception as e:
        print(f"Error fetching VIX: {e}")
        vix_df, vix_idx, vix_closes = None, None, None

    # Fetch Sector ETFs
    sector_etfs = list(set(SECTOR_MAP.values()))
    sector_data = {}
    print(f"Downloading {len(sector_etfs)} Sector ETFs...")
    for etf in sector_etfs:
        try:
            e_df = yf.download(etf, period="60d", interval="5m", progress=False)
            if not e_df.empty:
                e_idx = e_df.index.tz_localize(None) if e_df.index.tz is None else e_df.index.tz_convert('America/New_York').tz_localize(None)
                if isinstance(e_df.columns, pd.MultiIndex):
                    e_c = e_df['Close'].iloc[:, 0].values.astype(float)
                    e_o = e_df['Open'].iloc[:, 0].values.astype(float)
                else:
                    e_c = e_df['Close'].values.astype(float)
                    e_o = e_df['Open'].values.astype(float)
                sector_data[etf] = (e_idx, e_c, e_o)
        except Exception:
            pass

    # Process tickers in batches
    ticker_dfs = {}
    print(f"Downloading 5m bars for all {len(tickers)} tickers...")
    for idx, tk in enumerate(tickers, 1):
        try:
            df = yf.download(tk, period="60d", interval="5m", progress=False)
            if not df.empty and len(df) > 20:
                df_idx = df.index.tz_localize(None) if df.index.tz is None else df.index.tz_convert('America/New_York').tz_localize(None)
                if isinstance(df.columns, pd.MultiIndex):
                    h = df['High'].iloc[:, 0].values.astype(float)
                    l = df['Low'].iloc[:, 0].values.astype(float)
                    c = df['Close'].iloc[:, 0].values.astype(float)
                    o = df['Open'].iloc[:, 0].values.astype(float)
                    v = df['Volume'].iloc[:, 0].values.astype(float)
                else:
                    h = df['High'].values.astype(float)
                    l = df['Low'].values.astype(float)
                    c = df['Close'].values.astype(float)
                    o = df['Open'].values.astype(float)
                    v = df['Volume'].values.astype(float)
                ticker_dfs[tk] = (df_idx, o, h, l, c, v)
        except Exception as e:
            print(f"Error downloading {tk}: {e}")
        if idx % 20 == 0 or idx == len(tickers):
            print(f"  Processed {idx}/{len(tickers)} tickers...")

    print("Computing true historical values and updating wyckoff_trades.db...")
    conn = sqlite3.connect('wyckoff_trades.db')
    cursor = conn.cursor()

    updated_count = 0
    for trade in trades:
        trade_id, tk, ts_str, direction, entry_price = trade
        if tk not in ticker_dfs:
            continue

        df_idx, opens, highs, lows, closes, vols = ticker_dfs[tk]

        try:
            ts_clean = ts_str.split('+')[0].split('-04:00')[0].split('-05:00')[0]
            dt = datetime.fromisoformat(ts_clean)
        except:
            continue

        time_diffs = np.array([abs((x - dt).total_seconds()) for x in df_idx])
        curr = int(np.argmin(time_diffs))

        # Must be within 20 minutes of entry bar
        if time_diffs[curr] > 1200 or curr < 8:
            continue

        curr_date = df_idx[curr].date()
        sess_bars = [i for i in range(max(0, curr - 78), curr + 1) if df_idx[i].date() == curr_date]
        if len(sess_bars) < 3:
            sess_bars = list(range(max(0, curr - 12), curr + 1))

        # 1. Candle CLV
        curr_rng = highs[curr] - lows[curr]
        candle_clv = float(np.clip((closes[curr] - lows[curr]) / curr_rng, 0.0, 1.0)) if curr_rng > 1e-6 else 0.70

        # 2. Consumed ATR & ATR Headroom
        day_h = np.max(highs[sess_bars])
        day_l = np.min(lows[sess_bars])
        day_range = day_h - day_l
        tr = np.maximum(highs[1:curr+1] - lows[1:curr+1],
                        np.maximum(abs(highs[1:curr+1] - closes[:curr]),
                                   abs(lows[1:curr+1] - closes[:curr])))
        avg_tr = np.mean(tr[-78:]) * np.sqrt(78) if len(tr) >= 78 else np.mean(tr) * np.sqrt(len(tr))
        consumed_atr = float(day_range / avg_tr) if avg_tr > 0 else 1.0
        atr_headroom = max(0.0, round(1.0 - consumed_atr, 3))

        # 3. ST / SOS Reclaim Volume Ratio
        prev_v = vols[curr - 1] if curr > 0 else 1.0
        st_volume_ratio = float(vols[curr] / prev_v) if prev_v > 0 else 1.0

        # 4. CVD Delta Absorption Ratio
        cvd_delta_ratio = float((2.0 * (closes[curr] - lows[curr]) - curr_rng) / curr_rng) if curr_rng > 1e-6 else 0.0

        # 5. RVOL-TOD
        vol_sma20 = np.mean(vols[max(0, curr-20):curr]) if curr > 0 else vols[curr]
        rvol_tod = float(vols[curr] / vol_sma20) if vol_sma20 > 0 else 1.0

        # 6. Spring Penetration Depth (%)
        is_long = (direction.upper() == 'LONG')
        look_start = max(0, curr - 10)
        if is_long:
            prior_low = np.min(lows[look_start:curr])
            penetration_depth_pct = float(max(0.0, (prior_low - lows[curr]) / prior_low * 100.0)) if prior_low > 0 else 0.0
        else:
            prior_high = np.max(highs[look_start:curr])
            penetration_depth_pct = float(max(0.0, (highs[curr] - prior_high) / prior_high * 100.0)) if prior_high > 0 else 0.0

        # 7. VPOC Distance (%)
        sess_closes = closes[sess_bars]
        sess_vols = vols[sess_bars]
        hist, bin_edges = np.histogram(sess_closes, bins=10, weights=sess_vols)
        vpoc_idx = np.argmax(hist)
        vpoc = (bin_edges[vpoc_idx] + bin_edges[vpoc_idx + 1]) / 2.0
        vpoc_distance_pct = float((closes[curr] - vpoc) / vpoc * 100.0) if vpoc > 0 else 0.0

        # 8. MTF ATR Ratio
        atr_fast = np.mean(tr[-14:]) if len(tr) >= 14 else (highs[curr] - lows[curr])
        atr_slow = np.mean(tr[-78:]) if len(tr) >= 78 else atr_fast
        mtf_atr_ratio = float(atr_fast / atr_slow) if atr_slow > 0 else 1.0

        # 9. Pre-Spring Trend Exhaustion Score
        start_ex = max(0, curr - 8)
        red_count = sum(1 for i in range(start_ex, curr) if closes[i] < opens[i])
        lower_low_count = sum(1 for i in range(start_ex + 1, curr) if lows[i] < lows[i-1])
        trend_exhaustion = float((red_count + lower_low_count) / 2.0)

        # 10. Intraday VWAP Slope (%)
        vwap_series = calc_vwap(highs[sess_bars], lows[sess_bars], closes[sess_bars], vols[sess_bars])
        if len(vwap_series) >= 7:
            vwap_slope = float((vwap_series[-1] - vwap_series[-7]) / vwap_series[-7] * 100.0) if vwap_series[-7] > 0 else 0.0
        else:
            vwap_slope = 0.0

        # 11. ORB Regime Code
        if len(sess_bars) >= 6:
            orb_bars = sess_bars[:6]
            orb_h = max(highs[orb_bars])
            orb_l = min(lows[orb_bars])
            if closes[curr] > orb_h: orb_regime_code = 1
            elif closes[curr] < orb_l: orb_regime_code = -1
            else: orb_regime_code = 0
        else:
            orb_regime_code = 0

        # 12. Relative Strength vs SPY (Alpha %)
        start_6 = max(0, curr - 6)
        tk_30m_ret = float((closes[curr] - closes[start_6]) / closes[start_6] * 100.0) if closes[start_6] > 0 else 0.0
        spy_30m_ret = 0.0
        if spy_idx is not None and len(spy_idx) > 0:
            spy_time_diffs = np.array([abs((x - dt).total_seconds()) for x in spy_idx])
            s_curr = int(np.argmin(spy_time_diffs))
            if spy_time_diffs[s_curr] <= 1200 and s_curr >= 6:
                spy_30m_ret = float((spy_closes[s_curr] - spy_closes[s_curr - 6]) / spy_closes[s_curr - 6] * 100.0) if spy_closes[s_curr - 6] > 0 else 0.0
        rs_alpha = float(tk_30m_ret - spy_30m_ret)

        # 13. VIX Velocity Slope (%)
        vix_slope = 0.0
        if vix_idx is not None and len(vix_idx) > 0:
            vix_time_diffs = np.array([abs((x - dt).total_seconds()) for x in vix_idx])
            v_curr = int(np.argmin(vix_time_diffs))
            if vix_time_diffs[v_curr] <= 1200 and v_curr >= 6:
                vix_slope = float((vix_closes[v_curr] - vix_closes[v_curr - 6]) / vix_closes[v_curr - 6] * 100.0) if vix_closes[v_curr - 6] > 0 else 0.0

        # 14. Sector Return % & Directional Alignment
        sec_etf = SECTOR_MAP.get(tk, 'SPY')
        sector_ret_pct = 0.0
        if sec_etf in sector_data:
            e_idx, e_c, e_o = sector_data[sec_etf]
            e_time_diffs = np.array([abs((x - dt).total_seconds()) for x in e_idx])
            e_curr = int(np.argmin(e_time_diffs))
            if e_time_diffs[e_curr] <= 1200:
                e_sess = [i for i in range(max(0, e_curr - 78), e_curr + 1) if e_idx[i].date() == curr_date]
                if e_sess:
                    day_e_open = e_o[e_sess[0]]
                    curr_e_close = e_c[e_curr]
                    sector_ret_pct = float((curr_e_close - day_e_open) / day_e_open * 100.0) if day_e_open > 0 else 0.0
        sector_aligned = 1 if ((sector_ret_pct >= 0 and is_long) or (sector_ret_pct <= 0 and not is_long)) else 0

        # Execute SQL Update
        cursor.execute('''
            UPDATE alerts
            SET candle_clv = ?,
                consumed_atr = ?,
                atr_headroom = ?,
                st_volume_ratio = ?,
                cvd_delta_ratio = ?,
                rvol_tod = ?,
                penetration_depth_pct = ?,
                vpoc_distance_pct = ?,
                mtf_atr_ratio = ?,
                trend_exhaustion = ?,
                vwap_slope = ?,
                orb_regime_code = ?,
                rs_alpha = ?,
                vix_slope = ?,
                sector_ret_pct = ?,
                sector_aligned = ?
            WHERE id = ?
        ''', (
            round(candle_clv, 3),
            round(consumed_atr, 3),
            round(atr_headroom, 3),
            round(st_volume_ratio, 3),
            round(cvd_delta_ratio, 3),
            round(rvol_tod, 3),
            round(penetration_depth_pct, 3),
            round(vpoc_distance_pct, 3),
            round(mtf_atr_ratio, 3),
            round(trend_exhaustion, 1),
            round(vwap_slope, 3),
            int(orb_regime_code),
            round(rs_alpha, 3),
            round(vix_slope, 3),
            round(sector_ret_pct, 3),
            int(sector_aligned),
            trade_id
        ))
        updated_count += 1

    conn.commit()
    conn.close()
    print(f"\nSuccessfully backfilled {updated_count}/{len(trades)} trades with true continuous indicators!")

    # Retrain Machine Learning Engine
    print("\nRetraining Random Forest Model across newly populated historical features...")
    from wyckoff_ml_engine import train_and_upgrade_model
    success, msg = train_and_upgrade_model("True Historical 20-Indicator Backfill")
    print(f"Retrain Success: {success}, Message: {msg}")

if __name__ == "__main__":
    backfill()
