"""
Frank Wyckoff Market Pulse Engine
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Transforms Market Pulse into Frank's Live 15-Minute Tape Desk:
A high-energy, Jim Cramer-style market analysis infused with Richard Wyckoff's
Composite Man microstructure discipline.

Core Rule Enforced:
"As soon as an article is published online, it is usually too late to trade,
or the Composite Man has engineered the headline to shake retail out of their
longs at support or manufacture exit liquidity for a distribution pump-and-dump."

Frank audits the tape and headlines every 15 minutes, uncovering what smart
money is actually doing behind the media narratives.
"""

import os
import json
import time
import datetime
from typing import Dict, List, Any

from data_paths import DATA_DIR
import market_news
import frank_sector_guard

PULSE_NOTES_PATH = os.path.join(DATA_DIR, "frank_pulse_notes.json")

def load_pulse_notes() -> List[Dict[str, Any]]:
    if os.path.exists(PULSE_NOTES_PATH):
        try:
            with open(PULSE_NOTES_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []

def save_pulse_notes(notes: List[Dict[str, Any]]):
    try:
        os.makedirs(os.path.dirname(PULSE_NOTES_PATH), exist_ok=True)
        with open(PULSE_NOTES_PATH, "w", encoding="utf-8") as f:
            json.dump(notes[:25], f, indent=2)  # Keep last 25 notes
    except Exception as e:
        print(f"[FrankPulse] Error saving notes: {e}")

def _extract_price(val: Any) -> float:
    if isinstance(val, dict):
        return float(val.get("price") or val.get("last") or 0.0)
    try:
        return float(val or 0.0)
    except Exception:
        return 0.0

def generate_frank_pulse_note(force: bool = False) -> Dict[str, Any]:
    """
    Analyzes breaking wire articles against live tape geometry and sector health.
    Produces a fresh 15-minute Frank Wyckoff desk note.
    """
    now_et = frank_sector_guard.get_est_now()
    now_iso = now_et.isoformat()
    time_str = now_et.strftime("%I:%M %p EST")

    # Throttle: Only generate new note if >= 14 minutes since last note (unless force=True)
    existing_notes = load_pulse_notes()
    if not force and existing_notes:
        last_t_str = existing_notes[0].get("timestamp", "")
        try:
            import pandas as pd
            last_t = pd.to_datetime(last_t_str).to_pydatetime()
            elapsed_min = (now_et.replace(tzinfo=None) - last_t.replace(tzinfo=None)).total_seconds() / 60.0
            if elapsed_min < 14.0:
                return existing_notes[0]
        except Exception:
            pass

    # 1. Gather Market Data & Breaking News
    pulse_data = market_news.get_market_pulse(['SPY', 'QQQ', 'NVDA', 'AAPL', 'TSLA', 'AMZN', 'MSFT', 'SMH', 'XLV'])
    headlines = pulse_data.get("headlines", [])
    indices = pulse_data.get("indices", {})
    spy_p = _extract_price(indices.get("SPY"))
    qqq_p = _extract_price(indices.get("QQQ"))
    iwm_p = _extract_price(indices.get("IWM"))
    dia_p = _extract_price(indices.get("DIA"))

    # 2. Sector Health & Tape Audit
    sector_summary = frank_sector_guard.get_sector_guard_summary()
    quarantined = sector_summary.get("quarantined_sectors", [])
    spy_tape = frank_sector_guard.audit_sector_tape("SPY")
    spy_vwap = spy_tape.get("vwap", spy_p)
    spy_pct = spy_tape.get("pct_vs_vwap", 0.0)
    if spy_p == 0.0 and spy_tape.get("current_price", 0.0) > 0:
        spy_p = spy_tape.get("current_price")

    # Regime
    if spy_pct >= 0.15:
        regime = "RISK-ON // EXPANDING ABOVE VWAP"
        regime_color = "#00ff88"
    elif spy_pct <= -0.25:
        regime = "DEFENSIVE // DISTRIBUTING BELOW VWAP"
        regime_color = "#ff4444"
    else:
        regime = "NEUTRAL CONSOLIDATION // PINNED AT VWAP"
        regime_color = "#ffd700"

    # 3. Analyze Breaking Headlines for Composite Man Traps
    top_articles = headlines[:4] if headlines else []
    article_summaries = []
    trap_findings = []

    for art in top_articles:
        title = art.get("title", "")
        sent = art.get("sentiment", "NEUTRAL")
        t_low = title.lower()

        # Composite Man Trap Heuristics
        if any(w in t_low for w in ["surge", "jump", "soar", "record", "all-time high", "boom"]):
            trap_desc = f"BULL TRAP ALERT: Media promoting '{title[:55]}...' into resistance. Composite Man feeding exit liquidity to breakout buyers. Never chase green market orders."
            trap_findings.append(trap_desc)
        elif any(w in t_low for w in ["slump", "plunge", "warn", "fear", "crisis", "pressure", "drop"]):
            trap_desc = f"SHAKEOUT ALERT: Media amplifying '{title[:55]}...' at support. Classical Wyckoff Spring territory: smart money absorbs panic before marking up."
            trap_findings.append(trap_desc)
        else:
            trap_desc = f"CHOP NOISE: '{title[:55]}...' is retail media noise. Ignore headlines; follow tape volume."

        article_summaries.append({
            "title": title,
            "sentiment": sent,
            "source": art.get("source", "MarketWire"),
            "published": art.get("time_str", ""),
            "trap_insight": trap_desc
        })

    if not trap_findings:
        trap_findings.append("COMPOSITE MAN WATCH: No obvious media pump. Big money is testing inventory quietly at range boundaries.")

    # 4. Synthesize Frank's Desk Voice (Jim Cramer Energy + Richard Wyckoff Microstructure)
    q_text = f"QUARANTINED ({', '.join([q['sector_name'] for q in quarantined])})" if quarantined else "ALL 12 SECTORS ACTIVE"
    
    cramer_take = (
        f"LISTEN TO ME! Look at the tape right now at {time_str}! "
        f"The financial media is blasting headlines, but SPY is at ${spy_p:.2f} ({spy_pct:+.2f}% vs VWAP). "
        f"Here is the iron law retail always forgets: THE COMPOSITE MAN DOES NOT TRADE NEWS. "
        f"By the time an article hits MarketWatch or CNBC, the institutional move is ALREADY IN THE BOOKS. "
        f"They push horror stories to shake retail out at support, and they run breathless upgrades when they need someone to dump their bags onto! "
        f"Our playbook is simple: Sector Status is {q_text}. We don't chase headline momentum; we let sellers exhaust themselves, wait for the spring test on low volume, and strike with structural R-advantage!"
    )

    actionable_playbook = [
        "1. Never buy green market orders after an upbeat article drops — that is classic distribution trap territory.",
        "2. Look for high-volume panic selloffs into established daily Creek support (Phase C Spring setups).",
        "3. Protect active positions with the 5-Tier Exit Guardian: once Tier 3 triggers (+0.50R), breakeven is permanently locked.",
        f"4. Sector Discipline: {q_text}. Avoid lagging sectors under distribution."
    ]

    note = {
        "id": f"frank_note_{int(time.time())}",
        "timestamp": now_iso,
        "time_str": time_str,
        "regime": regime,
        "regime_color": regime_color,
        "spy_tape": f"SPY: ${spy_p:.2f} ({spy_pct:+.2f}% vs VWAP)",
        "qqq_tape": f"QQQ: ${qqq_p:.2f}",
        "iwm_tape": f"IWM: ${iwm_p:.2f}",
        "quarantine_status": q_text,
        "cramer_monologue": cramer_take,
        "trap_findings": trap_findings[:2],
        "top_articles": article_summaries,
        "playbook": actionable_playbook
    }

    # Prepend to list
    existing_notes.insert(0, note)
    save_pulse_notes(existing_notes)
    return note
