import datetime
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd

def get_est_now():
    return datetime.datetime.now(ZoneInfo("America/New_York"))

def get_rebalance_calendar_features(target_date=None):
    """
    Tactic 1: Index Rebalance & Triple Witching Calendar Dynamics.
    Calculates proximity to the quarterly S&P 500 / Russell / MSCI rebalance dates
    (Third Friday of March, June, September, December).
    """
    if target_date is None:
        target_date = get_est_now().date()
    elif isinstance(target_date, datetime.datetime):
        target_date = target_date.date()

    year = target_date.year
    rebalance_dates = []
    
    # Calculate third Friday of March, June, September, December for current and next year
    for y in [year, year + 1]:
        for month in [3, 6, 9, 12]:
            first_day = datetime.date(y, month, 1)
            first_friday = first_day + datetime.timedelta(days=(4 - first_day.weekday()) % 7)
            third_friday = first_friday + datetime.timedelta(weeks=2)
            rebalance_dates.append(third_friday)

    # Future dates
    future_dates = [d for d in rebalance_dates if d >= target_date]
    next_reb = future_dates[0] if future_dates else target_date
    days_to_rebalance = (next_reb - target_date).days

    # Triple witching week: within 4 days of quarterly expiration Friday
    is_triple_witching = 1 if days_to_rebalance <= 4 else 0

    # Post-rebalance hangover: 1 to 3 days after rebalance (prime UTAD short exhaustion window)
    past_dates = [d for d in rebalance_dates if d < target_date]
    last_reb = past_dates[-1] if past_dates else target_date
    days_since_reb = (target_date - last_reb).days
    is_rebalance_hangover = 1 if 1 <= days_since_reb <= 3 else 0

    return {
        "days_to_rebalance": days_to_rebalance,
        "is_triple_witching": is_triple_witching,
        "is_rebalance_hangover": is_rebalance_hangover
    }

def get_moc_surge_features(current_time=None, rel_vol=1.0):
    """
    Tactic 2: Market-on-Close (MOC) Imbalance & Closing Drive Dynamics.
    Detects whether the bar is inside the 3:45 PM - 4:00 PM EST institutional auction window
    and computes the closing volume surge acceleration.
    """
    if current_time is None:
        current_time = get_est_now()

    hour = current_time.hour
    minute = current_time.minute

    # MOC Auction window: 15:45 to 16:00 EST
    is_moc_window = 1 if (hour == 15 and minute >= 45) else 0
    moc_surge_score = round(float(rel_vol), 2) if is_moc_window else 0.0

    return {
        "is_moc_window": is_moc_window,
        "moc_surge_score": moc_surge_score
    }

def get_dealer_gamma_features(price, options_flow=None):
    """
    Tactic 3: Dealer Gamma Regime & Gamma Wall Distance.
    Determines if market makers are Long Gamma (+1: mean-reverting pin)
    or Short Gamma (-1: trending acceleration), and computes distance to the Gamma Wall.
    """
    if not options_flow:
        return {
            "dealer_gamma_regime": 0,
            "gamma_wall_dist_pct": 0.0
        }

    pcr = options_flow.get("put_call_ratio", 1.0) or 1.0
    gamma_wall = options_flow.get("gamma_wall", 0.0) or 0.0

    # Gamma Wall Distance %
    gw_dist_pct = 0.0
    if gamma_wall > 0 and price > 0:
        gw_dist_pct = round(((price - gamma_wall) / price) * 100.0, 2)

    # Gamma Regime Classification:
    # Low P/C (< 0.75) and near Wall -> Dealers Long Gamma (+1 / Mean Reverting)
    # High P/C (> 1.25) -> Dealers Short Gamma (-1 / Trend Accelerating)
    if pcr < 0.75 and abs(gw_dist_pct) < 1.5:
        dealer_gamma_regime = 1   # Positive Gamma / Volatility Dampener
    elif pcr > 1.25 or abs(gw_dist_pct) > 3.0:
        dealer_gamma_regime = -1  # Negative Gamma / Volatility Accelerator
    else:
        dealer_gamma_regime = 0   # Neutral Gamma

    return {
        "dealer_gamma_regime": dealer_gamma_regime,
        "gamma_wall_dist_pct": gw_dist_pct
    }

def get_institutional_block_features(price, bar_volume, rel_vol=1.0, effort_vs_result=1.0):
    """
    Tactic 4: Large Block Volume vs. Retail Odd-Lot Concentration Proxy.
    Combines bar dollar volume and Wyckoff absorption (effort vs result)
    to quantify institutional block participation.
    """
    dollar_vol = float(price * bar_volume) if (price and bar_volume) else 0.0
    
    # Dollar intensity scale (normalized around $2M institutional threshold)
    dollar_intensity = min(3.0, max(0.1, dollar_vol / 2_000_000.0))
    block_ratio = round(float(effort_vs_result * dollar_intensity), 2)

    return {
        "institutional_block_ratio": block_ratio
    }

def compute_all_institutional_features(ticker, price, bar_volume, rel_vol=1.0, effort_vs_result=1.0, options_flow=None, current_time=None):
    """
    Unified extraction of all 4 institutional tactics.
    Returns clean dictionary ready for ML feature ingestion.
    """
    est_now = current_time or get_est_now()
    
    reb = get_rebalance_calendar_features(est_now)
    moc = get_moc_surge_features(est_now, rel_vol)
    gamma = get_dealer_gamma_features(price, options_flow)
    block = get_institutional_block_features(price, bar_volume, rel_vol, effort_vs_result)

    return {
        "days_to_rebalance": reb["days_to_rebalance"],
        "is_triple_witching": reb["is_triple_witching"],
        "is_rebalance_hangover": reb["is_rebalance_hangover"],
        "is_moc_window": moc["is_moc_window"],
        "moc_surge_score": moc["moc_surge_score"],
        "dealer_gamma_regime": gamma["dealer_gamma_regime"],
        "gamma_wall_dist_pct": gamma["gamma_wall_dist_pct"],
        "institutional_block_ratio": block["institutional_block_ratio"]
    }
