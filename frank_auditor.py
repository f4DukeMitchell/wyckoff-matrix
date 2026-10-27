"""
Frank Model & Tape Auditor
Performs rigorous quantitative cross-validation, tape feature hygiene checks,
and directional skew diagnosis for the MSR Wyckoff Random Forest models.
"""
import os
import sqlite3
import json
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import StratifiedKFold, cross_val_score
from data_paths import DB_PATH

def compute_model_and_tape_telemetry() -> dict:
    """Computes exact statistical metrics across historical training data and tape features."""
    if not os.path.exists(DB_PATH):
        return {"error": "Database not found."}

    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("SELECT * FROM alerts WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN') AND (timeframe IS NULL OR timeframe != '1m')", conn)
    finally:
        conn.close()

    if len(df) < 15:
        return {"error": f"Insufficient training rows ({len(df)} < 15)."}

    # 1. Tape Features Integrity
    tape_cols = [
        'cvd_delta_ratio', 'candle_clv', 'rvol_tod', 
        'vpoc_distance_pct', 'st_volume_ratio', 'effort_vs_result',
        'institutional_block_ratio'
    ]
    tape_stats = {}
    for col in tape_cols:
        if col in df.columns:
            vals = df[col].fillna(0.0)
            zero_rate = float((vals == 0.0).mean() * 100.0)
            one_rate = float((vals == 1.0).mean() * 100.0)
            tape_stats[col] = {
                "mean": round(float(vals.mean()), 3),
                "std": round(float(vals.std()), 3),
                "min": round(float(vals.min()), 2),
                "max": round(float(vals.max()), 2),
                "default_pct": round(max(zero_rate, one_rate), 1)
            }

    # 2. Fill defaults for tabular model
    col_defaults = {
        'effort_vs_result': 1.0, 'test_vol_ratio': 1.0, 'days_to_rebalance': 45,
        'is_triple_witching': 0, 'dealer_gamma_regime': 0, 'gamma_wall_dist_pct': 0.0,
        'moc_surge_score': 0.0, 'institutional_block_ratio': 1.0, 'cvd_delta_ratio': 0.0,
        'rvol_tod': 1.0, 'penetration_depth_pct': 0.0, 'candle_clv': 0.70,
        'consumed_atr': 1.0, 'st_volume_ratio': 1.0, 'vpoc_distance_pct': 0.0,
        'mtf_atr_ratio': 1.0, 'trend_exhaustion': 3.0, 'vix_slope': 0.0,
        'rs_alpha': 0.0, 'pcr': 0.70, 'vwap_slope': 0.0, 'orb_regime_code': 0,
        'atr_headroom': 0.50, 'sector_ret_pct': 0.0, 'sector_aligned': 1
    }
    for c_name, def_val in col_defaults.items():
        if c_name not in df.columns:
            df[c_name] = def_val
        else:
            df[c_name] = df[c_name].fillna(def_val)

    feature_cols = [
        'bars_in_regime', 'vwap_distance', 'atr_expansion', 'effort_vs_result',
        'test_vol_ratio', 'pcr', 'vwap_slope', 'orb_regime_code', 'atr_headroom',
        'sector_ret_pct', 'sector_aligned', 'cvd_delta_ratio', 'rvol_tod',
        'penetration_depth_pct', 'candle_clv', 'consumed_atr', 'st_volume_ratio',
        'vpoc_distance_pct', 'mtf_atr_ratio', 'trend_exhaustion', 'vix_slope',
        'rs_alpha', 'days_to_rebalance', 'is_triple_witching', 'dealer_gamma_regime',
        'gamma_wall_dist_pct', 'moc_surge_score', 'institutional_block_ratio'
    ]

    df['target'] = (df['outcome'] == 'WIN').astype(int)
    df['dir_num'] = (df['direction'] == 'LONG').astype(int)
    feature_cols.append('dir_num')

    clean_df = df.dropna(subset=feature_cols)
    X = clean_df[feature_cols]
    y = clean_df['target']

    sample_weights = clean_df['outcome'].map({'WIN': 1.0, 'LOSS': 1.0, 'BREAKEVEN': 0.5}).fillna(1.0)

    # In-Sample Training
    rf = RandomForestClassifier(n_estimators=100, max_depth=4, random_state=42)
    rf.fit(X, y, sample_weight=sample_weights)
    in_sample_acc = float((rf.predict(X) == y).mean() * 100.0)

    # 5-Fold Stratified Out-of-Fold Cross Validation
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    cv_scores = cross_val_score(rf, X, y, cv=cv, scoring='accuracy', params={'sample_weight': sample_weights}) * 100.0
    oof_mean = float(cv_scores.mean())
    oof_std = float(cv_scores.std())
    overfit_gap = float(in_sample_acc - oof_mean)

    # Directional Breakdown
    long_clean = clean_df[clean_df['dir_num'] == 1]
    short_clean = clean_df[clean_df['dir_num'] == 0]
    long_wr = float((long_clean['target'] == 1).mean() * 100.0) if len(long_clean) > 0 else 0.0
    short_wr = float((short_clean['target'] == 1).mean() * 100.0) if len(short_clean) > 0 else 0.0

    return {
        "total_samples": len(clean_df),
        "in_sample_accuracy": round(in_sample_acc, 2),
        "out_of_fold_cv_accuracy": round(oof_mean, 2),
        "cv_std": round(oof_std, 2),
        "overfit_gap_pct": round(overfit_gap, 2),
        "long_samples": len(long_clean),
        "long_win_rate": round(long_wr, 1),
        "short_samples": len(short_clean),
        "short_win_rate": round(short_wr, 1),
        "tape_feature_health": tape_stats
    }

def generate_frank_model_audit_report(telemetry: dict) -> str:
    """Uses Frank (Gemini 3.5 Flash) to generate a comprehensive institutional model audit report."""
    from ai_brain import query_gemini

    system_instruction = (
        "You are Frank, Lead Quantitative Auditor & Chief Risk Officer for the MSR Wyckoff Terminal. "
        "Review the empirical statistical metrics of the Random Forest training process and tape features. "
        "Provide an institutional, candid, rigorous model audit. "
        "Address: "
        "1. Tape Reading & Microstructure Quality (Are tape indicators real or polluted with defaults?). "
        "2. Training Integrity & Overfit Gap (In-sample vs 5-fold cross-validation). "
        "3. Directional Asymmetry (Long vs Short performance). "
        "4. Exact Mathematical Prescriptions to train the model properly."
    )

    prompt = f"""Empirical Model Training Telemetry:
{json.dumps(telemetry, indent=2)}

Perform a complete Model Validation & Tape Reading Audit:"""

    try:
        report = query_gemini(prompt, system_instruction=system_instruction, json_mode=False, timeout=25)
        return report
    except Exception as e:
        return f"Frank Audit Error: {e}"
