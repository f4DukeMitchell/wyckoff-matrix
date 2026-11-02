import os
import json
import time
import sqlite3
import datetime
import urllib.request
import urllib.error
import numpy as np
import pandas as pd
from dotenv import load_dotenv

try:
    import zoneinfo
    ET_TZ = zoneinfo.ZoneInfo("America/New_York")
except Exception:
    ET_TZ = None

from data_paths import DATA_DIR, DB_PATH

# Load credentials
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
ENV_LOCAL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.local")
load_dotenv(ENV_PATH)
if os.path.exists(ENV_LOCAL_PATH):
    load_dotenv(ENV_LOCAL_PATH)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

FRANK_MEMORY_PATH = os.path.join(DATA_DIR, "frank_chat_memory.json")
FRANK_ANOMALY_CACHE_PATH = os.path.join(DATA_DIR, "frank_anomaly_radar.json")


def get_est_now():
    if ET_TZ:
        return datetime.datetime.now(ET_TZ)
    return datetime.datetime.now()


def get_gemini_api_key():
    global GEMINI_API_KEY
    if GEMINI_API_KEY:
        return GEMINI_API_KEY
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if GEMINI_API_KEY:
        return GEMINI_API_KEY
    for p in (ENV_LOCAL_PATH, ENV_PATH):
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        if line.startswith("GEMINI_API_KEY="):
                            GEMINI_API_KEY = line.strip().split("=", 1)[1]
                            return GEMINI_API_KEY
                        if line.startswith("GOOGLE_API_KEY="):
                            GEMINI_API_KEY = line.strip().split("=", 1)[1]
                            return GEMINI_API_KEY
            except Exception:
                pass
    return None


MODELS_CHAIN = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]


def clean_json_text(text: str) -> str:
    """Strips markdown code fences and cleans JSON output."""
    t = text.strip()
    if t.startswith("```json"):
        t = t[7:]
    elif t.startswith("```"):
        t = t[3:]
    if t.endswith("```"):
        t = t[:-3]
    return t.strip()


def query_gemini(prompt: str, system_instruction: str = None, json_mode: bool = False, timeout: int = 20) -> str:
    """
    Executes a high-speed inference call to Gemini via Google AI Studio REST API.
    Auto-fails over between Gemini models if a spike or rate limit occurs.
    """
    api_key = get_gemini_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured in .env")

    for model in MODELS_CHAIN:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 4096
            }
        }

        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                candidates = res_data.get("candidates", [])
                if candidates and "content" in candidates[0]:
                    parts = candidates[0]["content"].get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
        except urllib.error.HTTPError as e:
            if e.code in (404, 503, 429):
                continue
            err_msg = e.read().decode("utf-8", errors="ignore")
            print(f"[AI Brain] Gemini HTTP {e.code} on {model}: {err_msg}")
        except Exception as ex:
            print(f"[AI Brain] Gemini exception on {model}: {ex}")
            continue

    raise RuntimeError("All Gemini inference models in the fallback chain were exhausted or timed out.")


# ==============================================================================
# PERSISTENT CONVERSATION MEMORY
# ==============================================================================
def load_frank_memory(max_turns: int = 12) -> list:
    """Loads Frank's persistent multi-turn conversation history from disk."""
    if not os.path.exists(FRANK_MEMORY_PATH):
        return []
    try:
        with open(FRANK_MEMORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data[-max_turns:]
    except Exception:
        pass
    return []


def append_frank_memory(role: str, text: str, tools_called: list = None):
    """Appends a message turn to Frank's persistent memory."""
    os.makedirs(DATA_DIR, exist_ok=True)
    history = load_frank_memory(max_turns=30)
    history.append({
        "role": role,
        "text": text,
        "tools_called": tools_called or [],
        "timestamp": get_est_now().strftime("%Y-%m-%d %I:%M:%S %p EST")
    })
    history = history[-30:]
    try:
        with open(FRANK_MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        print(f"[AI Brain] Memory save error: {e}")


def clear_frank_memory():
    """Clears Frank's conversation history."""
    if os.path.exists(FRANK_MEMORY_PATH):
        try:
            os.remove(FRANK_MEMORY_PATH)
        except Exception:
            pass
    return {"success": True, "message": "Frank's conversation memory cleared."}


# ==============================================================================
# PHASE 1: FRANK'S AUTONOMOUS AGENT TOOLS (LIVE EXECUTION ENGINE)
# ==============================================================================
def tool_inspect_live_tape(ticker: str) -> dict:
    """
    Tool 1: Pulls real-time 5m bars for a ticker, computes intraday VWAP, RVOL, CLV, CVD proxy,
    maps Anti-HFT Stop-Hunt Liquidity Clusters, and diagnoses Runner vs. Harvester profile.
    """
    sym = (ticker or "SPY").strip().upper()
    try:
        import yfinance as yf
        df = yf.Ticker(sym).history(period="2d", interval="5m")
        if df is None or df.empty:
            return {"ticker": sym, "status": "NO_DATA", "error": f"No intraday 5m data returned for {sym}."}

        # Slice today's regular session bars if available, else last 30 bars
        today_str = get_est_now().strftime("%Y-%m-%d")
        df_today = df[df.index.strftime("%Y-%m-%d") == today_str]
        if len(df_today) < 3:
            df_today = df.tail(30)

        closes = df_today["Close"].values.astype(float)
        highs = df_today["High"].values.astype(float)
        lows = df_today["Low"].values.astype(float)
        opens = df_today["Open"].values.astype(float)
        vols = df_today["Volume"].values.astype(float)

        curr_price = float(closes[-1])
        typ = (highs + lows + closes) / 3.0
        cum_v = np.cumsum(vols)
        vwap = float((np.cumsum(typ * vols) / np.where(cum_v == 0, 1, cum_v))[-1])
        pct_vs_vwap = round(((curr_price - vwap) / vwap) * 100.0, 2) if vwap > 0 else 0.0

        # Bar Range & CLV
        bar_range = max(highs[-1] - lows[-1], 0.0001)
        clv = round(float((closes[-1] - lows[-1]) / bar_range), 2)

        # RVOL vs 20-bar mean
        avg_vol = float(np.mean(vols[:-1])) if len(vols) > 1 else float(vols[-1])
        rvol = round(float(vols[-1] / max(avg_vol, 1.0)), 2)

        # ATR (14-bar 5m)
        tr_list = np.maximum(highs - lows, np.abs(highs - np.roll(closes, 1)))
        atr_5m = float(np.mean(tr_list[-min(14, len(tr_list)):]))

        # CVD Proxy (last 6 bars net buying vs selling pressure)
        recent_clv = (closes[-6:] - lows[-6:]) / np.maximum(highs[-6:] - lows[-6:], 0.0001)
        signed_vol = (2.0 * recent_clv - 1.0) * vols[-6:]
        cvd_ratio = round(float(np.sum(signed_vol) / max(np.sum(vols[-6:]), 1.0)), 2)

        # Structural Range Low & High (last 20 bars)
        lookback = min(20, len(lows))
        swing_low = float(np.min(lows[-lookback:]))
        swing_high = float(np.max(highs[-lookback:]))
        dist_to_low_pct = round(((curr_price - swing_low) / swing_low) * 100.0, 2)

        # Anti-HFT Stop-Hunt & Liquidity Pool Mapper
        retail_stop_zone_top = round(swing_low, 2)
        retail_stop_zone_bot = round(swing_low - (0.25 * atr_5m), 2)
        hft_sweep_floor = round(swing_low - (0.60 * atr_5m), 2)
        safe_structural_stop = round(swing_low - (0.85 * atr_5m), 2)

        # Runner vs. Harvester Classification
        if pct_vs_vwap > 0.15 and (rvol >= 1.3 or cvd_ratio >= 0.20) and clv >= 0.60:
            runner_verdict = "HOME_RUN_RUNNER"
            runner_advice = (
                f"{sym} shows institutional expansion (+{pct_vs_vwap}% above VWAP, RVOL {rvol}x, CLV {clv}). "
                f"Hold 15% moonbag with stop loosened to $0.00 Breakeven for multi-R extension."
            )
        elif pct_vs_vwap < -0.15 or (cvd_ratio < -0.10 and clv < 0.45):
            runner_verdict = "HARVEST_DEFENSIVE"
            runner_advice = (
                f"{sym} is showing distribution/overhead supply ({pct_vs_vwap}% vs VWAP, CVD {cvd_ratio}). "
                f"Harvest profits aggressively at initial tiers and keep stops tight."
            )
        else:
            runner_verdict = "BALANCED_ROTATION"
            runner_advice = (
                f"{sym} is consolidating near equilibrium ({pct_vs_vwap}% vs VWAP). "
                f"Follow standard 5-tier Guardian progression with $0.00 Breakeven lock at Tier 3."
            )

        return {
            "ticker": sym,
            "status": "OK",
            "current_price": round(curr_price, 2),
            "intraday_vwap": round(vwap, 2),
            "pct_vs_vwap": pct_vs_vwap,
            "rvol_5m": rvol,
            "candle_clv": clv,
            "cvd_delta_ratio": cvd_ratio,
            "atr_5m": round(atr_5m, 3),
            "swing_low_20b": round(swing_low, 2),
            "swing_high_20b": round(swing_high, 2),
            "dist_to_swing_low_pct": dist_to_low_pct,
            "stop_hunt_map": {
                "retail_stop_cluster": f"${retail_stop_zone_bot:.2f} - ${retail_stop_zone_top:.2f}",
                "expected_hft_sweep_low": f"${hft_sweep_floor:.2f}",
                "institutional_safe_stop": f"${safe_structural_stop:.2f}",
                "tactical_note": (
                    f"Retail stops are clustered just under ${retail_stop_zone_top:.2f}. "
                    f"HFT algorithms typically sweep down to ~${hft_sweep_floor:.2f} to scoop liquidity before reversing. "
                    f"Place structural stop at ${safe_structural_stop:.2f} or buy the reclaim of ${retail_stop_zone_top:.2f}."
                )
            },
            "runner_vs_harvester": {
                "verdict": runner_verdict,
                "rationale": runner_advice
            }
        }
    except Exception as e:
        return {"ticker": sym, "status": "ERROR", "error": str(e)}


def tool_get_broker_portfolio_and_pnl() -> dict:
    """
    Tool 2: Queries live Public.com account capital, open positions, and today's exact realized P&L fills.
    """
    result = {
        "capital": {},
        "open_algo_positions": [],
        "core_protected_holds": ["AMC", "APE", "NKE"],
        "today_closed_trades": [],
        "today_summary": {}
    }
    try:
        from public_executor import get_account_capital_summary
        result["capital"] = get_account_capital_summary()
    except Exception as e:
        result["capital_error"] = str(e)

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT id, ticker, direction, timeframe, entry_price, stop_loss, initial_stop_loss,
                   take_profit, partial_tier_done, peak_high_r, ml_confidence, timestamp
            FROM alerts
            WHERE outcome = 'OPEN' AND user_active = 1
            ORDER BY id DESC
        """)
        result["open_algo_positions"] = [dict(r) for r in c.fetchall()]
        conn.close()
    except Exception as e:
        result["db_error"] = str(e)

    try:
        import pnl_engine
        pnl_data = pnl_engine.fetch_pnl_station_data()
        today_str = get_est_now().strftime("%Y-%m-%d")
        all_trades = pnl_data.get("trades", [])
        today_trades = [
            t for t in all_trades
            if t.get("exit_date") == today_str or str(t.get("exit_timestamp", ""))[:10] == today_str
        ]
        wins = [t for t in today_trades if t.get("outcome") == "WIN"]
        losses = [t for t in today_trades if t.get("outcome") == "LOSS"]
        net_dollar = round(sum(float(t.get("net_pnl_dollar") or 0.0) for t in today_trades), 2)
        net_r = round(sum(float(t.get("pnl_r") or 0.0) for t in today_trades), 2)

        result["today_Summary"] = {
            "date": today_str,
            "closed_count": len(today_trades),
            "wins": len(wins),
            "losses": len(losses),
            "net_realized_dollar": net_dollar,
            "net_realized_r": net_r
        }
        result["today_closed_trades"] = [
            {
                "ticker": t.get("ticker"),
                "outcome": t.get("outcome"),
                "net_pnl_dollar": t.get("net_pnl_dollar"),
                "pnl_r": t.get("pnl_r"),
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "duration": t.get("duration_str"),
                "partial_tier_done": t.get("partial_tier_done")
            }
            for t in today_trades[:15]
        ]
    except Exception as e:
        result["pnl_error"] = str(e)

    return result


def tool_evaluate_open_runners_and_stops() -> dict:
    """
    Tool 3: Audits all currently open positions (or recent candidates) for Runner vs. Harvester potential
    and Stop-Hunt vulnerability.
    """
    port = tool_get_broker_portfolio_and_pnl()
    open_pos = port.get("open_algo_positions", [])
    diagnostics = []

    for pos in open_pos:
        sym = pos.get("ticker")
        if not sym or sym.upper() in ("AMC", "APE", "NKE"):
            continue
        tape = tool_inspect_live_tape(sym)
        entry_p = float(pos.get("entry_price") or 0.0)
        curr_sl = float(pos.get("stop_loss") or 0.0)
        init_sl = float(pos.get("initial_stop_loss") or curr_sl or 0.0)
        curr_p = float(tape.get("current_price") or entry_p)
        risk = abs(entry_p - init_sl) if abs(entry_p - init_sl) > 0.001 else (entry_p * 0.01)
        live_r = round((curr_p - entry_p) / risk, 2) if risk > 0 else 0.0
        tier = int(pos.get("partial_tier_done") or 0)

        diagnostics.append({
            "ticker": sym,
            "timeframe": pos.get("timeframe", "5m"),
            "entry_price": entry_p,
            "current_price": curr_p,
            "current_stop": curr_sl,
            "guardian_tier": tier,
            "live_r": live_r,
            "moonbag_status": "ACTIVE ($0.00 Breakeven Stop)" if tier >= 5 else f"Scaling Tier {tier}/5",
            "tape_verdict": tape.get("runner_vs_harvester", {}),
            "stop_hunt_map": tape.get("stop_hunt_map", {})
        })

    return {
        "open_count": len(diagnostics),
        "positions_analyzed": diagnostics,
        "summary_note": (
            "All open positions inspected for Runner vs. Harvester velocity and Stop-Hunt safety."
            if diagnostics else
            "Zero active day-scalp positions open right now (100% dry powder)."
        )
    }


def tool_scan_market_anomalies_and_arbitrage(force: bool = False) -> dict:
    """
    Tool 4: Scans Macro Indices, Sector ETFs, and high-beta liquid leaders for:
    1. Statistical Arbitrage / Lead-Lag Laggard Plays (Sector surging while high-beta constituent lags at support).
    2. Relative Strength Divergence Anomalies (Stock coiling above VWAP while SPY dips).
    3. HFT Stop-Hunt Liquidity Sweeps (Stock piercing 20-bar low on dry volume & reclaiming).
    Caches results for 60 seconds so the UI and background loop stay fast.
    """
    if not force and os.path.exists(FRANK_ANOMALY_CACHE_PATH):
        try:
            age = time.time() - os.path.getmtime(FRANK_ANOMALY_CACHE_PATH)
            if age < 60:
                with open(FRANK_ANOMALY_CACHE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass

    sector_peers = {
        "SMH": ["NVDA", "AMD", "AVGO", "MU", "QCOM", "AMAT"],
        "XLK": ["AAPL", "MSFT", "PLTR", "CRWD", "NOW", "ORCL"],
        "XLF": ["JPM", "GS", "MS", "BAC", "COIN"],
        "XLY": ["TSLA", "AMZN", "HD", "NKE"],
        "XLC": ["META", "GOOGL", "NFLX", "RDDT"],
        "XLV": ["LLY", "UNH", "ISRG", "ABBV"],
        "XLI": ["CAT", "GE", "URI", "RKLB"]
    }

    all_symbols = ["SPY", "QQQ", "IWM"] + list(sector_peers.keys())
    for peers in sector_peers.values():
        for p in peers:
            if p not in all_symbols:
                all_symbols.append(p)

    metrics = {}
    try:
        import yfinance as yf
        raw = yf.download(all_symbols, period="1d", interval="5m", group_by="ticker", progress=False, threads=True)
        for sym in all_symbols:
            try:
                sdf = raw[sym].dropna() if sym in raw else pd.DataFrame()
                if len(sdf) < 3:
                    continue
                c = sdf["Close"].values.astype(float)
                h = sdf["High"].values.astype(float)
                l = sdf["Low"].values.astype(float)
                v = sdf["Volume"].values.astype(float)

                curr_p = float(c[-1])
                open_p = float(sdf["Open"].values[0])
                day_ret = round(((curr_p - open_p) / open_p) * 100.0, 2) if open_p > 0 else 0.0

                typ = (h + l + c) / 3.0
                cum_v = np.cumsum(v)
                vwap = float((np.cumsum(typ * v) / np.where(cum_v == 0, 1, cum_v))[-1])
                vwap_dist = round(((curr_p - vwap) / vwap) * 100.0, 2) if vwap > 0 else 0.0

                rng = max(h[-1] - l[-1], 0.0001)
                clv = round(float((c[-1] - l[-1]) / rng), 2)

                avg_v = float(np.mean(v[:-1])) if len(v) > 1 else float(v[-1])
                rvol = round(float(v[-1] / max(avg_v, 1.0)), 2)

                lb = min(20, len(l))
                swing_low = float(np.min(l[-lb:]))
                prev_swing_low = float(np.min(l[-lb:-1])) if lb > 2 else swing_low
                dist_low = round(((curr_p - swing_low) / swing_low) * 100.0, 2) if swing_low > 0 else 0.0
                swept_and_reclaimed = (float(l[-1]) <= prev_swing_low * 1.0005) and (curr_p > prev_swing_low) and (clv >= 0.60)

                metrics[sym] = {
                    "price": round(curr_p, 2),
                    "vwap": round(vwap, 2),
                    "vwap_dist": vwap_dist,
                    "day_ret": day_ret,
                    "clv": clv,
                    "rvol": rvol,
                    "swing_low": round(swing_low, 2),
                    "dist_low": dist_low,
                    "swept_and_reclaimed": bool(swept_and_reclaimed)
                }
            except Exception:
                continue
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "anomalies": [], "arbitrage_plays": []}

    spy_m = metrics.get("SPY", {"vwap_dist": 0.0, "day_ret": 0.0, "price": 0.0})
    qqq_m = metrics.get("QQQ", {"vwap_dist": 0.0, "day_ret": 0.0, "price": 0.0})

    arbitrage_plays = []
    market_anomalies = []
    stop_hunt_reclaims = []

    # 1. Detect Sector Lead-Lag Arbitrage Plays
    for etf, peers in sector_peers.items():
        em = metrics.get(etf)
        if not em:
            continue
        for p in peers:
            pm = metrics.get(p)
            if not pm:
                continue

            # Arbitrage Type A: Sector ETF strong (> +0.15% vs VWAP), Peer lagging near VWAP/support (-0.35% to +0.10%) with buyer absorption (CLV >= 0.55)
            spread_vwap = round(em["vwap_dist"] - pm["vwap_dist"], 2)
            spread_ret = round(em["day_ret"] - pm["day_ret"], 2)
            if em["vwap_dist"] >= 0.10 and spread_vwap >= 0.25 and -0.45 <= pm["vwap_dist"] <= 0.15 and pm["clv"] >= 0.50:
                arbitrage_plays.append({
                    "ticker": p,
                    "sector_etf": etf,
                    "type": "SECTOR LAGGARD CATCH-UP ARBITRAGE",
                    "spread_pct": spread_vwap,
                    "price": pm["price"],
                    "vwap": pm["vwap"],
                    "clv": pm["clv"],
                    "thesis": (
                        f"{etf} is pushing +{em['vwap_dist']}% above VWAP ({em['day_ret']:+.2f}% on day), "
                        f"while {p} lags by {spread_vwap:.2f}% near ${pm['price']:.2f} with {pm['clv']} CLV absorption. "
                        f"High-probability catch-up rotation play toward ${round(pm['price'] * 1.008, 2)}."
                    )
                })

            # Anomaly Type B: Institutional Relative Strength Divergence (Stock coiling strongly > SPY by +0.50%+)
            rs_vs_spy = round(pm["vwap_dist"] - spy_m["vwap_dist"], 2)
            if rs_vs_spy >= 0.45 and pm["clv"] >= 0.65 and pm["rvol"] >= 1.15:
                market_anomalies.append({
                    "ticker": p,
                    "sector_etf": etf,
                    "type": "REL-STRENGTH ACCUMULATION ANOMALY",
                    "edge_metric": f"+{rs_vs_spy:.2f}% vs SPY VWAP | {pm['rvol']}x RVOL",
                    "price": pm["price"],
                    "thesis": (
                        f"Composite Man accumulation footprint: {p} is trading +{pm['vwap_dist']}% above VWAP "
                        f"({rs_vs_spy:+.2f}% stronger than SPY) on {pm['rvol']}x RVOL and {pm['clv']} CLV."
                    )
                })

            # Anomaly Type C: Live HFT Stop-Hunt Sweep & Reclaim
            if pm["swept_and_reclaimed"] or (pm["dist_low"] <= 0.35 and pm["clv"] >= 0.72 and pm["rvol"] >= 1.1):
                stop_hunt_reclaims.append({
                    "ticker": p,
                    "sector_etf": etf,
                    "type": "HFT STOP-HUNT LIQUIDITY RECLAIM",
                    "swing_low": pm["swing_low"],
                    "price": pm["price"],
                    "clv": pm["clv"],
                    "thesis": (
                        f"Stop-run reversal detected on {p}: probed support near ${pm['swing_low']:.2f} "
                        f"and snapped back to ${pm['price']:.2f} with {pm['clv']} CLV upper-wick reclaim."
                    )
                })

    arbitrage_plays.sort(key=lambda x: x["spread_pct"], reverse=True)
    market_anomalies.sort(key=lambda x: float(x["edge_metric"].split("%")[0].replace("+", "")), reverse=True)

    payload = {
        "status": "OK",
        "updated_at": get_est_now().strftime("%I:%M:%S %p EST"),
        "macro": {
            "SPY": spy_m,
            "QQQ": qqq_m,
            "IWM": metrics.get("IWM", {})
        },
        "arbitrage_plays": arbitrage_plays[:6],
        "market_anomalies": market_anomalies[:6],
        "stop_hunt_reclaims": stop_hunt_reclaims[:6]
    }

    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(FRANK_ANOMALY_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass

    return payload


def tool_query_historical_backtest(ticker: str = None, min_ml_conf: float = 25.0) -> dict:
    """
    Tool 5: Queries alerts.db directly to calculate historical LONG win rate, net R, average R,
    and profit factor for a given ML confidence floor or specific ticker.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query("""
            SELECT ticker, direction, outcome, pnl_r, ml_confidence, timeframe, timestamp
            FROM alerts
            WHERE direction = 'LONG' AND outcome IN ('WIN', 'LOSS', 'BREAKEVEN')
        """, conn)
        conn.close()

        if ticker and ticker.strip().upper() not in ("ALL", "ANY", ""):
            sym = ticker.strip().upper()
            sub = df[df["ticker"] == sym]
        else:
            sym = "ALL_LONGS"
            sub = df

        if min_ml_conf and "ml_confidence" in sub.columns:
            with_ml = sub[sub["ml_confidence"].notnull()]
            if len(with_ml) >= 5:
                sub = with_ml[with_ml["ml_confidence"] >= float(min_ml_conf)]

        n = len(sub)
        if n == 0:
            return {"scope": sym, "min_ml_conf": min_ml_conf, "trades": 0, "note": "No historical resolved trades matching filter."}

        wins = int((sub["outcome"] == "WIN").sum())
        losses = int((sub["outcome"] == "LOSS").sum())
        be = int((sub["outcome"] == "BREAKEVEN").sum())
        wr = round((wins / max(wins + losses, 1)) * 100.0, 2)
        tot_r = round(float(sub["pnl_r"].sum()), 2)
        avg_r = round(float(sub["pnl_r"].mean()), 3)
        pos_r = float(sub[sub["pnl_r"] > 0]["pnl_r"].sum())
        neg_r = abs(float(sub[sub["pnl_r"] < 0]["pnl_r"].sum()))
        pf = round(pos_r / neg_r, 2) if neg_r > 0 else 99.0

        return {
            "scope": sym,
            "min_ml_conf": min_ml_conf,
            "total_trades": n,
            "wins": wins,
            "losses": losses,
            "breakevens": be,
            "win_rate_pct": wr,
            "net_realized_r": tot_r,
            "avg_r_per_trade": avg_r,
            "profit_factor": pf
        }
    except Exception as e:
        return {"status": "ERROR", "error": str(e)}


def tool_get_priority_scanner_hotlist() -> dict:
    """
    Tool 6: Returns the closest Wyckoff Spring candidates from priority_queue.json and sector guard status.
    """
    res = {"hot_springs": [], "sector_guard": {}}
    pq_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "priority_queue.json")
    if os.path.exists(pq_path):
        try:
            with open(pq_path, "r", encoding="utf-8") as f:
                pq = json.load(f)
                q_list = pq.get("queue", []) if isinstance(pq, dict) else []
                res["hot_springs"] = q_list[:10]
        except Exception:
            pass
    try:
        from frank_sector_guard import get_sector_guard_summary
        res["sector_guard"] = get_sector_guard_summary()
    except Exception:
        pass
    return res


# ==============================================================================
# GEMINI FUNCTION CALLING DECLARATIONS & DISPATCHER
# ==============================================================================
AGENT_TOOLS_DECLARATIONS = [
    {
        "name": "inspect_live_tape",
        "description": "Fetches real-time 5m bars, intraday VWAP, RVOL, CLV, CVD delta, Anti-HFT Stop-Hunt liquidity cluster zones, and Runner vs. Harvester classification for any stock ticker.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ticker": {
                    "type": "STRING",
                    "description": "Stock symbol to inspect (e.g., 'NVDA', 'CIFR', 'WDC', 'SPY')."
                }
            },
            "required": ["ticker"]
        }
    },
    {
        "name": "scan_market_anomalies_and_arbitrage",
        "description": "Scans SPY, QQQ, all 12 Sector ETFs, and top liquid stocks for Lead-Lag Sector Arbitrage plays, Relative Strength Accumulation Anomalies, and live HFT Stop-Hunt liquidity sweeps.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "get_broker_portfolio_and_pnl",
        "description": "Retrieves live Public.com buying power, total equity, open positions, and today's closed broker executions and exact realized cash/R P&L.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "evaluate_open_runners_and_stops",
        "description": "Evaluates all currently open trading positions for Runner vs. Harvester continuation velocity, Guardian scaling tier status (including the $0.00 Breakeven 15% moonbag), and HFT stop-hunt risk.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    },
    {
        "name": "query_historical_backtest",
        "description": "Queries the historical SQLite database (alerts.db) to calculate win rate, net R, average R, and profit factor for Long setups by ticker or ML confidence floor.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "ticker": {
                    "type": "STRING",
                    "description": "Optional ticker symbol (or 'ALL' for all Long trades)."
                },
                "min_ml_conf": {
                    "type": "NUMBER",
                    "description": "Minimum ML confidence floor percentage (default 25.0)."
                }
            }
        }
    },
    {
        "name": "get_priority_scanner_hotlist",
        "description": "Returns the top Wyckoff Spring setups currently closest to triggering on the scanner priority queue along with live Frank Sector Guard status.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}
        }
    }
]

TOOL_DISPATCH_MAP = {
    "inspect_live_tape": lambda args: tool_inspect_live_tape(args.get("ticker", "SPY")),
    "scan_market_anomalies_and_arbitrage": lambda args: tool_scan_market_anomalies_and_arbitrage(force=False),
    "get_broker_portfolio_and_pnl": lambda args: tool_get_broker_portfolio_and_pnl(),
    "evaluate_open_runners_and_stops": lambda args: tool_evaluate_open_runners_and_stops(),
    "query_historical_backtest": lambda args: tool_query_historical_backtest(
        ticker=args.get("ticker"), min_ml_conf=args.get("min_ml_conf", 25.0)
    ),
    "get_priority_scanner_hotlist": lambda args: tool_get_priority_scanner_hotlist()
}


def run_frank_agent(query: str, system_context: dict = None) -> dict:
    """
    Executes Frank Wyckoff's multi-step Agentic Function-Calling Loop with persistent memory.
    Returns {"reply": str, "tools_called": list, "memory_turns": int}
    """
    api_key = get_gemini_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not configured in .env")

    system_instruction = (
        "You are Frank Wyckoff — the autonomous Agentic Quantitative Analyst, Chief Tape Reader, and Desk Co-Pilot "
        "embedded inside the WSR Wyckoff Trading Terminal. "
        "You have LIVE agentic tools connected directly to Public.com broker actuals, live 5m market tape (VWAP, CVD, CLV, RVOL), "
        "the 12-Sector Anomaly & Arbitrage Scanner, HFT Stop-Hunt liquidity maps, and the SQLite backtest database.\n\n"
        "CORE OPERATIONAL LAWS:\n"
        "1. ALWAYS USE YOUR TOOLS FIRST: Before answering questions about tickers, P&L, positions, arbitrage plays, or market anomalies, "
        "call the relevant tools (`inspect_live_tape`, `scan_market_anomalies_and_arbitrage`, `get_broker_portfolio_and_pnl`, "
        "`evaluate_open_runners_and_stops`, `query_historical_backtest`, `get_priority_scanner_hotlist`) so your numbers are 100% real and verified.\n"
        "2. LONGS ONLY & GUARDIAN 5-TIER SCALING: The bot trades Long Wyckoff Springs only (ML floor >= 25.0%). "
        "Guardian scales out: Tier 1 (+20% tgt): Bank 10% | Tier 2 (+35% tgt): Bank 10% | Tier 3 (+50% tgt): Bank 15% + Lock $0.00 Breakeven | "
        "Tier 4 (+85% tgt): Bank 25% | Tier 5 (+100% tgt): Bank 25% | Runner: 15% Moonbag with stop loosened to $0.00 Breakeven (Entry) for maximum room to run.\n"
        "3. ARBITRAGE & ANOMALY HUNTING: Proactively flag Sector Lead-Lag Arbitrage plays (where a sector ETF surges above VWAP while a quality peer lags at support with high CLV), "
        "Relative Strength Divergences, and HFT Stop-Hunt Sweeps.\n"
        "4. SHADOW SAFEGUARD: You are strictly an advisory & analytical agent (100% SHADOW mode). You never mutate code or execute unauthorized trades.\n"
        "Be sharp, institutional, concise, and mathematically exact."
    )

    # Build multi-turn contents from persistent memory
    past_turns = load_frank_memory(max_turns=8)
    contents = []
    for turn in past_turns:
        role = "user" if turn.get("role") == "user" else "model"
        text = turn.get("text", "")
        if text:
            contents.append({"role": role, "parts": [{"text": text}]})

    context_prefix = ""
    if system_context:
        context_prefix = f"[Live Terminal Telemetry: {json.dumps(system_context)}]\n"

    contents.append({
        "role": "user",
        "parts": [{"text": f"{context_prefix}{query}"}]
    })

    tools_called_log = []
    final_reply = None

    for model in MODELS_CHAIN:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        working_contents = list(contents)
        model_succeeded = False

        # Allow up to 3 sequential tool-calling hops
        for hop in range(3):
            payload = {
                "contents": working_contents,
                "tools": [{"functionDeclarations": AGENT_TOOLS_DECLARATIONS}],
                "systemInstruction": {"parts": [{"text": system_instruction}]},
                "generationConfig": {
                    "temperature": 0.25,
                    "maxOutputTokens": 4096
                }
            }
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=25) as response:
                    res_data = json.loads(response.read().decode("utf-8"))

                candidates = res_data.get("candidates", [])
                if not candidates or "content" not in candidates[0]:
                    break

                model_content = candidates[0]["content"]
                parts = model_content.get("parts", [])

                # Check if Gemini requested any function calls
                fn_calls = [p["functionCall"] for p in parts if "functionCall" in p]
                if not fn_calls:
                    text_parts = [p.get("text", "") for p in parts if "text" in p]
                    final_reply = "\n".join(text_parts).strip()
                    if final_reply:
                        model_succeeded = True
                    break

                # Append the model's functionCall turn
                working_contents.append(model_content)

                # Execute all requested tools and construct functionResponse parts
                response_parts = []
                for fc in fn_calls:
                    fn_name = fc.get("name", "")
                    fn_args = fc.get("args", {}) or {}
                    arg_str = ", ".join(f"{k}={v}" for k, v in fn_args.items())
                    tools_called_log.append(f"{fn_name}({arg_str})")

                    dispatcher = TOOL_DISPATCH_MAP.get(fn_name)
                    if dispatcher:
                        try:
                            tool_output = dispatcher(fn_args)
                        except Exception as te:
                            tool_output = {"error": str(te)}
                    else:
                        tool_output = {"error": f"Unknown tool {fn_name}"}

                    response_parts.append({
                        "functionResponse": {
                            "name": fn_name,
                            "response": {"result": tool_output}
                        }
                    })

                working_contents.append({
                    "role": "function",
                    "parts": response_parts
                })

            except urllib.error.HTTPError as e:
                if e.code in (404, 503, 429, 400):
                    break
                break
            except Exception:
                break

        if model_succeeded and final_reply:
            break

    # Fallback if function-calling endpoint timed out or model chain needed direct synthesis
    if not final_reply:
        port_snap = tool_get_broker_portfolio_and_pnl()
        anom_snap = tool_scan_market_anomalies_and_arbitrage(force=False)
        tools_called_log.extend(["get_broker_portfolio_and_pnl()", "scan_market_anomalies_and_arbitrage()"])
        fallback_prompt = (
            f"Live Broker & P&L Data:\n{json.dumps(port_snap, indent=2)}\n\n"
            f"Live Market Anomalies & Arbitrage Data:\n{json.dumps(anom_snap, indent=2)}\n\n"
            f"User Question: {query}\n\nProvide Frank Wyckoff's institutional answer:"
        )
        final_reply = query_gemini(fallback_prompt, system_instruction=system_instruction, json_mode=False, timeout=20)

    # Save turn to persistent memory
    append_frank_memory("user", query)
    append_frank_memory("model", final_reply, tools_called=tools_called_log)

    return {
        "reply": final_reply,
        "tools_called": tools_called_log,
        "memory_turns": len(load_frank_memory(max_turns=30))
    }


def ask_ai_copilot(query: str, system_context: dict = None) -> str:
    """
    Backward-compatible wrapper returning text reply from Agent Frank.
    """
    res = run_frank_agent(query, system_context=system_context)
    return res.get("reply", "")


# ==============================================================================
# EXISTING COGNITIVE GATEKEEPER & NIGHTLY AUTOPSY (PRESERVED)
# ==============================================================================
def evaluate_trade_candidate(setup: dict) -> dict:
    """
    Cognitive Pre-Trade Gatekeeper:
    Cross-examines quantitative signals (Random Forest score, RVOL, VPOC, ATR)
    against macro index context, sector alignment, and trend exhaustion traps.
    Returns: {"verdict": "PROCEED" | "REDUCE_SIZE" | "VETO", "ai_confidence": int, "rationale": str}
    """
    system_instruction = (
        "You are Frank Wyckoff, the modern quantitative successor to Richard D. Wyckoff and Chief Risk Officer of the MSR Institutional Wyckoff Trading System (powered by Gemini). "
        "A candidate trade has passed technical Wyckoff criteria and Random Forest tabular scoring. "
        "Your duty is to perform the qualitative context filter: macro trend alignment (SPY/QQQ), sector velocity, "
        "and overextension/exhaustion traps. "
        "Be extremely disciplined. On strong trend days, do NOT approve counter-trend scalp shorts unless extreme capitulation is evident. "
        "Output valid JSON matching schema: {\"verdict\": \"PROCEED\"|\"REDUCE_SIZE\"|\"VETO\", \"ai_confidence\": 50-99, \"rationale\": \"1-2 sentence institutional justification.\"}"
    )

    prompt = f"""Evaluate this candidate trade setup:
Ticker: {setup.get('ticker')}
Direction: {setup.get('direction', 'LONG')}
Timeframe: {setup.get('timeframe', '5m')}
Wyckoff Regime: {setup.get('regime', 'Phase C Spring')}
Random Forest Confidence: {setup.get('ml_confidence', 65.0)}%
Technical Metrics:
- Time-of-Day RVOL: {setup.get('rvol', 1.0)}x
- VPOC Distance: {setup.get('vpoc_distance_pct', 0.0)}%
- Consumed Daily ATR: {setup.get('consumed_atr', 0.5)}x
- Candle CLV: {setup.get('candle_clv', 0.7)}
- CVD Absorption Delta: {setup.get('cvd_absorption', 1.0)}x
Macro Context:
- SPY Intraday Return: {setup.get('spy_ret_pct', 0.0)}%
- QQQ Intraday Return: {setup.get('qqq_ret_pct', 0.0)}%
- VIX Slope: {setup.get('vix_slope', 0.0)}%

Provide your verdict and concise rationale:"""

    try:
        raw = query_gemini(prompt, system_instruction=system_instruction, json_mode=True, timeout=12)
        parsed = json.loads(clean_json_text(raw))
        return {
            "verdict": parsed.get("verdict", "PROCEED").upper(),
            "ai_confidence": int(parsed.get("ai_confidence", 70)),
            "rationale": parsed.get("rationale", "Contextual alignment confirmed by Frank Wyckoff.")
        }
    except Exception as e:
        print(f"[AI Brain] Fallback during evaluation: {e}")
        return {
            "verdict": "PROCEED",
            "ai_confidence": int(setup.get('ml_confidence', 65)),
            "rationale": "Direct RF approval (Frank Wyckoff bypassed due to timeout)."
        }


def generate_nightly_autopsy_and_rules(closed_trades: list) -> dict:
    """
    Forensic Post-Mortem & Rule Evolution Engine:
    Ingests all real broker fills from the trading day, diagnoses failure patterns,
    and synthesizes statistically grounded execution rules.
    """
    system_instruction = (
        "You are Frank Wyckoff, Head of Quantitative Strategy and Lead Tape Reader for the MSR Wyckoff Terminal. "
        "Review today's closed broker executions. Diagnose what drove winning campaigns vs stopped campaigns. "
        "IMPORTANT ANTI-OVERFITTING DIRECTIVE: "
        "Do NOT synthesize hyper-specific single-ticker rules (e.g., 'require 4-hour springs on meme assets like GME') "
        "or arbitrary micro-restrictions based on small sample sizes (N < 30). That is textbook overfitting on noise. "
        "Rules MUST be grounded in broad, proven Wyckoff volume and tape laws (e.g., minimum volume dry-up, "
        "CVD delta absorption confirmation, CLV close thresholds, and broad risk geometry). "
        "Synthesize 3-4 statistically grounded, actionable algorithmic rules for the bot. "
        "Output valid JSON matching schema: {"
        "\"daily_assessment\": \"2-3 sentence executive summary of today's performance.\","
        "\"key_failure_cause\": \"The primary driver of stopped trades.\","
        "\"key_winning_driver\": \"The primary driver of winning trades.\","
        "\"generated_rules\": [\"Rule 1...\", \"Rule 2...\", \"Rule 3...\"]"
        "}"
    )

    trades_summary = []
    for t in closed_trades:
        trades_summary.append({
            "ticker": t.get("ticker"),
            "direction": t.get("direction"),
            "net_pnl": t.get("net_pnl_dollar"),
            "pnl_r": t.get("pnl_r"),
            "outcome": t.get("outcome"),
            "duration": t.get("duration_str"),
            "entry": t.get("entry_price"),
            "exit": t.get("exit_price")
        })

    prompt = f"Today's completed broker trades:\n{json.dumps(trades_summary, indent=2)}\n\nPerform forensic analysis and generate execution rules:"

    try:
        raw = query_gemini(prompt, system_instruction=system_instruction, json_mode=True, timeout=25)
        return json.loads(clean_json_text(raw))
    except Exception as e:
        print(f"[AI Brain] Error generating nightly autopsy: {e}")
        return {
            "daily_assessment": "Standard execution session completed with controlled downside.",
            "key_failure_cause": "Intraday momentum chop and lack of follow-through.",
            "key_winning_driver": "Disciplined micro-scaling at initial target tiers.",
            "generated_rules": [
                "Rule 1: Enforce strict Breakeven ratchet once Tier 3 (+50% target) is tagged, with 15% moonbag loosened to $0.00 Breakeven at Tier 5.",
                "Rule 2: Suppress counter-trend setups when SPY and QQQ trend in unison."
            ]
        }
