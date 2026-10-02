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
