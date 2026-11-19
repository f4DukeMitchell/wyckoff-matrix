"""
FRANK DESK REPORT  -  Post-Market Desk Review (v2)
===================================================
Replaces the old "Executive Briefing" email.

Design principles (learned from what was wrong with v1):
  1. NEVER recycle a prior session. If no trades closed today, the report says so and
     shows the signal funnel (what fired, what passed the gate, why nothing was taken).
  2. Every number is computed deterministically from broker fills + 5m bars. The LLM only
     writes the narrative, is fed ONLY today's facts, and is rejected if it names a ticker
     that did not trade today.
  3. Each trade gets a forensic card: real MFE/MAE, time in trade, what price did AFTER
     we exited, SPY/sector context, and a plain-English verdict.
  4. "Learning" = an Edge Tracker that measures slices of our own live results with
     sample sizes + confidence intervals + counterfactual P&L, and a Learning Health
     panel that audits the ML pipeline itself (proxy features, stale labels, placeholder data).
  5. Changes we ship are tracked in a changelog and measured before/after.
"""
import os
import json
import math
import sqlite3
import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    from zoneinfo import ZoneInfo
    NY = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    NY = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EDGE_STATE_PATH = os.path.join(BASE_DIR, "desk_edge_state.json")
CHANGELOG_PATH = os.path.join(BASE_DIR, "desk_changelog.json")
EXCLUDE_SYMBOLS = {"AMC", "APE", "NKE"}
ARCH_START = "2026-11-10"          # first full session on the current 6-tier guardian + 50% floor
DEFAULT_RISK_PCT = 0.015           # pnl_engine convention when the real stop is unknown

# Features that encode *when* a row was recorded rather than *what the setup looked like*.
PROXY_FEATURES = {
    "Index Rebalance Proximity (Days)": "defaults to 45 on backfilled rows, so it mostly encodes data era",
    "Triple Witching Week Flag": "calendar flag",
    "MOC Auction Closing Surge": "only known at the close",
}

DEFAULT_CHANGELOG = [
    {"date": "2026-11-02", "change": "30% BP per slot + sector circuit breaker + 35% ML floor"},
    {"date": "2026-11-05", "change": "ST dry-up gate, midday blackout, ML floor raised to 50%"},
    {"date": "2026-11-06", "change": "6-tier Exit Guardian (tiers 1-3 stop-only, bank 50%/35% at T4/T5)"},
]


# ----------------------------------------------------------------------------- helpers
def now_et() -> datetime.datetime:
    return datetime.datetime.now(NY) if NY else datetime.datetime.utcnow() - datetime.timedelta(hours=5)


def wilson(wins: int, n: int, z: float = 1.96):
    if n == 0:
        return 0.0, 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _f(x, default=None):
    try:
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return default
        return float(x)
    except Exception:
        return default


def money(x: float) -> str:
    return f"-${abs(x):,.2f}" if x < 0 else f"+${x:,.2f}"


def pct(x: float) -> str:
    return f"{x * 100:.0f}%"


# ----------------------------------------------------------------------------- data
def load_live_trades(pnl_data: Optional[dict] = None) -> pd.DataFrame:
    if pnl_data is None:
        from pnl_engine import fetch_pnl_station_data
        pnl_data = fetch_pnl_station_data()
    rows = []
    for t in pnl_data.get("trades", []) or []:
        if t.get("is_historical_anchor"):
            continue
        if "OPT" in str(t.get("timeframe", "")).upper() or "C0" in str(t.get("ticker")) or "P0" in str(t.get("ticker")):
            continue
        if t.get("source") != "AUTO_VM" or t.get("ticker") in EXCLUDE_SYMBOLS:
            continue
        if t.get("era") != "V12_LIVE":
            continue
        rows.append(t)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for c in ["entry_price", "exit_price", "shares", "cost_basis", "net_pnl_dollar", "pnl_r"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["entry_ts"] = pd.to_datetime(df["entry_timestamp"], utc=True, errors="coerce").dt.tz_convert("America/New_York")
    df["exit_ts"] = pd.to_datetime(df["exit_timestamp"], utc=True, errors="coerce").dt.tz_convert("America/New_York")
    df["hold_min"] = (df["exit_ts"] - df["entry_ts"]).dt.total_seconds() / 60.0
    df["entry_hhmm"] = df["entry_ts"].dt.strftime("%H:%M")
    df["exit_hhmm"] = df["exit_ts"].dt.strftime("%H:%M")
    df["exit_date"] = df["exit_ts"].dt.strftime("%Y-%m-%d")
    df["is_win"] = df["net_pnl_dollar"] > 0.05
    df["is_loss"] = df["net_pnl_dollar"] < -0.05
    df["opening_bell"] = df["entry_hhmm"] <= "09:46"
    df["eod_flatten"] = df["exit_hhmm"] >= "15:55"
    return df.sort_values("exit_ts").reset_index(drop=True)


def enrich_with_alerts(df: pd.DataFrame) -> pd.DataFrame:
    """Link each broker round-trip to the alert row that triggered it (real stop, real ML score, features)."""
    for c in ["real_stop", "real_target", "real_ml_conf", "candle_clv", "rvol_tod", "vwap_distance",
              "cvd_delta_ratio", "sector_aligned", "spy_bullish", "alert_regime"]:
        df[c] = None
    if df.empty:
        return df
    try:
        from data_paths import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        al = pd.read_sql_query(
            "SELECT ticker, timestamp, entry_price, stop_loss, initial_stop_loss, take_profit, ml_confidence, "
            "candle_clv, rvol_tod, vwap_distance, cvd_delta_ratio, sector_aligned, spy_bullish, regime, user_active "
            "FROM alerts WHERE timestamp >= ?", conn, params=(df["entry_ts"].min().strftime("%Y-%m-%d"),))
        conn.close()
    except Exception as e:
        print(f"[DeskReport] alert link unavailable: {e}")
        return df
    if al.empty:
        return df
    al["ts"] = pd.to_datetime(al["timestamp"], utc=True, errors="coerce").dt.tz_convert("America/New_York")
    for i, r in df.iterrows():
        cand = al[(al["ticker"] == r["ticker"]) & (al["ts"] <= r["entry_ts"] + pd.Timedelta(minutes=5))
                  & (al["ts"] >= r["entry_ts"] - pd.Timedelta(minutes=30))]
        if cand.empty:
            continue
        cand = cand.assign(_act=cand["user_active"].fillna(0)).sort_values(["_act", "ts"], ascending=[False, False])
        a = cand.iloc[0]
        stop = _f(a["initial_stop_loss"]) or _f(a["stop_loss"])
        if stop and stop < r["entry_price"]:
            df.at[i, "real_stop"] = stop
        df.at[i, "real_target"] = _f(a["take_profit"])
        df.at[i, "real_ml_conf"] = _f(a["ml_confidence"])
        for c in ["candle_clv", "rvol_tod", "vwap_distance", "cvd_delta_ratio", "sector_aligned", "spy_bullish"]:
            df.at[i, c] = _f(a[c])
        df.at[i, "alert_regime"] = a["regime"]
    return df


def _bars(sym: str) -> pd.DataFrame:
    try:
        from data_feed_public import get_public_bars_sync
        b = get_public_bars_sync(sym, "5m")
        if b is None or b.empty:
            return pd.DataFrame()
        b = b.copy()
        b.index = pd.to_datetime(b.index, utc=True).tz_convert("America/New_York")
        return b
    except Exception as e:
        print(f"[DeskReport] bars unavailable for {sym}: {e}")
        return pd.DataFrame()


def _sector(sym: str):
    try:
        from frank_sector_guard import get_ticker_sector
        etf, name = get_ticker_sector(sym)
        if etf in (None, "SPY"):   # unmapped tickers fall back to SPY; don't double-count it as a "sector"
            return None, None
        return etf, name
    except Exception:
        return None, None


def _move_during(sym_bars: pd.DataFrame, start, end) -> Optional[float]:
    if sym_bars.empty:
        return None
    w = sym_bars[(sym_bars.index >= start - pd.Timedelta(minutes=5)) & (sym_bars.index <= end)]
    if len(w) < 2:
        return None
    return (w["Close"].iloc[-1] / w["Open"].iloc[0]) - 1.0


def trade_forensics(r: pd.Series, spy_bars: pd.DataFrame) -> Dict[str, Any]:
    """Real MFE/MAE, post-exit behaviour and market context from 5m bars."""
    entry, exitp = r["entry_price"], r["exit_price"]
    risk_ps = (entry - r["real_stop"]) if _f(r.get("real_stop")) else entry * DEFAULT_RISK_PCT
    risk_known = bool(_f(r.get("real_stop")))
    out: Dict[str, Any] = {"risk_ps": risk_ps, "risk_known": risk_known,
                           "real_r": (exitp - entry) / risk_ps if risk_ps else None}
    b = _bars(r["ticker"])
    if b.empty:
        return out
    day = b[b.index.strftime("%Y-%m-%d") == r["exit_date"]]
    day = day[(day.index.strftime("%H:%M") >= "09:30") & (day.index.strftime("%H:%M") <= "15:59")]
    held = day[(day.index >= r["entry_ts"].floor("5min")) & (day.index <= r["exit_ts"])]
    after = day[day.index > r["exit_ts"]]
    if not held.empty:
        hi, lo = held["High"].max(), held["Low"].min()
        out.update({
            "mfe_px": hi, "mae_px": lo,
            "mfe_r": (hi - entry) / risk_ps, "mae_r": (lo - entry) / risk_ps,
            "mfe_time": held["High"].idxmax().strftime("%H:%M"),
            "capture": ((exitp - entry) / (hi - entry)) if hi > entry else None,
        })
    if not day.empty:
        close_px = day["Close"].iloc[-1]
        out["close_px"] = close_px
        out["close_r"] = (close_px - entry) / risk_ps
        out["day_hi"] = day["High"].max()
    if not after.empty:
        out["post_hi"] = after["High"].max()
        out["post_lo"] = after["Low"].min()
        out["post_hi_r"] = (out["post_hi"] - entry) / risk_ps
        out["recovered_above_entry"] = bool(r["is_loss"] and out["post_hi"] > entry)
    out["spy_move"] = _move_during(spy_bars, r["entry_ts"], r["exit_ts"])
    etf, name = _sector(r["ticker"])
    out["sector_etf"], out["sector_name"] = etf, name
    if etf:
        out["sector_move"] = _move_during(_bars(etf), r["entry_ts"], r["exit_ts"])
    out["stock_move"] = _move_during(b, r["entry_ts"], r["exit_ts"])
    return out


def classify_trade(r: pd.Series, fx: Dict[str, Any]) -> Dict[str, str]:
    """Deterministic, specific verdict. No generic filler."""
    mfe, mae = fx.get("mfe_r"), fx.get("mae_r")
    rr = fx.get("real_r") or 0.0
    spy, sec = fx.get("spy_move"), fx.get("sector_move")
    ctx = []
    if spy is not None:
        ctx.append(f"SPY {spy*100:+.2f}%")
    if sec is not None and fx.get("sector_etf"):
        ctx.append(f"{fx['sector_etf']} {sec*100:+.2f}%")
    ctx_s = " while " + ", ".join(ctx) if ctx else ""
    tags = []
    if r["opening_bell"]:
        tags.append("OPENING-BELL ENTRY")

    if r["is_loss"]:
        if mfe is not None and mfe < 0.15:
            tag, why = "NEVER WORKED", (f"Price never got more than {mfe:+.2f}R in our favour; it went against us "
                                         f"from the first bar{ctx_s}. The entry was wrong, not the management.")
        elif mfe is not None and mfe >= 0.5:
            tag, why = "GAVE BACK A WINNER", (f"Was up {mfe:+.2f}R at {fx.get('mfe_time')} but no profit was "
                                               f"locked before it reversed to {rr:+.2f}R. Management problem: tiers 1-3 "
                                               f"only move the stop, they don't bank.")
        else:
            tag, why = "WEAK FOLLOW-THROUGH", (f"Peaked at only {mfe:+.2f}R before failing{ctx_s}." if mfe is not None
                                                else f"Stopped at {rr:+.2f}R{ctx_s}.")
        if fx.get("recovered_above_entry"):
            why += (f" After we were stopped it traded back to ${fx['post_hi']:.2f} (above our ${r['entry_price']:.2f} "
                    f"entry) — the idea was right but the stop sat inside normal noise.")
            tag += " / STOP TOO TIGHT"
        if sec is not None and sec < -0.003:
            why += f" Its sector was falling during the trade, so we were buying against the group."
    elif r["is_win"]:
        cap = fx.get("capture")
        if r["eod_flatten"] and mfe is not None and cap is not None and cap < 0.6:
            tag, why = "LEFT MONEY ON TABLE", (f"Peaked at {mfe:+.2f}R ({fx.get('mfe_time')}) but closed at {rr:+.2f}R via "
                                                f"the 3:58 PM flatten — captured {cap*100:.0f}% of the move.")
        elif cap is not None and cap >= 0.7:
            tag, why = "CLEAN CAPTURE", f"Captured {cap*100:.0f}% of the best price available ({mfe:+.2f}R peak){ctx_s}."
        else:
            tag, why = "WIN", f"Closed {rr:+.2f}R{(' from a ' + format(mfe, '+.2f') + 'R peak') if mfe is not None else ''}{ctx_s}."
        if spy is not None and fx.get("stock_move") is not None and fx["stock_move"] - spy > 0.005:
            why += f" Real relative strength: stock beat SPY by {(fx['stock_move']-spy)*100:.1f} pts."
    else:
        tag, why = "SCRATCH", f"Closed flat{ctx_s}."
    return {"tag": tag, "why": why, "flags": " · ".join(tags)}


# ----------------------------------------------------------------------------- aggregates
def stats(d: pd.DataFrame) -> Dict[str, Any]:
    if d is None or d.empty:
        return {"n": 0, "w": 0, "l": 0, "net": 0.0, "wr": 0.0, "pf": None, "exp": 0.0, "avg_w": 0.0, "avg_l": 0.0}
    w, l = d[d.is_win], d[d.is_loss]
    gl = abs(l.net_pnl_dollar.sum())
    return {"n": len(d), "w": len(w), "l": len(l), "net": float(d.net_pnl_dollar.sum()),
            "wr": len(w) / max(1, len(w) + len(l)), "pf": (w.net_pnl_dollar.sum() / gl) if gl > 0 else None,
            "exp": float(d.net_pnl_dollar.mean()), "avg_w": float(w.net_pnl_dollar.mean()) if len(w) else 0.0,
            "avg_l": float(l.net_pnl_dollar.mean()) if len(l) else 0.0}


def scoreboard(df: pd.DataFrame, session_date: str) -> List[Dict[str, Any]]:
    sd = pd.Timestamp(session_date)
    wk_start = (sd - pd.Timedelta(days=sd.weekday())).strftime("%Y-%m-%d")
    mo_start = sd.strftime("%Y-%m-01")
    rows = [("Today", df[df.exit_date == session_date]),
            ("Week", df[(df.exit_date >= wk_start) & (df.exit_date <= session_date)]),
            ("Month", df[(df.exit_date >= mo_start) & (df.exit_date <= session_date)]),
            (f"Since {ARCH_START[5:]} (current rules)", df[(df.exit_date >= ARCH_START) & (df.exit_date <= session_date)]),
            ("All live (V12)", df[df.exit_date <= session_date])]
    return [{"label": k, **stats(v)} for k, v in rows]


def edge_tracker(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Slice our own live results. Report the leaks/edges with sample size, CI and counterfactual."""
    if df.empty:
        return []
    base_wr = df.is_win.sum() / max(1, (df.is_win | df.is_loss).sum())
    slices = {
        "Entries at the opening bell (9:40-9:46)": df.opening_bell,
        "Entries 9:47-11:30": (df.entry_hhmm > "09:46") & (df.entry_hhmm <= "11:30"),
        "Entries 11:30-14:00": (df.entry_hhmm > "11:30") & (df.entry_hhmm <= "14:00"),
        "Entries after 14:00": df.entry_hhmm > "14:00",
        "Held to the 3:58 PM flatten": df.eod_flatten,
        "Exited before 3:55 PM (stop/target)": ~df.eod_flatten,
        "Monday sessions": df.exit_ts.dt.weekday == 0,
        "Friday sessions": df.exit_ts.dt.weekday == 4,
        "Stocks over $300": df.entry_price > 300,
        "Stocks under $50": df.entry_price < 50,
    }
    out = []
    for name, mask in slices.items():
        s = df[mask]
        if len(s) < 5:
            continue
        wins = int(s.is_win.sum()); dec = int((s.is_win | s.is_loss).sum())
        lo, hi = wilson(wins, dec)
        net = float(s.net_pnl_dollar.sum())
        n = len(s)
        if n >= 30 and (hi < base_wr or lo > base_wr):
            status = "CONFIRMED"
        elif n >= 15 and (hi < base_wr + 0.05 or lo > base_wr - 0.05):
            status = "EMERGING"
        else:
            status = "WATCH"
        rec = s[s.exit_date >= ARCH_START]
        out.append({"slice": name, "n": n, "wr": wins / max(1, dec), "ci": (lo, hi), "net": net,
                    "rec_n": len(rec), "rec_net": float(rec.net_pnl_dollar.sum()),
                    "rec_wr": float(rec.is_win.sum() / max(1, (rec.is_win | rec.is_loss).sum())),
                    "exp": net / n, "skip_delta": -net, "status": status,
                    "kind": "LEAK" if net < 0 and (wins / max(1, dec)) < base_wr else ("EDGE" if net > 0 else "NEUTRAL")})
    out.sort(key=lambda x: (x["rec_net"], x["net"]))
    return out


def recent_vs_all(df: pd.DataFrame, slice_name: str, mask_fn) -> str:
    rec = df[df.exit_date >= ARCH_START]
    a, b = df[mask_fn(df)], rec[mask_fn(rec)]
    if len(b) == 0:
        return ""
    return f"all-time {len(a)} trades / {money(a.net_pnl_dollar.sum())}; since {ARCH_START[5:]} {len(b)} / {money(b.net_pnl_dollar.sum())}"


def changelog_impact(df: pd.DataFrame, session_date: str) -> List[Dict[str, Any]]:
    try:
        log = json.load(open(CHANGELOG_PATH)) if os.path.exists(CHANGELOG_PATH) else DEFAULT_CHANGELOG
    except Exception:
        log = DEFAULT_CHANGELOG
    out = []
    for e in log:
        d = e["date"]
        before = df[(df.exit_date < d)].tail(30)
        after = df[(df.exit_date >= d) & (df.exit_date <= session_date)]
        sb, sa = stats(before), stats(after)
        verdict = "TOO EARLY" if sa["n"] < 15 else ("HELPING" if sa["exp"] > sb["exp"] else "NOT HELPING")
        out.append({**e, "before": sb, "after": sa, "verdict": verdict})
    return out


def learning_health(df: pd.DataFrame, model_meta: Optional[dict]) -> List[Dict[str, str]]:
    checks = []
    # 1. Champion model top feature
    if model_meta:
        fi = model_meta.get("feature_importances") or {}
        if isinstance(fi, str):
            try:
                fi = json.loads(fi)
            except Exception:
                fi = {}
        top = sorted(fi.items(), key=lambda x: x[1], reverse=True)[:3]
        if top and top[0][0] in PROXY_FEATURES:
            checks.append({"level": "RED", "item": "Model is leaning on a calendar proxy",
                           "detail": f"#1 feature is '{top[0][0]}' at {top[0][1]}% — {PROXY_FEATURES[top[0][0]]}. "
                                     f"The model is partly learning *which week* a trade happened, not setup quality. "
                                     f"Fix: drop it from training (or stop defaulting it to 45)."})
        dead = [k for k, v in fi.items() if v == 0.0 and "Direction" not in k]
        if dead:
            checks.append({"level": "AMBER", "item": f"{len(dead)} features contribute 0%",
                           "detail": ", ".join(dead) + " — not being populated; remove or fix the feed."})
        acc = _f(model_meta.get("model_accuracy"))
        wr_before = _f(model_meta.get("win_rate_before"))
        if acc and wr_before:
            naive = max(wr_before, 100 - wr_before)
            lvl = "RED" if acc - naive < 5 else ("AMBER" if acc - naive < 10 else "GREEN")
            checks.append({"level": lvl, "item": f"Accuracy {acc:.1f}% vs {naive:.1f}% naive baseline",
                           "detail": f"Always guessing 'loss' scores {naive:.1f}%. Real lift is {acc - naive:+.1f} pts."})
    # 2. Live ledger linkage
    if not df.empty:
        linked = df["real_ml_conf"].notna().mean()
        if linked < 0.8:
            checks.append({"level": "RED" if linked < 0.5 else "AMBER", "item": f"Only {linked*100:.0f}% of live trades linked to their signal",
                           "detail": "Without the real entry ML score and stop we can't tell whether the ML gate "
                                     "separates winners from losers. (The P&L Station currently shows a placeholder "
                                     "score: 68.5 for every win, 58.0 for every loss.)"})
        lk = df[df["real_ml_conf"].notna()]
        if len(lk) >= 10:
            hi_c = lk[lk.real_ml_conf >= lk.real_ml_conf.median()]
            lo_c = lk[lk.real_ml_conf < lk.real_ml_conf.median()]
            sh, sl = stats(hi_c), stats(lo_c)
            lvl = "GREEN" if sh["exp"] > sl["exp"] else "RED"
            checks.append({"level": lvl, "item": "Does higher ML confidence actually pay more?",
                           "detail": f"Top half (>= {lk.real_ml_conf.median():.1f}%): {sh['n']} trades, {pct(sh['wr'])} WR, "
                                     f"{money(sh['exp'])}/trade. Bottom half: {sl['n']} trades, {pct(sl['wr'])} WR, "
                                     f"{money(sl['exp'])}/trade."})
    # 3. Stale paper labels (training data that never resolves)
    try:
        from data_paths import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        cutoff = (now_et() - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
        n_stale = conn.execute("SELECT COUNT(*) FROM alerts WHERE outcome='OPEN' AND timestamp < ? AND timeframe IN ('5m','15m')",
                               (cutoff,)).fetchone()[0]
        conn.close()
        if n_stale:
            checks.append({"level": "AMBER", "item": f"{n_stale} intraday paper signals never resolved",
                           "detail": "5m/15m signals older than 2 days still marked OPEN. They're excluded from "
                                     "training, so the model is not learning from them."})
    except Exception:
        pass
    # 4. Rules
    checks.append({"level": "AMBER", "item": "AI-written 'desk rules' are not wired into execution",
                   "detail": "The nightly LLM rules are stored and emailed but no code enforces them. From now on a "
                             "rule only counts as 'learned' once it is coded, deployed and listed in the changelog below."})
    return checks


def signal_funnel(session_date: str) -> Dict[str, Any]:
    out = {"fired": None}
    try:
        from data_paths import DB_PATH
        try:
            from config_manager import load_config
            floor = float(load_config().get("min_ml_conf", 50.0))
        except Exception:
            floor = 50.0
        conn = sqlite3.connect(DB_PATH)
        al = pd.read_sql_query("SELECT ticker, timestamp, timeframe, ml_confidence, user_active FROM alerts "
                               "WHERE substr(timestamp,1,10)=? AND direction='LONG'", conn, params=(session_date,))
        conn.close()
        al["ml_confidence"] = pd.to_numeric(al["ml_confidence"], errors="coerce")
        al["hhmm"] = pd.to_datetime(al["timestamp"], errors="coerce", utc=True).dt.tz_convert("America/New_York").dt.strftime("%H:%M")
        passed = al[al.ml_confidence >= floor]
        out = {"fired": len(al), "floor": floor, "passed": len(passed),
               "max_conf": float(al.ml_confidence.max()) if len(al) else None,
               "top": al.sort_values("ml_confidence", ascending=False).head(5)[["ticker", "hhmm", "ml_confidence"]].to_dict("records"),
               "executed": int((al.user_active == 1).sum()),
               "blackout": int(((al.hhmm >= "11:30") & (al.hhmm < "14:00")).sum())}
    except Exception as e:
        print(f"[DeskReport] funnel unavailable: {e}")
    return out


# ----------------------------------------------------------------------------- narrative
def llm_narrative(session_date: str, cards: List[Dict[str, Any]], board: List[Dict[str, Any]],
                  edges: List[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    if not cards:
        return None
    facts = {
        "session_date": session_date,
        "today": board[0],
        "trades": [{"ticker": c["ticker"], "entry_time": c["entry_hhmm"], "exit_time": c["exit_hhmm"],
                    "pnl_dollar": round(c["net"], 2), "r": round(c["fx"].get("real_r") or 0, 2),
                    "mfe_r": round(c["fx"]["mfe_r"], 2) if c["fx"].get("mfe_r") is not None else None,
                    "verdict": c["cls"]["tag"], "evidence": c["cls"]["why"]} for c in cards],
        "biggest_leak": edges[0] if edges else None,
    }
    system = ("You are the head of a small prop trading desk writing tonight's 4-sentence review for the PM. "
              "Use ONLY the facts in the JSON. Do not invent tickers, prices, indicators (no CVD, CLV, springs, "
              "volume dry-up) unless they appear in the facts. Be blunt and specific; name tickers and numbers. "
              'Return JSON: {"headline": "<= 12 words", "story": "3-4 sentences explaining what actually happened '
              'today and why", "one_change": "single concrete, testable change suggested by the evidence, or '
              'HOLD if the evidence is too thin"}')
    try:
        from ai_brain import query_gemini, clean_json_text
        import re as _re
        res = None
        for _ in range(2):
            raw = query_gemini(json.dumps(facts, default=str), system_instruction=system, json_mode=True, timeout=25)
            txt = clean_json_text(raw)
            m = _re.search(r"\{.*\}", txt, _re.S)
            try:
                res = json.loads(m.group(0) if m else txt)
                break
            except Exception:
                continue
        if not isinstance(res, dict):
            return None
        allowed = {c["ticker"] for c in cards}
        import re
        mentioned = set(re.findall(r"\b[A-Z]{2,5}\b", " ".join(str(v) for v in res.values())))
        bogus = {m for m in mentioned if m not in allowed and m not in {"SPY", "QQQ", "PM", "EOD", "HOLD", "VWAP", "ML", "WR", "R", "BP"}
                 and not m.startswith("XL")}
        if bogus:
            print(f"[DeskReport] rejected LLM narrative (mentions {bogus})")
            return None
        return res
    except Exception as e:
        print(f"[DeskReport] narrative unavailable: {e}")
        return None


def deterministic_headline(board_today: Dict[str, Any], cards: List[Dict[str, Any]]) -> str:
    if board_today["n"] == 0:
        return "No trades today"
    tags = [c["cls"]["tag"].split(" /")[0] for c in cards if c["is_loss"]]
    common = max(set(tags), key=tags.count) if tags else None
    s = f"{board_today['w']}W-{board_today['l']}L, {money(board_today['net'])}"
    if common:
        s += f" — losses were mostly '{common.lower()}'"
    return s


# ----------------------------------------------------------------------------- html
C = {"bg": "#0b0f19", "card": "#111827", "panel": "#1e293b", "line": "#334155", "txt": "#e2e8f0", "mut": "#94a3b8",
     "g": "#4ade80", "r": "#f87171", "y": "#facc15", "b": "#38bdf8"}


def _col(x):
    return C["g"] if x > 0 else (C["r"] if x < 0 else C["mut"])


def _section(title: str, inner: str, sub: str = "") -> str:
    sub_html = f"<div style='color:{C['mut']};font-size:11px;margin:2px 0 8px 11px'>{sub}</div>" if sub else ""
    return (f"<div style='margin-top:22px'><div style='font-size:13px;font-weight:bold;color:{C['b']};text-transform:uppercase;"
            f"letter-spacing:.5px;border-left:3px solid {C['b']};padding-left:8px'>{title}</div>{sub_html}{inner}</div>")


def _equity_bars(df: pd.DataFrame, session_date: str) -> str:
    d = df[df.exit_date <= session_date].groupby("exit_date").net_pnl_dollar.sum().tail(15)
    if d.empty:
        return ""
    mx = max(1.0, d.abs().max())
    rows, cum = "", float(df[df.exit_date < d.index[0]].net_pnl_dollar.sum())
    for day, v in d.items():
        cum += v
        w = int(abs(v) / mx * 140)
        bar = (f"<div style='display:inline-block;height:9px;width:{w}px;background:{_col(v)};border-radius:2px'></div>")
        rows += (f"<tr><td style='color:{C['mut']};font-size:11px;padding:1px 6px'>{pd.Timestamp(day).strftime('%a %m/%d')}</td>"
                 f"<td style='width:150px;padding:1px 6px'>{bar}</td>"
                 f"<td style='color:{_col(v)};font-size:11px;text-align:right;padding:1px 6px'>{money(v)}</td>"
                 f"<td style='color:{C['mut']};font-size:11px;text-align:right;padding:1px 6px'>{money(cum)}</td></tr>")
    return (f"<table style='border-collapse:collapse;background:{C['panel']};border-radius:6px;width:100%'>"
            f"<tr><td></td><td style='color:{C['mut']};font-size:10px;padding:4px 6px'>Daily P&amp;L (last 15 sessions)</td>"
            f"<td style='color:{C['mut']};font-size:10px;text-align:right;padding:4px 6px'>Day</td>"
            f"<td style='color:{C['mut']};font-size:10px;text-align:right;padding:4px 6px'>Cumulative</td></tr>{rows}</table>")


def _trade_card(c: Dict[str, Any]) -> str:
    fx, cls = c["fx"], c["cls"]
    color = _col(c["net"])
    rr = fx.get("real_r")
    r_lbl = (f"{rr:+.2f}R" + ("" if fx.get("risk_known") else " est")) if rr is not None else "—"

    def cell(lbl, val):
        return (f"<td style='padding:4px 8px;vertical-align:top'><div style='color:{C['mut']};font-size:9px;text-transform:uppercase'>{lbl}</div>"
                f"<div style='color:{C['txt']};font-size:12px'>{val}</div></td>")
    mfe = f"{fx['mfe_r']:+.2f}R @ {fx.get('mfe_time')}" if fx.get("mfe_r") is not None else "—"
    mae = f"{fx['mae_r']:+.2f}R" if fx.get("mae_r") is not None else "—"
    post = (f"hi ${fx['post_hi']:.2f} / lo ${fx['post_lo']:.2f}" if fx.get("post_hi") is not None else "held to close")
    close_ = f"${fx['close_px']:.2f} ({fx['close_r']:+.2f}R)" if fx.get("close_px") is not None else "—"
    ml = f"{c['ml']:.1f}%" if c.get("ml") is not None else "not linked"
    flags = f"<span style='color:{C['y']};font-size:10px;margin-left:8px'>{cls['flags']}</span>" if cls["flags"] else ""
    return (
        f"<div style='background:{C['panel']};border-left:3px solid {color};border-radius:6px;padding:10px 12px;margin-bottom:10px'>"
        f"<div><span style='color:{C['txt']};font-weight:bold;font-size:14px'>{c['ticker']}</span>"
        f"<span style='color:{color};font-weight:bold;font-size:14px;margin-left:10px'>{money(c['net'])}</span>"
        f"<span style='color:{C['mut']};font-size:12px;margin-left:8px'>{r_lbl}</span>"
        f"<span style='background:{color}22;color:{color};font-size:10px;font-weight:bold;padding:2px 6px;border-radius:3px;margin-left:10px'>{cls['tag']}</span>{flags}</div>"
        f"<table style='border-collapse:collapse;margin-top:6px;width:100%'><tr>"
        f"{cell('Entry', '$%.2f @ %s' % (c['entry'], c['entry_hhmm']))}{cell('Exit', '$%.2f @ %s' % (c['exit'], c['exit_hhmm']))}"
        f"{cell('Held', c['dur'])}{cell('ML score', ml)}</tr><tr>"
        f"{cell('Best (MFE)', mfe)}{cell('Worst (MAE)', mae)}{cell('After exit', post)}{cell('4PM close', close_)}</tr></table>"
        f"<div style='color:{C['txt']};font-size:12px;line-height:1.5;margin-top:6px'>{cls['why']}</div></div>")


def build_html(ctx: Dict[str, Any]) -> str:
    sd = ctx["session_date"]
    board, cards, edges, health, changes = ctx["board"], ctx["cards"], ctx["edges"], ctx["health"], ctx["changes"]
    today = board[0]
    nar = ctx.get("narrative")
    head = (nar or {}).get("headline") or ctx["headline"]
    date_lbl = pd.Timestamp(sd).strftime("%A, %B %d, %Y")

    # --- scoreboard
    sb_rows = ""
    for b in board:
        pf = f"{b['pf']:.2f}" if b["pf"] is not None else "—"
        sb_rows += (f"<tr style='border-top:1px solid {C['line']}'><td style='padding:6px 8px;color:{C['txt']};font-size:12px'>{b['label']}</td>"
                    f"<td style='padding:6px 8px;color:{C['txt']};font-size:12px;text-align:right'>{b['n']}</td>"
                    f"<td style='padding:6px 8px;color:{C['txt']};font-size:12px;text-align:right'>{b['w']}-{b['l']}</td>"
                    f"<td style='padding:6px 8px;color:{C['txt']};font-size:12px;text-align:right'>{pct(b['wr']) if b['n'] else '—'}</td>"
                    f"<td style='padding:6px 8px;color:{C['txt']};font-size:12px;text-align:right'>{pf}</td>"
                    f"<td style='padding:6px 8px;color:{_col(b['exp'])};font-size:12px;text-align:right'>{money(b['exp']) if b['n'] else '—'}</td>"
                    f"<td style='padding:6px 8px;color:{_col(b['net'])};font-size:12px;text-align:right;font-weight:bold'>{money(b['net'])}</td></tr>")
    th = lambda t, a="right": f"<th style='padding:6px 8px;color:{C['mut']};font-size:10px;text-transform:uppercase;text-align:{a};font-weight:normal'>{t}</th>"
    scoreboard_html = (f"<table style='border-collapse:collapse;width:100%;background:{C['panel']};border-radius:6px'>"
                       f"<tr>{th('Period','left')}{th('Trades')}{th('W-L')}{th('Win%')}{th('PF')}{th('Avg/trade')}{th('Net')}</tr>{sb_rows}</table>")

    # --- top story
    if nar:
        story = (f"<div style='color:{C['txt']};font-size:13px;line-height:1.6'>{nar.get('story','')}</div>")
    elif today["n"] == 0:
        f = ctx.get("funnel") or {}
        if f.get("fired") is not None:
            top = ", ".join(f"{t['ticker']} {t['ml_confidence']:.0f}% @ {t['hhmm']}" for t in f.get("top", []))
            story = (f"<div style='color:{C['txt']};font-size:13px;line-height:1.6'>No trades closed today, and that is the "
                     f"system working, not failing. The scanner fired <b>{f['fired']}</b> long signals; "
                     f"<b>{f['passed']}</b> cleared the {f['floor']:.0f}% ML floor (best score: "
                     f"{(f['max_conf'] or 0):.1f}%). {f.get('blackout', 0)} fired inside the 11:30-2:00 blackout. "
                     f"Highest-scoring signals: {top or 'none'}.</div>")
        else:
            story = f"<div style='color:{C['txt']};font-size:13px'>No trades closed today.</div>"
    else:
        story = f"<div style='color:{C['txt']};font-size:13px;line-height:1.6'>{ctx['headline']}.</div>"
    one_change = (nar or {}).get("one_change")
    if one_change:
        story += (f"<div style='margin-top:8px;color:{C['y']};font-size:12px'><b>Suggested test:</b> {one_change}</div>")

    # --- trades
    trades_html = "".join(_trade_card(c) for c in cards) if cards else (
        f"<div style='color:{C['mut']};font-size:12px'>No round-trips closed on {date_lbl}. "
        f"(Previous reports re-used the last trading day's trades when this happened — that's fixed.)</div>")

    # --- edge tracker
    edge_rows = ""
    for e in edges:
        sc = {"CONFIRMED": C["r"] if e["kind"] == "LEAK" else C["g"], "EMERGING": C["y"], "WATCH": C["mut"]}[e["status"]]
        edge_rows += (f"<tr style='border-top:1px solid {C['line']}'>"
                      f"<td style='padding:5px 8px;color:{C['txt']};font-size:12px'>{e['slice']}</td>"
                      f"<td style='padding:5px 8px;color:{C['txt']};font-size:12px;text-align:right'>{e['n']}</td>"
                      f"<td style='padding:5px 8px;color:{C['txt']};font-size:12px;text-align:right'>{pct(e['wr'])}<span style='color:{C['mut']};font-size:10px'> ({pct(e['ci'][0])}-{pct(e['ci'][1])})</span></td>"
                      f"<td style='padding:5px 8px;color:{_col(e['net'])};font-size:12px;text-align:right'>{money(e['net'])}</td>"
                      f"<td style='padding:5px 8px;color:{_col(e['rec_net'])};font-size:12px;text-align:right'>{money(e['rec_net'])}<span style='color:{C['mut']};font-size:10px'> ({e['rec_n']}, {pct(e['rec_wr'])})</span></td>"
                      f"<td style='padding:5px 8px;font-size:10px;font-weight:bold;color:{sc};text-align:right'>{e['status']}</td></tr>")
    edge_html = (f"<table style='border-collapse:collapse;width:100%;background:{C['panel']};border-radius:6px'>"
                 f"<tr>{th('Slice of our live trades','left')}{th('N')}{th('Win% (95% CI)')}{th('Net (all V12)')}{th(f'Since {ARCH_START[5:]}')}{th('Status')}</tr>{edge_rows}</table>"
                 f"<div style='color:{C['mut']};font-size:10px;margin-top:4px'>CONFIRMED = 30+ trades and the confidence interval "
                 f"clears our overall win rate ({pct(ctx['base_wr'])}). EMERGING = 15+ trades, leaning one way. WATCH = too few to act on.</div>")

    # --- learning health
    hc = {"RED": C["r"], "AMBER": C["y"], "GREEN": C["g"]}
    health_html = "".join(
        f"<div style='background:{C['panel']};border-left:3px solid {hc[h['level']]};border-radius:6px;padding:8px 12px;margin-bottom:6px'>"
        f"<div style='color:{hc[h['level']]};font-size:12px;font-weight:bold'>{h['item']}</div>"
        f"<div style='color:{C['txt']};font-size:11.5px;line-height:1.5;margin-top:2px'>{h['detail']}</div></div>" for h in health)
    m = ctx.get("model_meta") or {}
    if m:
        health_html = (f"<div style='color:{C['mut']};font-size:11px;margin-bottom:6px'>Champion model {m.get('version','?')} · "
                       f"{m.get('training_samples','?')} training samples · retrained {str(m.get('timestamp',''))[:16]}</div>") + health_html

    # --- changelog
    ch_rows = ""
    for e in changes:
        vc = {"HELPING": C["g"], "NOT HELPING": C["r"], "TOO EARLY": C["mut"]}[e["verdict"]]
        ch_rows += (f"<tr style='border-top:1px solid {C['line']}'><td style='padding:5px 8px;color:{C['mut']};font-size:11px'>{e['date'][5:]}</td>"
                    f"<td style='padding:5px 8px;color:{C['txt']};font-size:11.5px'>{e['change']}</td>"
                    f"<td style='padding:5px 8px;color:{_col(e['before']['exp'])};font-size:11px;text-align:right'>{money(e['before']['exp'])}<span style='color:{C['mut']}'> ({e['before']['n']})</span></td>"
                    f"<td style='padding:5px 8px;color:{_col(e['after']['exp'])};font-size:11px;text-align:right'>{money(e['after']['exp'])}<span style='color:{C['mut']}'> ({e['after']['n']})</span></td>"
                    f"<td style='padding:5px 8px;color:{vc};font-size:10px;font-weight:bold;text-align:right'>{e['verdict']}</td></tr>")
    ch_html = (f"<table style='border-collapse:collapse;width:100%;background:{C['panel']};border-radius:6px'>"
               f"<tr>{th('Date','left')}{th('Change shipped','left')}{th('Avg/trade before')}{th('After')}{th('Verdict')}</tr>{ch_rows}</table>")

    tcol = _col(today["net"])
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:20px;background:{C['bg']};font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
<div style="max-width:680px;margin:0 auto;background:{C['card']};border:1px solid #1f2937;border-radius:8px;padding:22px">
  <div style="border-bottom:1px solid #1f2937;padding-bottom:14px">
    <div style="color:{C['mut']};font-size:11px;letter-spacing:.5px;text-transform:uppercase">WSR Desk Review · Session {date_lbl}</div>
    <div style="margin-top:6px"><span style="color:{tcol};font-size:28px;font-weight:bold">{money(today['net'])}</span>
      <span style="color:{C['mut']};font-size:13px;margin-left:10px">{today['n']} trades · {today['w']}W-{today['l']}L</span></div>
    <div style="color:{C['txt']};font-size:15px;margin-top:6px;font-weight:600">{head}</div>
  </div>
  {_section('The story', story)}
  {_section('Scoreboard', scoreboard_html, 'Public.com fills only. AMC/APE/NKE core holdings and options excluded.')}
  <div style="margin-top:10px">{_equity_bars(ctx['df'], sd)}</div>
  {_section('Trade-by-trade', trades_html, 'MFE/MAE from 5m bars while we held. R uses the real stop when linked, otherwise a 1.5% estimate.')}
  {_section('Edge tracker — what our own data says', edge_html, 'Every live V12 trade, sliced. Biggest leaks first.')}
  {_section('Is the algo actually learning? (pipeline audit)', health_html)}
  {_section('Changes shipped & measured', ch_html, 'Avg $/trade over the 30 trades before vs. all trades since. A change only counts as learning once it shows up here.')}
  <div style="border-top:1px solid #1f2937;margin-top:22px;padding-top:12px;color:#64748b;font-size:10.5px;text-align:center">
    Generated {now_et().strftime('%b %d %I:%M %p ET')} · numbers computed from broker fills &amp; 5m bars · narrative constrained to these facts
  </div>
</div></body></html>"""


# ----------------------------------------------------------------------------- entry points
def latest_model_meta() -> Optional[dict]:
    try:
        from data_paths import DB_PATH
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM ml_model_history ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        if not r:
            return None
        d = dict(r)
        d["feature_importances"] = d.get("feature_importances_json")
        return d
    except Exception:
        return None


def build_desk_report(session_date: Optional[str] = None, pnl_data: Optional[dict] = None,
                      model_meta: Optional[dict] = None, use_llm: bool = True) -> Dict[str, Any]:
    session_date = session_date or now_et().strftime("%Y-%m-%d")
    df = enrich_with_alerts(load_live_trades(pnl_data))
    if model_meta is None:
        model_meta = latest_model_meta()
    today = df[df.exit_date == session_date] if not df.empty else df
    spy = _bars("SPY") if not today.empty else pd.DataFrame()
    cards = []
    for _, r in today.iterrows():
        fx = trade_forensics(r, spy)
        cls = classify_trade(r, fx)
        cards.append({"ticker": r["ticker"], "net": r["net_pnl_dollar"], "entry": r["entry_price"], "exit": r["exit_price"],
                      "entry_hhmm": r["entry_hhmm"], "exit_hhmm": r["exit_hhmm"], "dur": r.get("duration_str") or f"{r['hold_min']:.0f}m",
                      "ml": _f(r.get("real_ml_conf")), "is_loss": bool(r["is_loss"]), "fx": fx, "cls": cls})
    board = scoreboard(df, session_date) if not df.empty else [{"label": "Today", **stats(None)}]
    hist = df[df.exit_date <= session_date] if not df.empty else df
    edges = edge_tracker(hist)
    base_wr = (hist.is_win.sum() / max(1, (hist.is_win | hist.is_loss).sum())) if not hist.empty else 0.0
    ctx = {"session_date": session_date, "df": df, "board": board, "cards": cards, "edges": edges, "base_wr": base_wr,
           "health": learning_health(hist, model_meta), "changes": changelog_impact(hist, session_date) if not hist.empty else [],
           "funnel": signal_funnel(session_date) if today.empty else None, "model_meta": model_meta}
    ctx["headline"] = deterministic_headline(board[0], cards)
    ctx["narrative"] = llm_narrative(session_date, cards, board, edges) if use_llm else None
    ctx["html"] = build_html(ctx)
    try:
        json.dump({"session_date": session_date, "generated": now_et().isoformat(),
                   "edges": [{k: v for k, v in e.items() if k != "ci"} for e in edges]},
                  open(EDGE_STATE_PATH, "w"), indent=1, default=str)
    except Exception:
        pass
    return ctx


def send_desk_report(session_date: Optional[str] = None, model_meta: Optional[dict] = None, recipient: str = None) -> bool:
    ctx = build_desk_report(session_date, model_meta=model_meta)
    t = ctx["board"][0]
    subj = (f"Desk Review {pd.Timestamp(ctx['session_date']).strftime('%a %m/%d')}: "
            + (f"{money(t['net'])} ({t['w']}W-{t['l']}L)" if t["n"] else "No trades"))
    from frank_mailer import send_frank_email
    return send_frank_email(subj, ctx["html"], recipient)


if __name__ == "__main__":
    import sys
    c = build_desk_report(sys.argv[1] if len(sys.argv) > 1 else None)
    out = os.path.join(BASE_DIR, f"desk_report_{c['session_date']}.html")
    open(out, "w", encoding="utf-8").write(c["html"])
    print("wrote", out)
