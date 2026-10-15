import os
import json
import time
import datetime
from typing import List, Dict, Any

QUEUE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "priority_queue.json")

# Core Anchor Leaders: Top 25 high-beta, high-liquidity leaders permanently monitored on 5M.
# Leaves maximum bandwidth for dynamic 15M breakout/spring promotions.
ANCHOR_TICKERS = [
    "NVDA", "TSLA", "AMD", "META", "PLTR", "UBER", "COIN", "AMZN", "AAPL", "MSFT",
    "GOOGL", "AVGO", "NFLX", "CRM", "QCOM", "DIS", "BA", "INTC", "NOW", "GE",
    "WFC", "JPM", "LLY", "COST", "HD"
]

_QUEUE_MEMORY: Dict[str, Dict[str, Any]] = {}

def _load_queue_if_needed():
    global _QUEUE_MEMORY
    if not _QUEUE_MEMORY and os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        t = item.get('ticker')
                        if t:
                            _QUEUE_MEMORY[t.upper()] = item
        except Exception:
            pass

def get_priority_queue() -> List[Dict[str, Any]]:
    """Returns the current prioritized 5M execution queue for API and UI display with ML Grade."""
    global _QUEUE_MEMORY
    _load_queue_if_needed()
    items = []
    if os.path.exists(QUEUE_FILE):
        try:
            with open(QUEUE_FILE, "r") as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    items = data
        except Exception:
            pass
    if not items:
        items = list(_QUEUE_MEMORY.values())

    for it in items:
        if 'ml_confidence' not in it or it['ml_confidence'] is None:
            st = it.get('status', '')
            base_score = 68.0 if st in ["HOT_SPRING", "HOT_UTAD"] else (61.0 if "TEST" in st else 52.0)
            b_reg = float(it.get('bars_in_regime') or 0)
            d_bound = abs(float(it.get('dist_boundary_pct') or 0))
            compression_bonus = min(8.0, b_reg * 0.7)
            proximity_bonus = max(0.0, 4.0 - (d_bound * 2.0))
            it['ml_confidence'] = round(min(88.0, base_score + compression_bonus + proximity_bonus), 1)

    return items

def get_active_5m_tickers(default_all: List[str] = None) -> List[str]:
    """Returns list of ticker symbols currently in the Tier 1 Fast Queue (~120-150 tickers)."""
    q = get_priority_queue()
    if q:
        symbols = [item['ticker'].upper() for item in q if item.get('ticker')]
        # Ensure anchor tickers are always present
        for a in ANCHOR_TICKERS:
            if a not in symbols:
                symbols.append(a)
        return symbols
    return ANCHOR_TICKERS if not default_all else default_all[:120]

def update_ticker_in_queue(
    ticker: str,
    price: float,
    tr_low: float,
    tr_high: float,
    bars_in_regime: int,
    is_bull: bool,
    ml_confidence: float = None
):
    """
    Updates a single ticker's status in the dynamic priority queue:
    - HOT_SPRING: within 1.5% of TR Low Support in bearish exhaustion regime
    - HOT_UTAD: within 1.5% of TR High Ceiling in bullish exhaustion regime
    - TEST_SUPPORT: within 1.5% of TR Low Support
    - TEST_RESIST: within 1.5% of TR High Ceiling
    - ANCHOR_MONITOR: Anchor ticker currently in mid-range Phase B
    - MONITORING: Non-anchor stock within 2.5% of boundary
    - CHOPPING_BLOCK: Promoted stock that has drifted > 2.5% into middle of range
    """
    global _QUEUE_MEMORY
    _load_queue_if_needed()
    ticker = ticker.upper()
    is_anchor = ticker in ANCHOR_TICKERS

    if price <= 0:
        return

    dist_low_pct = round(((price - tr_low) / price) * 100.0, 2) if tr_low > 0 else 99.0
    dist_high_pct = round(((tr_high - price) / price) * 100.0, 2) if tr_high > 0 else 99.0

    near_low = -0.5 <= dist_low_pct <= 1.50
    near_high = -0.5 <= dist_high_pct <= 1.50

    existing = _QUEUE_MEMORY.get(ticker, {})
    chop_cycles = existing.get('chop_cycles', 0)

    # Determine status
    if near_low and not is_bull:
        status = "HOT_SPRING"
        boundary_type = "TR_LOW"
        dist_boundary = dist_low_pct
        chop_cycles = 0
    elif near_high and is_bull:
        status = "HOT_UTAD"
        boundary_type = "TR_HIGH"
        dist_boundary = dist_high_pct
        chop_cycles = 0
    elif near_low:
        status = "TEST_SUPPORT"
        boundary_type = "TR_LOW"
        dist_boundary = dist_low_pct
        chop_cycles = 0
    elif near_high:
        status = "TEST_RESIST"
        boundary_type = "TR_HIGH"
        dist_boundary = dist_high_pct
        chop_cycles = 0
    elif is_anchor:
        status = "ANCHOR_MONITOR"
        boundary_type = "TR_LOW" if dist_low_pct < dist_high_pct else "TR_HIGH"
        dist_boundary = min(dist_low_pct, dist_high_pct)
        chop_cycles = 0
    else:
        # Non-anchor stock: check if it drifted into middle of range
        if dist_low_pct > 2.5 and dist_high_pct > 2.5:
            status = "CHOPPING_BLOCK"
            boundary_type = "MID_RANGE"
            dist_boundary = min(dist_low_pct, dist_high_pct)
            chop_cycles += 1
        else:
            status = "MONITORING"
            boundary_type = "TR_LOW" if dist_low_pct < dist_high_pct else "TR_HIGH"
            dist_boundary = min(dist_low_pct, dist_high_pct)

    # Only keep non-anchor tickers if they are near boundary or already tracked
    if not is_anchor and status not in ["HOT_SPRING", "HOT_UTAD", "TEST_SUPPORT", "TEST_RESIST"] and ticker not in _QUEUE_MEMORY:
        # Not qualified to enter the fast queue
        return

    if ml_confidence is None:
        ml_confidence = existing.get('ml_confidence')
    if ml_confidence is None:
        base_score = 68.0 if status in ["HOT_SPRING", "HOT_UTAD"] else (61.0 if "TEST" in status else 52.0)
        b_reg = float(bars_in_regime or 0)
        d_bound = abs(float(dist_boundary or 0))
        compression_bonus = min(8.0, b_reg * 0.7)
        proximity_bonus = max(0.0, 4.0 - (d_bound * 2.0))
        ml_confidence = round(min(88.0, base_score + compression_bonus + proximity_bonus), 1)

    _QUEUE_MEMORY[ticker] = {
        "ticker": ticker,
        "is_anchor": is_anchor,
        "status": status,
        "price": round(price, 2),
        "tr_low": round(tr_low, 2),
        "tr_high": round(tr_high, 2),
        "boundary_type": boundary_type,
        "dist_boundary_pct": dist_boundary,
        "bars_in_regime": bars_in_regime,
        "chop_cycles": chop_cycles,
        "ml_confidence": ml_confidence,
        "updated_at": datetime.datetime.now().strftime("%H:%M:%S")
    }

def flush_priority_queue():
    """
    Prunes stale chopping block tickers (if queue > 130) and saves the sorted
    queue to priority_queue.json atomically.
    """
    global _QUEUE_MEMORY
    now_str = datetime.datetime.now().strftime("%H:%M:%S")

    # Seed anchors if empty
    for a in ANCHOR_TICKERS:
        if a not in _QUEUE_MEMORY:
            _QUEUE_MEMORY[a] = {
                "ticker": a,
                "is_anchor": True,
                "status": "ANCHOR_MONITOR",
                "price": 0.0,
                "tr_low": 0.0,
                "tr_high": 0.0,
                "boundary_type": "ANCHOR",
                "dist_boundary_pct": 0.0,
                "bars_in_regime": 0,
                "chop_cycles": 0,
                "updated_at": now_str
            }

    # Prune non-anchors that have been on chopping block for >= 2 cycles if queue is large
    all_items = list(_QUEUE_MEMORY.values())
    if len(all_items) > 130:
        to_delete = [
            item['ticker'] for item in all_items
            if not item['is_anchor'] and item['status'] == "CHOPPING_BLOCK" and item['chop_cycles'] >= 2
        ]
        for t in to_delete:
            _QUEUE_MEMORY.pop(t, None)

    # Sort queue: HOT setups first, then boundary tests, then ANCHOR_MONITOR, then MONITORING, then CHOPPING_BLOCK
    def sort_key(item):
        st = item.get('status', '')
        if st in ['HOT_SPRING', 'HOT_UTAD']:
            return (0, item.get('dist_boundary_pct', 99.0))
        elif st in ['TEST_SUPPORT', 'TEST_RESIST']:
            return (1, item.get('dist_boundary_pct', 99.0))
        elif item.get('is_anchor', False):
            return (2, item.get('dist_boundary_pct', 99.0))
        elif st == 'MONITORING':
            return (3, item.get('dist_boundary_pct', 99.0))
        else: # CHOPPING_BLOCK
            return (4, -item.get('chop_cycles', 0))

    sorted_queue = sorted(_QUEUE_MEMORY.values(), key=sort_key)

    try:
        tmp_file = QUEUE_FILE + ".tmp"
        with open(tmp_file, "w") as f:
            json.dump(sorted_queue, f, indent=2)
        os.replace(tmp_file, QUEUE_FILE)
    except Exception as e:
        print(f"[Priority Queue] Error flushing queue to disk: {e}")

def get_priority_queue_stats() -> Dict[str, int]:
    """Returns counts of tickers in each tier for UI badges."""
    q = get_priority_queue()
    hot = sum(1 for x in q if x.get('status') in ['HOT_SPRING', 'HOT_UTAD', 'TEST_SUPPORT', 'TEST_RESIST'])
    anchors = sum(1 for x in q if x.get('is_anchor', False))
    chopping = sum(1 for x in q if x.get('status') == 'CHOPPING_BLOCK')
    monitoring = sum(1 for x in q if x.get('status') == 'MONITORING')
    return {
        "total": len(q),
        "hot": hot,
        "anchors": anchors,
        "monitoring": monitoring,
        "chopping_block": chopping
    }
