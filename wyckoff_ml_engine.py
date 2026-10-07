import os
import json
import pickle
import datetime
import sqlite3
import pandas as pd
from sklearn.ensemble import RandomForestClassifier

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wyckoff_trades.db")
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wyckoff_model.pkl")

def init_history_table():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS ml_model_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT,
                timestamp TEXT,
                training_samples INTEGER,
                win_rate_before REAL,
                model_accuracy REAL,
                top_feature TEXT,
                feature_importances_json TEXT,
                rules_generated TEXT,
                notes TEXT
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error initializing ml_model_history: {e}")

def train_and_upgrade_model(trigger_reason="Daily Post-Market Evolution"):
    """
    Automated Machine Learning Upgrade Engine:
    1. Ingests all closed trades with ground-truth outcomes from SQLite.
    2. Trains a Random Forest Classifier on institutional features.
    3. Serializes the updated model to disk for real-time scoring.
    4. Records an immutable audit log entry in ml_model_history.
    5. Formats an AI Evolution Briefing for Telegram.
    """
    init_history_table()
    
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query("SELECT * FROM alerts WHERE outcome IN ('WIN', 'LOSS', 'BREAKEVEN')", conn)
        
        if len(df) < 10:
            conn.close()
            return False, f"Not enough closed trade data to train (needs >= 10, currently {len(df)})."
            
        # Ground-truth targets:
        # Only true Take-Profit completions are WIN (target = 1).
        # Stop-at-Breakeven scratch trades (0.0R) and full Losses (-1.0R) are target = 0.
        df['target'] = (df['outcome'] == 'WIN').astype(int)
        df['dir_num'] = (df['direction'] == 'LONG').astype(int)
        
        # Differentiated Sample Weights for ML:
        # Full WIN: weight 1.0 (reinforce high-probability setup features)
        # Full LOSS: weight 1.0 (penalize toxic failure features)
        # BREAKEVEN: weight 0.5 (scratch trade reached +0.75R ratchet before stalling;
        #            not a winner, but setup had positive excursion, so down-weight penalty)
        sample_weights = df['outcome'].map({
            'WIN': 1.0,
            'LOSS': 1.0,
            'BREAKEVEN': 0.5
        }).fillna(1.0)
        
        if 'effort_vs_result' not in df.columns:
            df['effort_vs_result'] = 1.0
        else:
            df['effort_vs_result'] = df['effort_vs_result'].fillna(1.0)
            
        if 'test_vol_ratio' not in df.columns:
            df['test_vol_ratio'] = 1.0
        else:
            df['test_vol_ratio'] = df['test_vol_ratio'].fillna(1.0)

        feature_map = {
            'bars_in_regime': 'Trend Exhaustion (Bars)',
            'vwap_distance': 'VWAP Stretch (%)',
            'atr_expansion': 'ATR Expansion (Vol)',
            'effort_vs_result': 'Effort vs Result (Absorption)',
            'test_vol_ratio': 'Secondary Test Vol Ratio',
            'hour_of_day': 'Hour of Day (EST)',
            'dir_num': 'Direction (Long/Short)'
        }
        cols = list(feature_map.keys())
        clean_df = df.dropna(subset=cols)
        
        if len(clean_df) < 10:
            conn.close()
            return False, "Not enough clean feature rows to train."
            
        X = clean_df[cols]
        y = clean_df['target']
        weights = sample_weights.loc[clean_df.index]
        
        # Train Random Forest Classifier with sample weights
        rf = RandomForestClassifier(n_estimators=100, max_depth=4, random_state=42)
        rf.fit(X, y, sample_weight=weights)
        
        acc = float((rf.predict(X) == y).mean() * 100)
        
        wins = int((df['outcome'] == 'WIN').sum())
        losses = int((df['outcome'] == 'LOSS').sum())
        breakevens = int((df['outcome'] == 'BREAKEVEN').sum())
        total_closed = len(df)
        
        all_win_rate = (wins / total_closed * 100.0) if total_closed > 0 else 0.0
        directional_win_rate = (wins / (wins + losses) * 100.0) if (wins + losses) > 0 else 0.0
        be_rate = (breakevens / total_closed * 100.0) if total_closed > 0 else 0.0
        
        importances = {feature_map[k]: round(float(imp * 100), 1) for k, imp in zip(cols, rf.feature_importances_)}
        sorted_imp = sorted(importances.items(), key=lambda x: x[1], reverse=True)
        top_feat = sorted_imp[0][0]
        
        # Save model artifact
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(rf, f)
            
        # Determine Version String
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM ml_model_history")
        v_num = c.fetchone()[0] + 1
        version_str = f"v1.{v_num}"
        
        # Generate Algorithmic Adaptation Rules
        rules = [
            f"Heavily weight {top_feat} ({sorted_imp[0][1]}% influence) on Phase C reversals",
            f"Secondary filter: {sorted_imp[1][0]} ({sorted_imp[1][1]}% influence)",
            f"Breakeven Ratchet active: +0.75R triggers stop move to entry ({breakevens} scratches protected)"
        ]
        
        notes_str = f"{trigger_reason} | {wins}W - {losses}L - {breakevens}BE (Ex-BE WR: {directional_win_rate:.1f}%, BE Rate: {be_rate:.1f}%)"

        # Insert Audit Record
        c.execute('''
            INSERT INTO ml_model_history 
            (version, timestamp, training_samples, win_rate_before, model_accuracy, top_feature, feature_importances_json, rules_generated, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            version_str,
            datetime.datetime.now().isoformat(),
            len(clean_df),
            round(all_win_rate, 1),
            round(acc, 1),
            top_feat,
            json.dumps(importances),
            json.dumps(rules),
            notes_str
        ))
        conn.commit()
        conn.close()
        
        # Format Telegram Report
        report = (
            f"🤖 *WYCKOFF AI: MODEL EVOLUTION COMPLETE*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• *Model Version:* `{version_str}`\n"
            f"• *Trigger:* {trigger_reason}\n"
            f"• *Training Dataset:* {len(clean_df)} Closed Trades\n"
            f"  ▸ 🟢 Wins (Take Profit): *{wins}* ({all_win_rate:.1f}%)\n"
            f"  ▸ 🔴 Losses (Stop Out): *{losses}*\n"
            f"  ▸ 🛡️ Breakevens (Scratch): *{breakevens}* ({be_rate:.1f}%)\n"
            f"• *Directional Win Rate (Ex-BE):* *{directional_win_rate:.1f}%*\n"
            f"• *Model Fitting Accuracy:* {acc:.1f}%\n\n"
            f"🏆 *Top Predictive Features:*\n"
        )
        for name, imp_val in sorted_imp[:3]:
            report += f"  ▸ {name}: *{imp_val}%*\n"
            
        report += (
            f"\n🛡️ *Updated Execution Rules:*\n"
            f"  1. Prioritize setups with high {top_feat}\n"
            f"  2. Filter sub-threshold entries before Telegram alert\n"
            f"  3. Treat Breakevens as non-winners with 0.5x sample weight\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Audit log permanently saved to `ml_model_history`."
        )
        
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] ML Model successfully upgraded to {version_str} ({acc:.1f}% acc)")
        return True, report
        
    except Exception as e:
        print(f"Error during ML model upgrade: {e}")
        return False, str(e)

if __name__ == "__main__":
    success, rep = train_and_upgrade_model("Manual CLI Invocation")
    try:
        print("\n" + rep)
    except UnicodeEncodeError:
        print("\n" + rep.encode('ascii', errors='replace').decode('ascii'))
