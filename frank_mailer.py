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

# SMTP Credentials
GMAIL_USER = os.getenv("GMAIL_USER", "f4dukemitchell@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "aakvdgppwfwxnhua").replace(" ", "")
DEFAULT_RECIPIENT = os.getenv("DESTINATION_EMAIL", "matt.smith@pga.com")

def send_frank_email(subject: str, html_body: str, recipient: str = None) -> bool:
    """
    Sends an executive HTML email from Frank via secure Gmail SMTP.
    """
    target = recipient or DEFAULT_RECIPIENT
    msg = MIMEMultipart("alternative")
    msg["From"] = f"Frank Wyckoff // MSR AI Co-Pilot <{GMAIL_USER}>"
    msg["To"] = target
    msg["Subject"] = subject

    # Plain text fallback
    plain_text = f"Frank Wyckoff AI Briefing:\n\nPlease view this email in an HTML-compatible client to render the complete Wyckoff terminal report."
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587, timeout=12)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, [target], msg.as_string())
        server.quit()
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [Frank Mailer] Email successfully sent to {target}: {subject}")
        return True
    except Exception as e:
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] [Frank Mailer] Failed to send email to {target}: {e}")
        return False

def format_executive_briefing_html(pnl_summary: dict, autopsy: dict, rules: list, shadow_scorecard: dict = None, study_log: dict = None) -> str:
    if study_log is None:
        try:
            from frank_study_engine import conduct_daily_study_session
            study_log = conduct_daily_study_session()
        except Exception:
            study_log = {}
    """Formats a sleek, dark-themed institutional briefing email."""
    now_str = datetime.datetime.now().strftime("%B %d, %Y - %I:%M %p EST")
    
    rules_html = "".join([f"<li style='margin-bottom: 6px; color: #e2e8f0;'>{r}</li>" for r in (rules or [])])
    
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
          <div class="subtitle">MSR Wyckoff Institutional Trading Terminal &bull; {now_str}</div>
        </div>

        <div class="stat-grid">
          <div class="stat-card">
            <div class="stat-val">{pnl_summary.get('total_trades', 0)}</div>
            <div class="stat-lbl">Trades Today</div>
          </div>
          <div class="stat-card">
            <div class="stat-val" style="color: {'#4ade80' if pnl_summary.get('net_pnl_dollar', 0) >= 0 else '#f87171'}">${pnl_summary.get('net_pnl_dollar', 0.0):+.2f}</div>
            <div class="stat-lbl">Day Realized P&L</div>
          </div>
          <div class="stat-card">
            <div class="stat-val">{pnl_summary.get('win_rate', 0.0):.1f}%</div>
            <div class="stat-lbl">Win Rate (Ex-BE)</div>
          </div>
        </div>

        <div class="section-title">Forensic Autopsy & Session Diagnosis</div>
        <div class="content-box">
          <p><strong>Daily Assessment:</strong> {autopsy.get('daily_assessment', 'Session completed within risk thresholds.')}</p>
          <p><strong>Primary Driver of Losses:</strong> {autopsy.get('key_failure_cause', 'Microstructure chop and lack of sustained volume expansion.')}</p>
          <p><strong>Primary Driver of Wins:</strong> {autopsy.get('key_winning_driver', 'Clean Phase C absorption followed by Sign of Strength (SOS).')}</p>
        </div>

        <div class="section-title">Active Champion Model Rules Synthesized</div>
        <div class="content-box">
          <ul style="margin: 0; padding-left: 20px;">
            {rules_html}
          </ul>
        </div>

        <div class="section-title">📚 Frank Wyckoff // Daily Research & Homework Log</div>
        <div class="content-box" style="border-left: 3px solid #ffd700;">
          <p style="margin-top: 0;"><strong style="color: #ffd700;">Text Studied:</strong> {study_log.get('source', 'Studies in Tape Reading (1910)')}</p>
          <p><strong style="color: #38bdf8;">Topic:</strong> {study_log.get('topic', 'Classical Tape Reading & Order Absorption')}</p>
          <p><strong>Core Wyckoff Principle:</strong> {study_log.get('wyckoff_principle', 'N/A')}</p>
          <p style="color: #f87171;"><strong>Learning from Failure (What Can Go Wrong):</strong> {study_log.get('failure_analysis', 'N/A')}</p>
          <p style="color: #4ade80;"><strong>Quantitative Application:</strong> {study_log.get('system_application', 'N/A')}</p>
        </div>

        <div class="footer">
          Frank Cognitive Trading Layer &bull; Google Gemini 3.5 Flash &bull; Random Forest Tabular Engine &bull; MSR Terminal
        </div>
      </div>
    </body>
    </html>
    """
    return html

if __name__ == "__main__":
    print("Testing Frank Mailer dispatch...")
    sample_pnl = {"total_trades": 8, "net_pnl_dollar": 142.50, "win_rate": 62.5}
    sample_autopsy = {
        "daily_assessment": "Markets exhibited strong absorption in tech and industrial names with disciplined risk scaling.",
        "key_failure_cause": "Premature counter-trend short attempts against strong SPY trend.",
        "key_winning_driver": "High-CLV Phase C Springs with secondary test volume dry-up."
    }
    sample_rules = [
        "Enforce 30-minute minimum duration filter on primary entries.",
        "Suppress counter-trend short setups when SPY and QQQ trend in unison.",
        "Rachet stops to breakeven once +0.35R profit tier is reached."
    ]
    html = format_executive_briefing_html(sample_pnl, sample_autopsy, sample_rules)
    success = send_frank_email("[TEST] Frank // Post-Market Briefing & Autopsy", html)
    print("Dispatch status:", success)
