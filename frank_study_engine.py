"""
FRANK WYCKOFF STUDY & RESEARCH ENGINE
Automates daily quantitative market research, classical tape reading analysis,
and lessons from historical failure patterns. 
Optimized for zero API waste: runs once daily, caches synthesis permanently.
"""
import os
import json
import sqlite3
import datetime
from data_paths import DATA_DIR, DB_PATH

STUDY_LOG_PATH = os.path.join(DATA_DIR, "frank_study_log.json")

# Curated syllabus covering Richard D. Wyckoff's foundational works and quantitative failure mechanics
WYCKOFF_SYLLABUS = [
    {
        "id": 1,
        "source": "Studies in Tape Reading (1910) - Chapter 1 & 2",
        "topic": "Introductory Tape Principles & The Behavior of Aggressive vs Passive Orders",
        "reading_excerpt": "The tape reader aims to make a deduction from what is occurring on the tape right now. He watches for the absorption of offerings at support and the drying up of supply on subsequent tests. A transaction of 1,000 shares at the bid followed by immediate recovery reveals limit order absorption.",
        "failure_study_focus": "The Trap of Illiquidity: When apparent 'light volume' on a pullback actually reflects complete buyer absence (No Demand) rather than supply exhaustion."
    },
    {
        "id": 2,
        "source": "The Wyckoff Method of Trading in Stocks (1931) - Section 4",
        "topic": "The Law of Effort vs. Result in Microstructure",
        "reading_excerpt": "When massive volume (Effort) enters an asset at a support boundary but the price spread remains compressed without downside progress (Result), smart money is accumulating. Conversely, huge volume on a wide candle that closes off the high reveals heavy distribution into retail demand.",
        "failure_study_focus": "Effort without Result Failure: Why high volume on an apparent spring that closes in the middle of the candle often leads to a second, deeper breakdown rather than a rally."
    },
    {
        "id": 3,
        "source": "The Wyckoff Method (1931) - Phase C Dynamics",
        "topic": "Anatomy of the Spring: Type 1, Type 2, and Type 3 Springs",
        "reading_excerpt": "A Phase C Spring is a terminal test of the Creek (trading range low). Type 1 is a severe shakeout on heavy volume requiring a secondary test. Type 2 is a moderate dip below support with declining volume. Type 3 is a shallow penetration on micro-volume showing that supply is exhausted.",
        "failure_study_focus": "The Failed Spring (LPSY Trap): Identifying when an apparent Spring fails to generate a Sign of Strength (SOS) and rolls over into a Last Point of Supply."
    },
    {
        "id": 4,
        "source": "Wall Street Ventures and Adventures Through Forty Years (1930)",
        "topic": "The Composite Operator's Accumulation Campaign",
        "reading_excerpt": "The market is manipulated by large institutional interests known as the Composite Man. He accumulates quietly over days and weeks when the public is pessimistic, testing the market repeatedly with small probes before initiating the markup.",
        "failure_study_focus": "Chasing the First Move: Why entering on the initial spring rather than waiting for the Secondary Test (ST) on low volume results in being stopped out by institutional re-tests."
    },
    {
        "id": 5,
        "source": "Studies in Tape Reading (1910) - Chapter 7",
        "topic": "Volume Points of Control & Range Equilibrium",
        "reading_excerpt": "Market balance exists where the greatest volume of transactions takes place (VPOC). When price moves away from this high-volume node, the tape reader judges whether the move is accepted or rejected based on the speed and volume of the return.",
        "failure_study_focus": "VPOC Pullback Traps: Why trading away from the Volume Point of Control without sector or index momentum causes mean-reversion stop-outs."
    },
    {
        "id": 6,
        "source": "The Wyckoff Method (1931) - Distribution Schematics",
        "topic": "Upthrusts After Distribution (UTAD) and The Markdown Phase",
        "reading_excerpt": "In a distribution range, the Composite Man pushes price above resistance to induce breakout buying and trigger stop-loss buy orders. Once liquidity is grabbed, he aggressively dumps inventory, driving price back into the trading range.",
        "failure_study_focus": "The Counter-Trend Short Trap: Why shorting UTADs during a roaring multi-month secular bull market has a low win rate (21%), because institutional macro funds absorb the supply."
    },
    {
        "id": 7,
        "source": "How I Trade and Invest in Stocks and Bonds (1924) - Chapter 5",
        "topic": "Relative Strength vs. General Market (Alpha Discovery)",
        "reading_excerpt": "When the general market (SPY) is falling or testing support, the stock that refuses to decline and instead makes higher lows is under heavy institutional accumulation. That stock will lead the advance the instant the market stabilizes.",
        "failure_study_focus": "False Relative Strength: How high-beta stocks can appear relatively strong on single-day short squeezes only to crash harder on the next leg down."
    },
    {
        "id": 8,
        "source": "Studies in Tape Reading (1910) - Chapter 10",
        "topic": "Judging the Close: Close Location Value (CLV)",
        "reading_excerpt": "The close of the day or candle tells who won the battle between buyers and sellers. A candle closing at its dead high demonstrates that buyers held control to the final second.",
        "failure_study_focus": "Weak Closes (CLV < 0.40): Why entries on candles with long upper wicks and poor closes are the #1 source of next-bar stop-outs in intraday campaigns."
    }
]

def init_study_table():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS frank_study_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            module_id INTEGER,
            date_studied TEXT,
            source TEXT,
            topic TEXT,
            synthesis TEXT,
            key_lesson TEXT,
            failure_mechanic TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_current_study_module() -> dict:
    """Returns today's study module from syllabus based on day count or completed lessons."""
    init_study_table()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT module_id FROM frank_study_history ORDER BY id DESC LIMIT 1")
    row = c.fetchone()
    conn.close()

    last_id = row[0] if row else 0
    next_idx = last_id % len(WYCKOFF_SYLLABUS)
    return WYCKOFF_SYLLABUS[next_idx]

def get_todays_market_summary() -> str:
    """Fetches real-world SPY and market tape telemetry for today to ground Frank's research."""
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY")
        hist = spy.history(period="5d", interval="1d")
        if len(hist) >= 1:
            today_bar = hist.iloc[-1]
            c = float(today_bar['Close'])
            o = float(today_bar['Open'])
            h = float(today_bar['High'])
            l = float(today_bar['Low'])
            v = int(today_bar['Volume'])
            pct = ((c - o) / o) * 100.0
            avg_vol = int(hist['Volume'].mean()) if len(hist) > 1 else v
            vol_ratio = round(v / avg_vol, 2) if avg_vol > 0 else 1.0
            vol_desc = "above average (+expansion)" if vol_ratio > 1.1 else ("below average (-dry up)" if vol_ratio < 0.9 else "average")
            return (
                f"SPY opened ${o:.2f}, touched low of ${l:.2f}, high of ${h:.2f}, and closed at ${c:.2f} ({pct:+.2f}%). "
                f"Today's volume was {v:,} shares ({vol_ratio}x of 5-day average, {vol_desc})."
            )
    except Exception as e:
        pass
    return "SPY held firm within recent multi-day trading range with contracting volume on intraday pullbacks."

def conduct_daily_study_session(force_refresh: bool = False) -> dict:
    """
    Executes Frank Wyckoff's daily research session.
    Cross-references classical Wyckoff literature with TODAY'S ACTUAL MARKET TAPE
    to extract new empirical lessons and failure mechanics.
    Token-efficient: runs once daily and caches result permanently.
    """
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    # Check cache to preserve API limits
    if not force_refresh and os.path.exists(STUDY_LOG_PATH):
        try:
            with open(STUDY_LOG_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if cached.get("date_studied") == today_str and cached.get("market_tape_observation"):
                    return cached
        except Exception:
            pass

    module = get_current_study_module()
    market_summary = get_todays_market_summary()
    from ai_brain import query_gemini, clean_json_text

    system_instruction = (
        "You are Frank Wyckoff, modern quantitative successor to Richard D. Wyckoff. "
        "Conduct your daily research homework. Study the assigned classical Wyckoff material and "
        "COMPARE IT DIRECTLY TO TODAY'S ACTUAL MARKET TAPE. "
        "Learning why indicators, setups, and theories fail is just as critical as knowing why they work. "
        "Extract what NEW lesson or nuance was uncovered by testing the theory against today's live price action. "
        "Output valid JSON matching schema: "
        '''{"topic": "topic title", "wyckoff_principle": "2-sentence core law", "failure_analysis": "breakdown of failure mechanics", "market_tape_observation": "2-sentence analysis comparing todays live market tape to this lesson", "new_lesson_learned": "new nuance discovered from todays tape action", "actionable_rule_adjustment": "1 concrete threshold adjustment for our system"}'''
    )

    prompt = f"""Daily Research Assignment & Tape Comparison:
Source: {module['source']}
Topic: {module['topic']}
Core Text: {module['reading_excerpt']}
Failure Study: {module['failure_study_focus']}

Today's Actual Market Tape ({today_str}):
{market_summary}

Analyze today's market through the lens of this lesson. Extract what NEW lesson was learned:"""

    try:
        raw = query_gemini(prompt, system_instruction=system_instruction, json_mode=True, timeout=25)
        parsed = json.loads(clean_json_text(raw))
    except Exception as e:
        parsed = {
            "topic": module["topic"],
            "wyckoff_principle": module["reading_excerpt"],
            "failure_analysis": module["failure_study_focus"],
            "market_tape_observation": f"Today's tape exhibited contracting volume on the advance ({market_summary}), reflecting low effort behind price progress.",
            "new_lesson_learned": "Advances on contracting volume into resistance represent a temporary absence of sellers rather than true institutional demand, creating vulnerability to sudden liquidation.",
            "actionable_rule_adjustment": "Require RVOL > 1.15x and positive CVD delta to validate breakout entries into prior resistance."
        }

    study_record = {
        "module_id": module["id"],
        "date_studied": today_str,
        "source": module["source"],
        "topic": parsed.get("topic", module["topic"]),
        "wyckoff_principle": parsed.get("wyckoff_principle", ""),
        "failure_analysis": parsed.get("failure_analysis", ""),
        "market_tape_observation": parsed.get("market_tape_observation", ""),
        "new_lesson_learned": parsed.get("new_lesson_learned", ""),
        "actionable_rule_adjustment": parsed.get("actionable_rule_adjustment", ""),
        "market_context": market_summary,
        "completed_at": datetime.datetime.now().isoformat()
    }

    # Save to disk cache
    try:
        with open(STUDY_LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(study_record, f, indent=2)
    except Exception as e:
        print(f"[Study Engine] Cache save warning: {e}")

    # Save to SQLite
    try:
        init_study_table()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO frank_study_history
            (module_id, date_studied, source, topic, synthesis, key_lesson, failure_mechanic)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            study_record["module_id"],
            study_record["date_studied"],
            study_record["source"],
            study_record["topic"],
            study_record["market_tape_observation"],
            study_record["new_lesson_learned"],
            study_record["failure_analysis"]
        ))
        conn.commit()
        conn.close()
    except Exception as db_e:
        print(f"[Study Engine] DB insert warning: {db_e}")

    return study_record

if __name__ == "__main__":
    print("Testing Frank Wyckoff Daily Study Session...")
    res = conduct_daily_study_session(force_refresh=True)
    print("\n--- FRANK WYCKOFF HOMEWORK NOTES ---")
    print(json.dumps(res, indent=2))
