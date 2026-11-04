import sqlite3
import pandas as pd
import numpy as np

def run_short_backtest():
    conn = sqlite3.connect(r'C:\Users\f4duk\.gemini\antigravity\brain\ff19be90-08cb-4b69-bd79-77eaa347aec0\scratch\wyckoff_cloud\wyckoff_trades.db')
    df = pd.read_sql_query("SELECT * FROM alerts WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')", conn)
    conn.close()

    shorts = df[df['direction'] == 'SHORT'].copy()
    longs = df[df['direction'] == 'LONG'].copy()

    print(f"=== HISTORICAL BASELINE COMPARISON ===")
    print(f"Total Closed Samples: {len(df)}")
    
    # Long Stats
    long_w = len(longs[longs['outcome'] == 'WIN'])
    long_l = len(longs[longs['outcome'] == 'LOSS'])
    long_be = len(longs[longs['outcome'] == 'BREAKEVEN'])
    long_wr = (long_w / len(longs)) * 100
    long_r = longs['pnl_r'].sum()
    print(f"LONGS:  {len(longs)} trades | {long_w}W - {long_l}L - {long_be}BE | Win Rate: {long_wr:.1f}% | Net R: {long_r:+.2f}R")

    # Short Baseline Stats
    short_w = len(shorts[shorts['outcome'] == 'WIN'])
    short_l = len(shorts[shorts['outcome'] == 'LOSS'])
    short_be = len(shorts[shorts['outcome'] == 'BREAKEVEN'])
    short_wr = (short_w / len(shorts)) * 100
    short_r = shorts['pnl_r'].sum()
    print(f"SHORTS (Unfiltered Baseline): {len(shorts)} trades | {short_w}W - {short_l}L - {short_be}BE | Win Rate: {short_wr:.1f}% | Net R: {short_r:+.2f}R")

    print("\n" + "="*60)
    print("BACKTEST: SHORT TRADES CONDITIONED ON SECTOR & CIRCUIT BREAKER RULES")
    print("="*60)

    # 1. Sector Alignment (sector_aligned == 1 means sector matches trade direction, i.e., sector is falling)
    print("\n--- 1. SECTOR DIRECTIONAL ALIGNMENT ---")
    for val in [0, 1]:
        sub = shorts[shorts['sector_aligned'] == val]
        if len(sub) > 0:
            w = len(sub[sub['outcome'] == 'WIN'])
            l = len(sub[sub['outcome'] == 'LOSS'])
            be = len(sub[sub['outcome'] == 'BREAKEVEN'])
            wr = (w / len(sub)) * 100
            net_r = sub['pnl_r'].sum()
            label = "Sector Aligned (Falling Sector)" if val == 1 else "Sector NOT Aligned (Fighting Sector Trend)"
            print(f"[{label}] (N={len(sub)}): {w}W - {l}L - {be}BE | Win Rate: {wr:.1f}% | Net R: {net_r:+.2f}R | Avg R: {net_r/len(sub):+.2f}R")

    # 2. Sector Return Pct (Negative vs Positive)
    print("\n--- 2. SECTOR INTRADAY RETURN (CIRCUIT BREAKER PROXIES) ---")
    sub_down = shorts[shorts['sector_ret_pct'] < 0]
    sub_up = shorts[shorts['sector_ret_pct'] >= 0]
    for name, sub in [("Sector in Negative Return (< 0.0%)", sub_down), ("Sector in Positive Return (>= 0.0%)", sub_up)]:
        if len(sub) > 0:
            w = len(sub[sub['outcome'] == 'WIN'])
            l = len(sub[sub['outcome'] == 'LOSS'])
            be = len(sub[sub['outcome'] == 'BREAKEVEN'])
            wr = (w / len(sub)) * 100
            net_r = sub['pnl_r'].sum()
            print(f"[{name}] (N={len(sub)}): {w}W - {l}L - {be}BE | Win Rate: {wr:.1f}% | Net R: {net_r:+.2f}R | Avg R: {net_r/len(sub):+.2f}R")

    # 3. Macro Market Alignment (SPY Bullish vs Bearish)
    print("\n--- 3. BROAD MARKET REGIME (SPY BULLISH VS WEAK/BEARISH) ---")
    for val in [0, 1]:
        sub = shorts[shorts['spy_bullish'] == val]
        if len(sub) > 0:
            w = len(sub[sub['outcome'] == 'WIN'])
            l = len(sub[sub['outcome'] == 'LOSS'])
            be = len(sub[sub['outcome'] == 'BREAKEVEN'])
            wr = (w / len(sub)) * 100
            net_r = sub['pnl_r'].sum()
            label = "SPY Bearish / Neutral (spy_bullish=0)" if val == 0 else "SPY Bullish (Fighting Broad Tape)"
            print(f"[{label}] (N={len(sub)}): {w}W - {l}L - {be}BE | Win Rate: {wr:.1f}% | Net R: {net_r:+.2f}R | Avg R: {net_r/len(sub):+.2f}R")

    # 4. CVD Delta Ratio (Negative Delta = Sellers in control / Absorption of buyers)
    print("\n--- 4. ORDER FLOW DELTA CONFIRMATION (CVD) ---")
    sub_neg_delta = shorts[shorts['cvd_delta_ratio'] < 0]
    sub_pos_delta = shorts[shorts['cvd_delta_ratio'] >= 0]
    for name, sub in [("CVD Delta Negative (< 0.0, Selling Pressure)", sub_neg_delta), ("CVD Delta Positive (>= 0.0, Trapped Bidders)", sub_pos_delta)]:
        if len(sub) > 0:
            w = len(sub[sub['outcome'] == 'WIN'])
            l = len(sub[sub['outcome'] == 'LOSS'])
            be = len(sub[sub['outcome'] == 'BREAKEVEN'])
            wr = (w / len(sub)) * 100
            net_r = sub['pnl_r'].sum()
            print(f"[{name}] (N={len(sub)}): {w}W - {l}L - {be}BE | Win Rate: {wr:.1f}% | Net R: {net_r:+.2f}R | Avg R: {net_r/len(sub):+.2f}R")

    # 5. Combined Institutional Circuit Breaker Short Rule:
    # Rule: ONLY Short when:
    # A) Sector is Aligned (sector_aligned == 1 and sector_ret_pct < 0)
    # B) Broad Tape is NOT furiously bullish (spy_bullish == 0) OR VIX is expanding (vix_slope >= 0)
    print("\n" + "="*60)
    print("--- 5. COMBINED INSTITUTIONAL SHORT CIRCUIT BREAKER MODEL ---")
    print("="*60)
    
    cb_rule_1 = shorts[(shorts['sector_aligned'] == 1) & (shorts['sector_ret_pct'] < 0)]
    cb_rule_strict = shorts[(shorts['sector_aligned'] == 1) & (shorts['sector_ret_pct'] < 0) & (shorts['spy_bullish'] == 0)]
    cb_rule_cvd = shorts[(shorts['sector_aligned'] == 1) & (shorts['cvd_delta_ratio'] < 0)]

    for name, sub in [
        ("Filter A: Sector Falling & Aligned (sector_ret_pct < 0 & sector_aligned=1)", cb_rule_1),
        ("Filter B: Sector Falling + CVD Delta Negative (Active Supply Wave)", cb_rule_cvd),
        ("Filter C (STRICT): Sector Falling + SPY Neutral/Bearish (Macro + Sector Alignment)", cb_rule_strict)
    ]:
        if len(sub) > 0:
            w = len(sub[sub['outcome'] == 'WIN'])
            l = len(sub[sub['outcome'] == 'LOSS'])
            be = len(sub[sub['outcome'] == 'BREAKEVEN'])
            wr = (w / len(sub)) * 100
            net_r = sub['pnl_r'].sum()
            win_r_sum = sub[sub['outcome'] == 'WIN']['pnl_r'].sum()
            loss_r_sum = abs(sub[sub['outcome'] == 'LOSS']['pnl_r'].sum())
            pf = (win_r_sum / loss_r_sum) if loss_r_sum > 0 else 999.0
            print(f"\n>>> {name} <<<")
            print(f"    Trades: {len(sub)} | Record: {w}W - {l}L - {be}BE")
            print(f"    Win Rate: {wr:.1f}% (vs Baseline 21.0%) -> Improvement: {wr - 21.0:+.1f}%")
            print(f"    Net Expectancy: {net_r:+.2f}R (vs Baseline -24.27R)")
            print(f"    Profit Factor: {pf:.2f}")

    # Also what if we looked at the overall portfolio impact?
    print("\n" + "="*60)
    print("PORTFOLIO IMPACT: COMPARING FULL PORTFOLIO HYPOTHETICALS")
    print("="*60)
    # Original all trades:
    tot_w = len(df[df['outcome'] == 'WIN'])
    tot_l = len(df[df['outcome'] == 'LOSS'])
    tot_be = len(df[df['outcome'] == 'BREAKEVEN'])
    tot_wr = (tot_w / len(df)) * 100
    tot_r = df['pnl_r'].sum()
    print(f"1. Unfiltered Portfolio (Longs + All Shorts): {len(df)} trades | WR: {tot_wr:.1f}% | Net R: {tot_r:+.2f}R")

    # Longs Only:
    print(f"2. Longs-Only Regime (Current Production Rule): {len(longs)} trades | WR: {long_wr:.1f}% | Net R: {long_r:+.2f}R")

    # Longs + Strict Circuit-Breaker Shorts:
    combined_strict = pd.concat([longs, cb_rule_strict])
    c_w = len(combined_strict[combined_strict['outcome'] == 'WIN'])
    c_l = len(combined_strict[combined_strict['outcome'] == 'LOSS'])
    c_be = len(combined_strict[combined_strict['outcome'] == 'BREAKEVEN'])
    c_wr = (c_w / len(combined_strict)) * 100
    c_r = combined_strict['pnl_r'].sum()
    print(f"3. Longs + Circuit-Breaker Filtered Shorts: {len(combined_strict)} trades | WR: {c_wr:.1f}% | Net R: {c_r:+.2f}R")

if __name__ == '__main__':
    run_short_backtest()
