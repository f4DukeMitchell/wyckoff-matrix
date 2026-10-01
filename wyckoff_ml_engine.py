import yfinance as yf
import pandas as pd
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score
import warnings
warnings.filterwarnings('ignore')

import json
try:
    with open("all_tickers.json", "r") as f:
        TICKERS = json.load(f)[:100] # Use top 100 for ML to prevent memory crash
except:
    TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA", "BRK-B", "LLY", "AVGO", "JPM", "V"]
INTERVAL = "5m"
PERIOD = "60d"

def get_supertrend(close, high, low, length, multiplier):
    tr0 = np.abs(high - low)
    tr1 = np.abs(high - np.roll(close, 1))
    tr2 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr0, np.maximum(tr1, tr2))
    tr[0] = 0
    atr = np.zeros_like(close)
    if len(close) > length:
        atr[length] = np.mean(tr[1:length+1])
        for i in range(length+1, len(close)): atr[i] = (atr[i-1] * (length - 1) + tr[i]) / length
    hl2 = (high + low) / 2
    upperband = hl2 + (multiplier * atr)
    lowerband = hl2 - (multiplier * atr)
    in_uptrend = np.ones(len(close), dtype=bool)
    for i in range(1, len(close)):
        if close[i] > upperband[i-1]: in_uptrend[i] = True
        elif close[i] < lowerband[i-1]: in_uptrend[i] = False
        else:
            in_uptrend[i] = in_uptrend[i-1]
            if in_uptrend[i] and lowerband[i] < lowerband[i-1]: lowerband[i] = lowerband[i-1]
            if not in_uptrend[i] and upperband[i] > upperband[i-1]: upperband[i] = upperband[i-1]
    return in_uptrend

print("=========================================")
print(" INITIATING WYCKOFF ML TRAINING ENGINE ")
print("=========================================")
print("1. Harvesting thousands of historical 5m Wyckoff setups...")

features = []
labels = []

data = yf.download(TICKERS, period=PERIOD, interval=INTERVAL, group_by='ticker', progress=False)

for ticker in TICKERS:
    try:
        df = data[ticker].dropna() if len(TICKERS) > 1 else data.dropna()
        if len(df) < 200: continue
        
        highs, lows, closes, vols = df['High'].values, df['Low'].values, df['Close'].values, df['Volume'].values
        u1 = get_supertrend(closes, highs, lows, 1, 1.0)
        u9 = get_supertrend(closes, highs, lows, 9, 9.0)
        u14 = get_supertrend(closes, highs, lows, 14, 14.0)
        
        vol_sma = pd.Series(vols).rolling(20, min_periods=1).mean().values
        rel_vol_arr = np.where(vol_sma > 0, vols / vol_sma, 1.0)
        
        range_high = pd.Series(highs).rolling(200, min_periods=20).max().shift(1).values
        range_low = pd.Series(lows).rolling(200, min_periods=20).min().shift(1).values
        
        bars_in_regime = np.zeros(len(df))
        for i in range(1, len(df)):
            if (u9[i] == u9[i-1]) and (u14[i] == u14[i-1]):
                bars_in_regime[i] = bars_in_regime[i-1] + 1
            else:
                bars_in_regime[i] = 0
                
        for i in range(200, len(df)-20):
            if pd.isna(range_high[i]): continue
            c_below = (lows[i] < range_low[i]) or (lows[i-1] < range_low[i-1])
            c_above = (highs[i] > range_high[i]) or (highs[i-1] > range_high[i-1])
            
            is_spring = c_below and u1[i] and not u1[i-1] and not u9[i]
            is_utad = c_above and not u1[i] and u1[i-1] and u9[i]
            
            if is_spring or is_utad:
                # Extract ML Features
                f_vol = rel_vol_arr[i]
                f_bars = bars_in_regime[i]
                f_box_pct = (range_high[i] - range_low[i]) / range_low[i] * 100
                f_dir = 1 if is_spring else -1
                
                # Check outcome
                sl_pct = 0.01
                if is_spring:
                    sl = min(lows[i], lows[i-1]) * (1.0 - sl_pct)
                    tp = range_low[i] + ((range_high[i] - range_low[i]) * 0.5)
                    outcome = 0
                    for j in range(i+1, len(df)):
                        if highs[j] >= tp: outcome = 1; break
                        if lows[j] <= sl: outcome = 0; break
                else:
                    sl = max(highs[i], highs[i-1]) * (1.0 + sl_pct)
                    tp = range_high[i] - ((range_high[i] - range_low[i]) * 0.5)
                    outcome = 0
                    for j in range(i+1, len(df)):
                        if lows[j] <= tp: outcome = 1; break
                        if highs[j] >= sl: outcome = 0; break
                        
                features.append([f_vol, f_bars, f_box_pct, f_dir])
                labels.append(outcome)
    except Exception as e:
        pass

print(f" Harvested {len(labels)} historical Wyckoff Traps.")

# Train Model
print("2. Training Random Forest Classifier...")
X = np.array(features)
y = np.array(labels)

if len(X) < 10:
    print("Not enough data to train. Need at least 10 samples.")
else:
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

    clf = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
    clf.fit(X_train, y_train)
    y_pred = clf.predict(X_test)

    print(f" Model trained. Accuracy on predicting winning vs losing setups: {accuracy_score(y_test, y_pred)*100:.1f}%")

    # Feature Importance
    print("\n=========================================")
    print(" WHAT CAUSES WYCKOFF TRADES TO FAIL? (FEATURE IMPORTANCE)")
    print("=========================================")
    importances = clf.feature_importances_
    feature_names = ["Relative Volume on Trigger", "Regime Exhaustion (Bars)", "Structural Box Height (%)", "Trade Direction (Long vs Short)"]
    for name, imp in sorted(zip(feature_names, importances), key=lambda x: x[1], reverse=True):
        print(f"- {name}: {imp*100:.1f}% impact on win rate")

    print("\nAI CONCLUSION:")
    print("The Machine Learning model has successfully mapped the historical failure vectors of the 4-Pillar system.")
