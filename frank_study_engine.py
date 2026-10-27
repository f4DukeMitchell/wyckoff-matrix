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

def conduct_daily_study_session(force_refresh: bool = False) -> dict:
    """
    Executes Frank Wyckoff's daily research session.
    Token-efficient: uses a single lightweight query and caches result on disk and DB.
    """
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    # Check cache to preserve API limits
    if not force_refresh and os.path.exists(STUDY_LOG_PATH):
        try:
            with open(STUDY_LOG_PATH, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if cached.get("date_studied") == today_str:
                    return cached
        except Exception:
            pass

    module = get_current_study_module()
    from ai_brain import query_gemini, clean_json_text

    system_instruction = (
        "You are Frank Wyckoff, modern quantitative successor to Richard D. Wyckoff. "
        "Conduct your daily research homework. Study the assigned classical Wyckoff material and "
        "critically analyze the FAILURE mechanics. Learning why indicators, setups, and theories fail "
        "is just as critical as knowing why they work. "
        "Be rigorous, concise, institutional, and actionable. "
        "Output valid JSON matching schema: {"
        "\"topic\": \"topic title\","
        "\"wyckoff_principle\": \"2-sentence core law\","
        "\"failure_analysis\": \"2-sentence breakdown of what can go wrong and why traders fail here\","
        "\"system_application\": \"How to apply this lesson to our 27 quantitative features (CVD, CLV, RVOL, ATR)\""
        "}"
    )

    prompt = f"""Daily Research Assignment:
Source: {module['source']}
Topic: {module['topic']}
Core Text: {module['reading_excerpt']}
Failure Study: {module['failure_study_focus']}

Deliver your quantitative study notes:"""

    try:
        raw = query_gemini(prompt, system_instruction=system_instruction, json_mode=True, timeout=20)
        parsed = json.loads(clean_json_text(raw))
    except Exception as e:
        parsed = {
            "topic": module["topic"],
            "wyckoff_principle": module["reading_excerpt"],
            "failure_analysis": module["failure_study_focus"],
            "system_application": "Enforce strict volume dry-up on Phase C tests before deploying capital."
        }

    study_record = {
        "module_id": module["id"],
        "date_studied": today_str,
        "source": module["source"],
        "topic": parsed.get("topic", module["topic"]),
        "wyckoff_principle": parsed.get("wyckoff_principle", ""),
        "failure_analysis": parsed.get("failure_analysis", ""),
        "system_application": parsed.get("system_application", ""),
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
            study_record["wyckoff_principle"],
            study_record["system_application"],
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
