import os
import json
import threading

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auto_buyer_config.json")
_config_lock = threading.Lock()

DEFAULT_CONFIG = {
    "alloc_mode": "PERCENT_BP",       # "PERCENT_BP" (% of BP) or "FIXED_DOLLAR" ($ fixed)
    "alloc_value": 1.0,               # Default 1.0% of Buying Power or $20.00
    "target_weighted_sizing": True,   # ON by default: scales risk by expected target R
    "min_r_units": 0.65,              # 0.65R minimum target floor to capture tight rotations
    "max_concurrent": 5,              # Max concurrent 5 active algo positions
    "auto_buy_enabled": True          # Master engine switch
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
    
    Target-Weighted Sizing Multipliers (When Enabled):
    - target_r >= 1.20: 1.25x (Max Expansion upside)
    - 0.80 <= target_r < 1.20: 1.00x (Standard Wyckoff Swing)
    - 0.65 <= target_r < 0.80: 0.60x (Defensive sizing for tight range scalps)
    - target_r < 0.65: 0.60x (Floor defense)
    
    When Target-Weighted Sizing is Disabled:
    - 1.00x flat sizing across all trades.
    """
    cfg = load_config()
    alloc_mode = cfg.get("alloc_mode", "PERCENT_BP")
    alloc_val = float(cfg.get("alloc_value", 1.0))
    target_weighted = bool(cfg.get("target_weighted_sizing", True))
    
    # Base dollar allocation from user command
    if alloc_mode == "PERCENT_BP":
        base_alloc = round(buying_power * (alloc_val / 100.0), 2)
    else:
        base_alloc = round(alloc_val, 2)
    
    # Enforce basic sanity bounds ($5 min)
    base_alloc = max(5.0, base_alloc)
    
    # Compute dynamic multiplier
    if target_weighted:
        if target_r >= 1.20:
            multiplier = 1.25
        elif target_r >= 0.80:
            multiplier = 1.00
        elif target_r >= 0.65:
            multiplier = 0.60
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
        "target_r": target_r
    }
