"""
FRANK - Autonomous Institutional Quantitative Co-Pilot & AI Brain
MSR Wyckoff Terminal Engine
Powered by Google Gemini 3.5 Flash Cognitive Layer
"""
import os
import sys

# Re-export core cognitive functions from ai_brain under Frank namespace
from ai_brain import (
    evaluate_trade_candidate,
    generate_nightly_autopsy_and_rules,
    ask_ai_copilot,
    query_gemini,
    clean_json_text,
    get_gemini_api_key,
    MODELS_CHAIN
)

# Friendly functional aliases
ask_frank = ask_ai_copilot
evaluate_setup = evaluate_trade_candidate
run_autopsy = generate_nightly_autopsy_and_rules

__all__ = [
    "ask_frank",
    "evaluate_setup",
    "run_autopsy",
    "evaluate_trade_candidate",
    "generate_nightly_autopsy_and_rules",
    "ask_ai_copilot",
    "query_gemini",
    "clean_json_text",
    "get_gemini_api_key",
    "MODELS_CHAIN"
]

if __name__ == "__main__":
    import sys
    if sys.stdout.encoding.lower() != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    print("[Frank] Frank is online. Running diagnostic ping...")
    reply = ask_frank("Hey Frank, state your name and confirmation of readiness.")
    print(f"\nFrank's Response:\n{reply}\n")

from frank_mailer import send_frank_email, format_executive_briefing_html
from frank_auditor import compute_model_and_tape_telemetry, generate_frank_model_audit_report

email_briefing = send_frank_email
audit_model = generate_frank_model_audit_report
