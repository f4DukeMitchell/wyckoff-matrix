"""
Multi-Timeframe Confluence Scoring Engine
Scores each ticker based on alignment between daily and intraday regimes.
"""
import yfinance as yf
import numpy as np
import pandas as pd


def get_supertrend(highs, lows, closes, atr_len, factor):
    """Calculate Supertrend direction. Returns boolean array (True=Bullish)."""
    n = len(closes)
    atr = np.zeros(n)
    up = np.zeros(n)
    dn = np.zeros(n)
    direction = np.ones(n, dtype=bool)

    tr = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    
    for i in range(1, n):
        atr[i] = (atr[i-1] * (atr_len - 1) + tr[i]) / atr_len if i >= atr_len else np.mean(tr[1:i+1]) if i > 0 else tr[i]
    
    for i in range(1, n):
        hl2 = (highs[i] + lows[i]) / 2.0
        new_up = hl2 - factor * atr[i]
        new_dn = hl2 + factor * atr[i]
        up[i] = max(new_up, up[i-1]) if closes[i-1] > up[i-1] else new_up
        dn[i] = min(new_dn, dn[i-1]) if closes[i-1] < dn[i-1] else new_dn
        if closes[i] > dn[i-1]:
            direction[i] = True
        elif closes[i] < up[i-1]:
            direction[i] = False
        else:
            direction[i] = direction[i-1]
    return direction


def score_ticker_confluence(ticker, intraday_interval="5m", intraday_period="5d"):
    """
    Score a ticker's multi-timeframe confluence.
    
    Returns a dict with:
    - daily_regime: BULL / BEAR / MIXED
    - intraday_regime: BULL / BEAR / MIXED
    - confluence_score: 0-100 (100 = perfect alignment across all timeframes)
    - confluence_label: "STRONG BULL", "STRONG BEAR", "DIVERGENT", "MIXED"
    - daily_supertrends: dict of each ST direction on daily
    - intraday_supertrends: dict of each ST direction on intraday
    - reversal_quality: "HIGH" if daily exhausted + intraday flipping = best setups
    """
    result = {
        'ticker': ticker,
        'daily_regime': 'N/A',
        'intraday_regime': 'N/A',
        'confluence_score': 0,
        'confluence_label': 'N/A',
        'daily_exhaustion_bars': 0,
        'reversal_quality': 'LOW',
        'daily_st': {},
        'intraday_st': {},
    }
    
    try:
        # --- DAILY DATA ---
        daily = yf.download(ticker, period="6mo", interval="1d", progress=False)
        if daily.empty or len(daily) < 30:
            return result
        
        dh = daily['High'].values.flatten()
        dl = daily['Low'].values.flatten()
        dc = daily['Close'].values.flatten()
        
        d_micro = get_supertrend(dh, dl, dc, 1, 1.0)
        d_struct = get_supertrend(dh, dl, dc, 3, 3.0)
        d_macro = get_supertrend(dh, dl, dc, 9, 9.0)
        d_anchor = get_supertrend(dh, dl, dc, 14, 14.0)
        
        result['daily_st'] = {
            'micro': bool(d_micro[-1]),
            'structural': bool(d_struct[-1]),
            'macro': bool(d_macro[-1]),
            'anchor': bool(d_anchor[-1]),
        }
        
        daily_bulls = sum([d_micro[-1], d_struct[-1], d_macro[-1], d_anchor[-1]])
        if daily_bulls >= 3:
            result['daily_regime'] = 'BULL'
        elif daily_bulls <= 1:
            result['daily_regime'] = 'BEAR'
        else:
            result['daily_regime'] = 'MIXED'
        
        # Daily exhaustion (how long macro+anchor have held same direction)
        is_bull_regime = d_macro[-1] and d_anchor[-1]
        is_bear_regime = not d_macro[-1] and not d_anchor[-1]
        bars = 0
        if is_bull_regime or is_bear_regime:
            for i in range(len(d_macro)-1, -1, -1):
                if (is_bull_regime and d_macro[i] and d_anchor[i]) or \
                   (is_bear_regime and not d_macro[i] and not d_anchor[i]):
                    bars += 1
                else:
                    break
        result['daily_exhaustion_bars'] = bars
        
        # --- INTRADAY DATA ---
        intra = yf.download(ticker, period=intraday_period, interval=intraday_interval, progress=False)
        if intra.empty or len(intra) < 50:
            return result
        
        ih = intra['High'].values.flatten()
        il = intra['Low'].values.flatten()
        ic = intra['Close'].values.flatten()
        
        i_micro = get_supertrend(ih, il, ic, 1, 1.0)
        i_struct = get_supertrend(ih, il, ic, 3, 3.0)
        i_macro = get_supertrend(ih, il, ic, 9, 9.0)
        i_anchor = get_supertrend(ih, il, ic, 14, 14.0)
        
        result['intraday_st'] = {
            'micro': bool(i_micro[-1]),
            'structural': bool(i_struct[-1]),
            'macro': bool(i_macro[-1]),
            'anchor': bool(i_anchor[-1]),
        }
        
        intra_bulls = sum([i_micro[-1], i_struct[-1], i_macro[-1], i_anchor[-1]])
        if intra_bulls >= 3:
            result['intraday_regime'] = 'BULL'
        elif intra_bulls <= 1:
            result['intraday_regime'] = 'BEAR'
        else:
            result['intraday_regime'] = 'MIXED'
        
        # --- CONFLUENCE SCORING ---
        # Each matching supertrend pair (daily vs intraday) = 25 points
        score = 0
        for key in ['micro', 'structural', 'macro', 'anchor']:
            if result['daily_st'].get(key) == result['intraday_st'].get(key):
                score += 25
        result['confluence_score'] = score
        
        # Label
        if score == 100 and result['daily_regime'] == 'BULL':
            result['confluence_label'] = 'STRONG BULL'
        elif score == 100 and result['daily_regime'] == 'BEAR':
            result['confluence_label'] = 'STRONG BEAR'
        elif score <= 25:
            result['confluence_label'] = 'DIVERGENT'
        else:
            result['confluence_label'] = 'MIXED'
        
        # Reversal quality: HIGH if daily is exhausted AND intraday is flipping opposite
        if bars >= 20 and result['daily_regime'] != result['intraday_regime'] and result['intraday_regime'] != 'MIXED':
            result['reversal_quality'] = 'HIGH'
        elif bars >= 15:
            result['reversal_quality'] = 'MEDIUM'
        else:
            result['reversal_quality'] = 'LOW'
        
    except Exception as e:
        pass
    
    return result


def scan_confluence(tickers, max_workers=10):
    """Scan multiple tickers for multi-TF confluence. Returns list of result dicts."""
    import concurrent.futures
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(score_ticker_confluence, t): t for t in tickers}
        for future in concurrent.futures.as_completed(futures):
            try:
                r = future.result()
                if r['confluence_score'] > 0:
                    results.append(r)
            except:
                pass
    results.sort(key=lambda x: x['confluence_score'], reverse=True)
    return results


if __name__ == '__main__':
    test = score_ticker_confluence("AAPL")
    print(f"AAPL Daily: {test['daily_regime']} | Intraday: {test['intraday_regime']} | Confluence: {test['confluence_score']}/100 | Label: {test['confluence_label']} | Reversal Quality: {test['reversal_quality']}")
