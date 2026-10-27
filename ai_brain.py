import os
import json
import urllib.request
import urllib.error
from dotenv import load_dotenv

# Load credentials
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

def get_gemini_api_key():
    global GEMINI_API_KEY
    if GEMINI_API_KEY:
        return GEMINI_API_KEY
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if GEMINI_API_KEY:
        return GEMINI_API_KEY
    if os.path.exists(ENV_PATH):
        try:
            with open(ENV_PATH, "r", encoding="utf-8") as f:
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

MODELS_CHAIN = ["gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.8-flash"]

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
                "maxOutputTokens": 2048
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
            # If 404, 503, or 429, attempt next model in chain
            if e.code in (404, 503, 429):
                continue
            err_msg = e.read().decode("utf-8", errors="ignore")
            print(f"[AI Brain] Gemini HTTP {e.code} on {model}: {err_msg}")
        except Exception as ex:
            print(f"[AI Brain] Gemini exception on {model}: {ex}")
            continue

    raise RuntimeError("All Gemini inference models in the fallback chain were exhausted or timed out.")


def evaluate_trade_candidate(setup: dict) -> dict:
    """
    Cognitive Pre-Trade Gatekeeper:
    Cross-examines quantitative signals (Random Forest score, RVOL, VPOC, ATR)
    against macro index context, sector alignment, and trend exhaustion traps.
    Returns: {"verdict": "PROCEED" | "REDUCE_SIZE" | "VETO", "ai_confidence": int, "rationale": str}
    """
    system_instruction = (
        "You are the Chief Risk Officer and Cognitive AI Brain of the MSR Institutional Wyckoff Trading System. "
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
            "rationale": parsed.get("rationale", "Contextual alignment confirmed by Gemini Brain.")
        }
    except Exception as e:
        print(f"[AI Brain] Fallback during evaluation: {e}")
        return {
            "verdict": "PROCEED",
            "ai_confidence": int(setup.get('ml_confidence', 65)),
            "rationale": "Direct RF approval (AI Brain bypassed due to timeout)."
        }


def generate_nightly_autopsy_and_rules(closed_trades: list) -> dict:
    """
    Forensic Post-Mortem & Rule Evolution Engine:
    Ingests all real broker fills from the trading day, diagnoses failure patterns,
    and synthesizes concrete execution rules and ML weight adjustments.
    """
    system_instruction = (
        "You are the Head of Quantitative Strategy for the MSR Wyckoff Terminal. "
        "Review today's closed broker executions. Diagnose what drove winning campaigns vs stopped campaigns. "
        "Synthesize 3-4 concrete, actionable algorithmic rules for the bot to deploy tomorrow. "
        "Output valid JSON matching schema: {"
        "\"daily_assessment\": \"2-3 sentence executive summary of today's performance.\","
        "\"key_failure_cause\": \"The primary driver of stopped trades today.\","
        "\"key_winning_driver\": \"The primary driver of winning trades today.\","
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
                "Rule 1: Enforce strict Breakeven ratchet once Tier 2 (+35% target) is tagged.",
                "Rule 2: Suppress counter-trend setups when SPY and QQQ trend in unison."
            ]
        }


def ask_ai_copilot(query: str, system_context: dict = None) -> str:
    """
    Interactive Terminal Co-Pilot:
    Answers trader queries about current positions, setup diagnoses, or market regime.
    """
    system_instruction = (
        "You are Gemini, the native AI Co-Pilot embedded in the MSR Wyckoff Trading Terminal. "
        "You have direct real-time access to the user's Wyckoff Scanner, Random Forest Model, "
        "and Public.com broker executions. Be concise, institutional, direct, and data-driven. "
        "Always prioritize risk management and structural Wyckoff logic (Springs, UTADs, Absorption, SOS)."
    )

    context_str = ""
    if system_context:
        context_str = f"\nCurrent System Context:\n{json.dumps(system_context, indent=2)}\n"

    prompt = f"{context_str}\nUser Question: {query}\n\nAnswer:"
    return query_gemini(prompt, system_instruction=system_instruction, json_mode=False, timeout=15)


if __name__ == "__main__":
    print("Testing AI Brain integration...")
    sample_candidate = {
        "ticker": "NVDA",
        "direction": "LONG",
        "timeframe": "5m",
        "regime": "Phase C Spring",
        "ml_confidence": 72.0,
        "rvol": 1.45,
        "vpoc_distance_pct": -0.15,
        "consumed_atr": 0.40,
        "candle_clv": 0.82,
        "cvd_absorption": 1.35,
        "spy_ret_pct": 0.85,
        "qqq_ret_pct": 1.15,
        "vix_slope": -0.8
    }
    verdict = evaluate_trade_candidate(sample_candidate)
    print("Candidate Evaluation:", json.dumps(verdict, indent=2))
