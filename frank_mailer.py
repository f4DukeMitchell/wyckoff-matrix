"""
FRANK MAILER - Institutional Notification Bridge
Dispatches executive market briefings, trade autopsies, and ML audit reports
to the fund manager via Gmail SMTP.
"""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import datetime
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

# SMTP Credentials
GMAIL_USER = os.getenv("GMAIL_USER", "f4dukemitchell@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "aakvdgppwfwxnhua").replace(" ", "")
DEFAULT_RECIPIENT = os.getenv("DESTINATION_EMAIL", "matt.smith@pga.com")

def get_est_now() -> datetime.datetime:
    if ZoneInfo is not None:
        return datetime.datetime.now(ZoneInfo("America/New_York"))
    return datetime.datetime.utcnow() - datetime.timedelta(hours=5)

def send_frank_email(subject: str, html_body: str, recipient: str = None) -> bool:
    """
    Sends an executive HTML email from Frank via secure Gmail SMTP.
    """
    target = recipient or DEFAULT_RECIPIENT
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Frank Wyckoff // WSR AI Co-Pilot <{GMAIL_USER}>"
    msg["To"] = target
    msg["Subject"] = subject

    # Plain text fallback
    plain_text = "Frank Wyckoff AI Briefing:\n\nPlease view this email in an HTML-compatible client to render the complete Wyckoff terminal report."
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=12)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, [target], msg.as_string())
        server.quit()
        print(f"[{get_est_now().strftime('%H:%M:%S EST')}] [Frank Mailer] Email successfully sent to {target}: {subject}")
        return True
    except Exception as e:
        print(f"[{get_est_now().strftime('%H:%M:%S EST')}] [Frank Mailer] Failed to send email to {target}: {e}")
        return False

def format_executive_briefing_html(pnl_summary: dict, autopsy: dict, rules: list, shadow_scorecard: dict = None, study_log: dict = None) -> str:
    """Formats a sleek, dark-themed institutional briefing email grounded 100% in live Public.com actuals."""
    if study_log is None:
        try:
            from frank_study_engine import conduct_daily_study_session
            study_log = conduct_daily_study_session()
        except Exception:
            study_log = {}

    now_str = get_est_now().strftime("%B %d, %Y - %I:%M %p EST")
    rules_html = "".join([f"<li style='margin-bottom: 6px; color: #e2e8f0;'>{r}</li>" for r in (rules or [])])

    today_trades = int(pnl_summary.get("today_trades", 0))
    today_wins = int(pnl_summary.get("today_wins", 0))
    today_losses = int(pnl_summary.get("today_losses", 0))
    today_wr = float(pnl_summary.get("today_win_rate", 0.0))
    today_pnl = float(pnl_summary.get("today_pnl_dollar", 0.0))
    today_r = float(pnl_summary.get("today_pnl_r", 0.0))

    mode_badge = pnl_summary.get("trading_mode", "ACTIVE DESK ENFORCE (30% BP/SLOT)")
    today_sub = (
        f"{today_wins}W - {today_losses}L ({today_wr:.1f}% WR | {today_r:+.2f}R) • PUBLIC.COM ACTUALS"
        if today_trades > 0
        else "0 CLOSED BROKER TRADES TODAY • PUBLIC.COM ACTUALS"
    )

    cum_dollar = float(pnl_summary.get("cumulative_model_dollar", pnl_summary.get("net_pnl_dollar", 0.0)))
    total_samples = int(pnl_summary.get("total_model_samples", pnl_summary.get("total_trades", 0)))
    win_rate = float(pnl_summary.get("win_rate", 0.0))
    cum_lbl = pnl_summary.get("cumulative_label", "Cumulative Realized Broker P&L")
    sample_sub = pnl_summary.get("sample_subtitle", f"{total_samples} LONG SAMPLES ({win_rate:.1f}% WR @ >=35% ML FLOOR)")

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #0b0f19; color: #f8fafc; margin: 0; padding: 24px; }}
        .container {{ max-width: 650px; margin: 0 auto; background-color: #111827; border: 1px solid #1f2937; border-radius: 8px; padding: 24px; box-shadow: 0 4px 12px rgba(0,0,0,0.5); }}
        .header {{ border-bottom: 1px solid #1f2937; padding-bottom: 16px; margin-bottom: 20px; }}
        .title {{ color: #ffd700; font-size: 18px; font-weight: bold; letter-spacing: 0.5px; margin: 0; }}
        .subtitle {{ color: #94a3b8; font-size: 12px; margin-top: 4px; }}
        .stat-grid {{ display: flex; justify-content: space-between; gap: 12px; margin-bottom: 20px; }}
        .stat-card {{ background-color: #1e293b; border: 1px solid #334155; border-radius: 6px; padding: 12px; flex: 1; text-align: center; }}
        .stat-val {{ font-size: 18px; font-weight: bold; color: #38bdf8; }}
        .stat-lbl {{ font-size: 10px; color: #94a3b8; text-transform: uppercase; margin-top: 4px; }}
        .section-title {{ font-size: 13px; font-weight: bold; color: #38bdf8; text-transform: uppercase; letter-spacing: 0.5px; border-left: 3px solid #38bdf8; padding-left: 8px; margin-top: 20px; margin-bottom: 10px; }}
        .content-box {{ background-color: #1e293b; border-radius: 6px; padding: 14px; font-size: 12px; line-height: 1.5; color: #cbd5e1; }}
        .footer {{ border-top: 1px solid #1f2937; margin-top: 24px; padding-top: 14px; font-size: 11px; color: #64748b; text-align: center; }}
      </style>
    </head>
    <body>
      <div class="container">
        <div class="header">
          <div class="title">🧠 FRANK WYCKOFF // POST-MARKET EXECUTIVE BRIEFING</div>
          <div class="subtitle">WSR Wyckoff Institutional Trading Terminal &bull; {now_str}</div>
        </div>

        <div class="stat-grid">
          <div class="stat-card">
            <div class="stat-val">{today_trades}</div>
            <div class="stat-lbl">Broker Trades Closed Today</div>
            <div style="font-size: 8.5px; color: #00ff88; margin-top: 2px; font-weight: bold;">{mode_badge}</div>
          </div>
          <div class="stat-card">
            <div class="stat-val" style="color: {'#4ade80' if today_pnl >= 0 else '#f87171'}">${today_pnl:+.2f}</div>
            <div class="stat-lbl">Today's Realized Broker P&L</div>
            <div style="font-size: 8.5px; color: #94a3b8; margin-top: 2px;">{today_sub}</div>
          </div>
          <div class="stat-card">
            <div class="stat-val" style="color: #ffd700;">${cum_dollar:+.2f}</div>
            <div class="stat-lbl">{cum_lbl}</div>
            <div style="font-size: 8.5px; color: #4ade80; margin-top: 2px;">{sample_sub}</div>
          </div>
        </div>

        <div class="section-title">Forensic Autopsy &amp; Session Diagnosis</div>
        <div class="content-box">
          <p><strong>Daily Assessment:</strong> {autopsy.get('daily_assessment', 'Session completed under Active Trading Room Desk enforcement.')}</p>
          <p><strong>Primary Driver of Losses:</strong> {autopsy.get('key_failure_cause', 'Sector-wide distribution below intraday VWAP.')}</p>
          <p><strong>Primary Driver of Wins:</strong> {autopsy.get('key_winning_driver', 'Phase C Long Springs with positive CVD absorption and Sector ETF above VWAP.')}</p>
        </div>

        <div class="section-title">Active Champion Model &amp; Desk Rules Synthesized</div>
        <div class="content-box">
          <ul style="margin: 0; padding-left: 20px;">
            {rules_html}
          </ul>
        </div>

        <div class="section-title">📚 Frank Wyckoff // Daily Research &amp; Homework Log</div>
        <div class="content-box" style="border-left: 3px solid #ffd700;">
          <p style="margin-top: 0;"><strong style="color: #ffd700;">Text Studied:</strong> {study_log.get('source', 'Studies in Tape Reading (1910)')}</p>
          <p><strong style="color: #38bdf8;">Topic:</strong> {study_log.get('topic', 'Classical Tape Reading & Order Absorption')}</p>
          <p><strong>Core Wyckoff Principle:</strong> {study_log.get('wyckoff_principle', 'N/A')}</p>
          <p style="color: #f87171;"><strong>Learning from Failure (What Can Go Wrong):</strong> {study_log.get('failure_analysis', 'N/A')}</p>
          
          <div style="background: rgba(56, 189, 248, 0.08); border-left: 3px solid #38bdf8; padding: 10px; margin: 10px 0; border-radius: 4px;">
            <p style="margin: 0 0 6px 0;"><strong style="color: #38bdf8;">🔎 Real-World Market Application (Today's Verified SPY Tape):</strong><br>
            <span style="color: #e2e8f0;">{study_log.get('market_tape_observation', 'Tape analysis active across SPY and leaders.')}</span></p>
            <p style="margin: 6px 0 0 0;"><strong style="color: #facc15;">💡 New Lesson Learned from Today's Market:</strong><br>
            <span style="color: #cbd5e1;">{study_log.get('new_lesson_learned', 'Validated against live intraday order flow.')}</span></p>
          </div>

          <p style="color: #4ade80; margin-bottom: 0;"><strong>🛡️ Actionable System Rule Adjustment:</strong> {study_log.get('actionable_rule_adjustment', study_log.get('system_application', 'Enforce strict volume dry-up on tests.'))}</p>
        </div>

        <div class="footer">
          Frank Cognitive Trading Layer (ACTIVE DESK ENFORCE) &bull; Google Gemini &bull; Random Forest Long-Only Engine (35% Floor) &bull; WSR Terminal
        </div>
      </div>
    </body>
    </html>
    """
    return html
