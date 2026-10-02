# Wyckoff AI Engine: Architecture & ML Pipeline

This document is a **living blueprint** of how the automated Wyckoff trading system operates, how the Machine Learning (ML) model trains itself, and how the two systems interact. It is designed to automatically update as new features are added or the ML model evolves.

---

## 1. The Base Algorithm (The "Eyes")
Before any Machine Learning happens, the system uses a strict, rules-based algorithm to scan the market. This is the "Eyes" of the bot. It does not think; it only looks for specific mathematical conditions:

1. **Phase B Lookback (The Trading Range):** The bot looks back over a set number of bars (e.g., 200 bars) to establish a firm Resistance (High) and Support (Low) channel.
2. **The Sweep (Phase C):** It waits for the price to temporarily break *below* Support (a Spring) or *above* Resistance (a UTAD).
3. **Volume Exhaustion:** It checks the Relative Volume. For a valid Spring/UTAD, the breakdown must happen on *low volume* (dry supply/demand), indicating it is a fake-out by institutions rather than a true breakout.
4. **Micro-Trend Reversal:** It uses a fast 1-period SuperTrend. The moment the price breaks back inside the range, the signal fires.

When these conditions are met, the Base Algorithm triggers an alert. In a non-ML system, this is where the process ends.

---

## Deep Dive: The Base Algorithm Mathematical Formula

The Base Algorithm evaluates the market on a candle-by-candle basis. To trigger a trade, a ticker must mathematically pass four strict conditions simultaneously.

### 1. The Phase B Channel (Establishing the Range)
Wyckoff theory relies on identifying institutional accumulation/distribution zones (Trading Ranges). The algorithm defines this mathematically using a rolling lookback window (default: 200 bars).
* **Resistance (Range_High)**: The absolute maximum High over the previous 200 bars.
* **Support (Range_Low)**: The absolute minimum Low over the previous 200 bars.

*Code Formula:*
Range_High = RollingMax(High, window=200).shift(1)
Range_Low = RollingMin(Low, window=200).shift(1)

### 2. The Phase C Break (The Fake-out)
Institutions hunt liquidity above Resistance and below Support. The algorithm waits for the price to temporarily pierce these boundaries.
* **Spring Setup:** The current or previous candle's Low must be strictly less than the Range_Low.
* **UTAD Setup:** The current or previous candle's High must be strictly greater than the Range_High.

### 3. Volume Exhaustion (Dry Supply/Demand)
A true breakout has massive volume. A Wyckoff Phase C fake-out happens on *exhausted* volume, proving that institutions are not supporting the move.
* **Volume Baseline (ol_sma)**: The 20-period Simple Moving Average of Volume.
* **Relative Volume (RV)**: Current Volume / vol_sma
* **The Rule (ol_dry)**: RV must be strictly less than the Volume Threshold (default: 1.2x).

### 4. Micro-Trend Reversal (The Exact Trigger)
We do not blindly buy just because the price drops below Support on low volume. We must wait for the price to reverse back *inside* the channel. The algorithm uses a highly sensitive **SuperTrend (1-Period ATR, 1.0 Multiplier)** to detect the exact tick the micro-trend flips.

* **SPRING TRIGGER (Long):**
  1. Price is below Range_Low
  2. ol_dry is True (Low volume)
  3. The 1-Period SuperTrend just flipped from Bearish to Bullish on this exact candle.
  4. The 9-Period SuperTrend is still Bearish (ensuring we are catching the absolute bottom, not entering late).

* **UTAD TRIGGER (Short):**
  1. Price is above Range_High
  2. ol_dry is True (Low volume)
  3. The 1-Period SuperTrend just flipped from Bullish to Bearish on this exact candle.
  4. The 9-Period SuperTrend is still Bullish (ensuring we are catching the absolute top).

### Risk Management Logic (The Bracket Order)
The moment the trigger fires, the algorithm calculates the bracket:
* **Entry:** Current Candle Close Price
* **Stop Loss (Spring):** The absolute lowest point of the Phase C dip, minus a 1% safety buffer.
* **Take Profit (Spring):** Target 1 is the 50% Mid-Line of the Phase B Channel. Target 2 is the Range_High Resistance line.
 

---

## 2. The Machine Learning Engine (The "Brain")
The Base Algorithm catches *every* Wyckoff setup, but not every setup is a winner. This is where the Machine Learning (Random Forest Classifier) steps in to act as a filter.

### Phase 1: Data Collection (Forward-Testing)
Every time the Base Algorithm fires an alert, the bot logs it into the `wyckoff_trades.db` database. Crucially, it records the exact "Environment" at the time of the trade:
* **Features Collected:** Timeframe, Direction, SuperTrend Regime (Bull/Bear), Options Put/Call Ratio, Options Sentiment, Time of Day, and Relative Volume.
* **Status:** Marked as `OPEN`.

### Phase 2: Ground Truth Tracking
A hidden background loop constantly monitors the live market price against the `OPEN` trades in the database.
* If the price hits the Take Profit, the database updates the trade to **`WIN`**.
* If the price hits the Stop Loss, it updates to **`LOSS`**.
This provides the ML model with "Labeled Data" (Ground Truth).

### Phase 3: Self-Training
Once the database accumulates enough closed trades (e.g., 50-100+ records), the Python `scikit-learn` Random Forest model wakes up. 
It analyzes the historical Features against the Outcomes (Win/Loss) to find hidden correlations that human eyes can't see. For example, it might mathematically discover:
> *"When a 5m Spring occurs between 12:00 PM and 1:00 PM, and the Put/Call ratio is above 1.1, the Win Rate drops to 12%."*

### Phase 4: Live Inference (The Filter)
Now, the ML model is trained. When the Base Algorithm finds a *new* setup tomorrow, it does not send the alert immediately. Instead, it asks the ML Brain:
* **Algorithm:** *"I found a 5m Spring on AAPL. Here is the current volume, time, and options flow."*
* **ML Brain:** *"Based on my training, this specific combination of variables only has a 34% probability of hitting the Take Profit. **Block the trade.**"*

Over time, as the bot logs thousands of trades across different market regimes, the ML model's probability predictions become sharper, naturally filtering out whipsaws and false breakouts.

---

## 3. System Change Log & Model Evolution
*This section tracks core architectural changes to the algorithm and ML parameters.*

* **v10.4 (Oct 02, 2026):** UI Redundancy cleanup. Options Flow and Trade Logs integrated into main scanner view.
* **v10.3 (Oct 02, 2026):** Added Live Reports Tab to track forward-tested DB outcomes (Win/Loss records) which serve as the Ground Truth for the ML engine.
* **v10.0 (Oct 02, 2026):** Bot upgraded from single-timeframe to concurrent Multi-Timeframe scanning (5m, 15m, 1h, 1d). Database schema updated to track timeframes independently for isolated ML training.
* **v9.0 (Initial):** Base Wyckoff Algorithm established with Volume Exhaustion and Phase B boundary tracking.
