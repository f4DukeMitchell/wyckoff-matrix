import os
import json
import threading

from data_paths import CONFIG_PATH  # runtime config lives outside the git tree
_config_lock = threading.Lock()

DEFAULT_CONFIG = {
    "alloc_mode": "DYNAMIC_TRIM",     # "DYNAMIC_TRIM" (100% -> 50% -> 33%), "PERCENT_BP", or "FIXED_DOLLAR"
    "alloc_value": 33.3,              # Sizing baseline / slot percentage
    "max_concurrent": 3,              # Max concurrent active algo positions (3 slots optimal)
    "breakeven_guarded": True,        # Guard active runners: only trim when profit/breakeven locked
    "target_weighted_sizing": False,  # Flat sizing by default (pure % of BP or $ amount)
    "min_r_units": 0.65,              # 0.65R minimum target floor to capture tight rotations
    "auto_buy_enabled": True,         # Master engine switch
    "stalker_auto_buy": False,        # 1m stalker real-money buys OFF: triggers are paper-logged only until validated
    "frank_mode": "SHADOW",           # "SHADOW" (Proving mode: telemetry only, ZERO trade interference) or "ENFORCE"
    "min_ml_conf": 25.0,              # Calibrated ML confidence floor for regularized Random Forest (default 25.0%)
    "allowed_timeframes": ["5m", "15m"], # Active automated day-trade timeframes
    "alpha_rotation_enabled": True,   # Timeframe-aware alpha replacement when at max slots
    "min_rotation_gap": 8.0           # Minimum ML Edge advantage required to swap (8.0% delta)
}

def load_config() -> dict:
    """Loads configuration with thread safety and automatic default population."""
    with _config_lock:
        if not os.path.exists(CONFIG_PATH):
            save_config_internal(DEFAULT_CONFIG)
            return dict(DEFAULT_CONFIG)
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = dict(DEFAULT_CONFIG)
            merged.update(data)
            return merged
        except Exception as e:
            print(f"[ConfigManager] Error reading {CONFIG_PATH}: {e}, using defaults.")
            return dict(DEFAULT_CONFIG)

def save_config_internal(cfg: dict) -> dict:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    return cfg

def save_config(updates: dict) -> dict:
    """Updates and saves persistent config."""
    with _config_lock:
        current = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    current = json.load(f)
            except Exception:
                current = dict(DEFAULT_CONFIG)
        else:
            current = dict(DEFAULT_CONFIG)
        
        current.update(updates)
        save_config_internal(current)
        return dict(current)

def calculate_sizing_allocation(buying_power: float, target_r: float = 1.0) -> dict:
    """
    Computes exact dollar allocation and risk weighting based on current user configuration.
    
    Modes:
    - DYNAMIC_TRIM: Dynamic 3-slot architecture (100% -> 50% -> 33%) with Breakeven Guarding.
    - PERCENT_BP: Fixed percentage of available buying power.
    - FIXED_DOLLAR: Fixed dollar amount per trade.
    """
    cfg = load_config()
    alloc_mode = cfg.get("alloc_mode", "DYNAMIC_TRIM")
    alloc_val = float(cfg.get("alloc_value", 33.3))
    target_weighted = bool(cfg.get("target_weighted_sizing", False))
    
    # Base dollar allocation from user command
    if alloc_mode == "DYNAMIC_TRIM":
        base_alloc = round(buying_power / 3.0, 2)
    elif alloc_mode == "PERCENT_BP":
        base_alloc = round(buying_power * (alloc_val / 100.0), 2)
    else:
        base_alloc = round(alloc_val, 2)
    
    # Enforce basic sanity bounds ($5 min)
    base_alloc = max(5.0, base_alloc)
    
    # Compute dynamic multiplier (if target weighting enabled)
    if target_weighted:
        if target_r >= 1.20:
            multiplier = 1.25
        elif target_r >= 0.55:
            multiplier = 1.00
        else:
            multiplier = 0.60
    else:
        multiplier = 1.00
    
    final_alloc = round(base_alloc * multiplier, 2)
    
    # Never exceed available buying power
    if buying_power > 0:
        final_alloc = min(final_alloc, buying_power)
        base_alloc = min(base_alloc, buying_power)
        
    return {
        "final_alloc": final_alloc,
        "base_alloc": base_alloc,
        "multiplier": multiplier,
        "alloc_mode": alloc_mode,
        "alloc_value": alloc_val,
        "target_weighted": target_weighted,
        "target_r": target_r,
        "max_concurrent": int(cfg.get("max_concurrent", 3)),
        "breakeven_guarded": bool(cfg.get("breakeven_guarded", True))
    }
