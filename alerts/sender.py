"""
alerts/sender.py
CPOI Platform — Alert Email Sender

Sends the formatted CP alert email via SendGrid for a single alert record.
Updates the alert record on successful delivery and writes to audit_log.

Public interface:
  send_alert_email(
      conn, alert_id, recipient_email, client_name, drafted_message
  ) -> bool

Email format (spec Part 6 Module 4):
  FROM:    intelligence@criterion-partners.com  (CPOI_SENDER_EMAIL env var)
  TO:      recipient_email
  SUBJECT: [SEVERITY] Operational Intelligence Alert -- [Client] -- [Signal Name]
  BODY:
    CP letterhead
    AI-generated 4-sentence alert message
    Signal data table: Signal | Value | Threshold | Severity
    Footer

Configuration (environment variables):
  SENDGRID_API_KEY   -- SendGrid API key (required; no default)
  CPOI_SENDER_EMAIL  -- verified sender identity
                        (default: intelligence@criterion-partners.com)

Return contract:
  True   -- email accepted by SendGrid (HTTP 202), alert record updated,
            audit log written.
  False  -- alert not found in database, or email_sent is already TRUE
            (idempotency guard). No send attempted.

Error contract:
  Raises RuntimeError if SendGrid returns an HTTP error (non-202 response).
  The alert record is NOT updated on error. The caller (alerts/queue.py
  circuit breaker) catches this exception, queues the alert for retry, and
  never silently drops it.

  Raises ValueError if SENDGRID_API_KEY is not set in the environment.

No commit:
  This function never calls conn.commit(). The caller owns the transaction.

Privacy:
  drafted_message arrives from alerts/narrator.py with CLIENT_A as the
  client placeholder. This function substitutes CLIENT_A with client_name
  before building the email body. The substitution is applied only in the
  delivered email body -- the alerts.alert_message column retains the
  original drafted text.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content, MimeType

from db.audit import write_audit_log

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _build_logger(name: str) -> logging.Logger:
    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
            return json.dumps(payload)

    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


log = _build_logger("cpoi.alerts.sender")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_SENDER_EMAIL: str = "intelligence@criterion-partners.com"


# ---------------------------------------------------------------------------
# Email construction helpers
# ---------------------------------------------------------------------------


def _format_signal_name(signal_name: str) -> str:
    """Convert snake_case signal name to Title Case for display."""
    return signal_name.replace("_", " ").title()


def _build_subject(severity: str, client_name: str, signal_name: str) -> str:
    """
    Build the alert email subject line per spec Part 6 Module 4.

    Format: [SEVERITY] Operational Intelligence Alert -- [Client] -- [Signal Name]

    The spec uses em dashes in the template; per the AI prompt governance rule
    (Part 5: 'Do not use em dashes'), double hyphens are used as the separator
    to maintain consistency with the governed output style.

    Args:
        severity:    One of 'critical', 'elevated', 'watch'.
        client_name: Real client name (displayed in subject, not anonymized).
        signal_name: Canonical signal name from signal_readings.

    Returns:
        str: Formatted subject line.
    """
    display_signal = _format_signal_name(signal_name)
    return (
        f"[{severity.upper()}] Operational Intelligence Alert"
        f" -- {client_name}"
        f" -- {display_signal}"
    )


def _build_plain_body(
    client_name: str,
    signal_name: str,
    signal_value: float,
    threshold_value: float,
    severity: str,
    period_date: str,
    alert_message: str,
) -> str:
    """
    Build the plain-text email body in the Module 4 format.

    Sections in order:
      1. CP letterhead
      2. AI-generated 4-sentence alert message
      3. Signal data table
      4. Footer

    Args:
        client_name:     Real client name (used in letterhead).
        signal_name:     Canonical signal name.
        signal_value:    Measured signal value.
        threshold_value: Watch-level threshold that was breached.
        severity:        One of 'critical', 'elevated', 'watch'.
        period_date:     Reporting period date (YYYY-MM-DD).
        alert_message:   Drafted 4-sentence message body with CLIENT_A
                         already substituted for the real client name.

    Returns:
        str: Complete plain-text email body.
    """
    display_signal = _format_signal_name(signal_name)
    separator = "-" * 60

    letterhead = (
        "CRITERION PARTNERS\n"
        "Operational Intelligence Platform\n"
        f"Client: {client_name}\n"
        f"Reporting Period: {period_date}\n"
        f"Alert Severity: {severity.upper()}\n"
        f"{separator}\n"
    )

    signal_table = (
        f"\n{separator}\n"
        "SIGNAL INTELLIGENCE\n"
        f"{separator}\n"
        f"{'Signal':<40} {'Value':>12} {'Threshold':>12} {'Severity':>10}\n"
        f"{'-'*40} {'-'*12} {'-'*12} {'-'*10}\n"
        f"{display_signal:<40} {signal_value:>12.4f} {threshold_value:>12.4f} "
        f"{severity.upper():>10}\n"
    )

    footer = (
        f"\n{separator}\n"
        "This alert was generated by the CPOI Platform.\n"
        "Full analysis will be included in your next Monthly Intelligence Brief.\n"
        f"{separator}\n"
        "Criterion Partners | intelligence@criterion-partners.com\n"
    )

    return f"{letterhead}\n{alert_message}\n{signal_table}{footer}"


def _build_html_body(
    client_name: str,
    signal_name: str,
    signal_value: float,
    threshold_value: float,
    severity: str,
    period_date: str,
    alert_message: str,
) -> str:
    """
    Build the HTML email body in the Module 4 format.

    Mirrors the plain-text structure with basic styling for readability.
    No external CSS dependencies. Inline styles only.

    Args:
        client_name:     Real client name.
        signal_name:     Canonical signal name.
        signal_value:    Measured signal value.
        threshold_value: Watch-level threshold that was breached.
        severity:        One of 'critical', 'elevated', 'watch'.
        period_date:     Reporting period date.
        alert_message:   Drafted 4-sentence message with real client name.

    Returns:
        str: Complete HTML email body.
    """
    severity_colors = {
        "critical": "#8B0000",
        "elevated": "#B8860B",
        "watch":    "#4682B4",
    }
    severity_color = severity_colors.get(severity.lower(), "#333333")
    display_signal = _format_signal_name(signal_name)

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family: Georgia, serif; color: #222; max-width: 680px; margin: 0 auto; padding: 24px;">

  <table width="100%" cellpadding="0" cellspacing="0" style="border-bottom: 2px solid #1a1a1a; margin-bottom: 20px;">
    <tr>
      <td>
        <p style="margin:0; font-size:18px; font-weight:bold; letter-spacing:1px;">CRITERION PARTNERS</p>
        <p style="margin:2px 0 0; font-size:12px; color:#555;">Operational Intelligence Platform</p>
      </td>
      <td align="right">
        <span style="background:{severity_color}; color:#fff; padding:4px 10px; font-size:12px;
                      font-weight:bold; letter-spacing:1px;">{severity.upper()}</span>
      </td>
    </tr>
  </table>

  <table width="100%" cellpadding="4" cellspacing="0" style="font-size:13px; margin-bottom:20px;">
    <tr><td style="color:#555; width:140px;">Client</td><td><strong>{client_name}</strong></td></tr>
    <tr><td style="color:#555;">Reporting Period</td><td>{period_date}</td></tr>
    <tr><td style="color:#555;">Alert Severity</td>
        <td><strong style="color:{severity_color};">{severity.upper()}</strong></td></tr>
  </table>

  <hr style="border:none; border-top:1px solid #ddd; margin: 16px 0;">

  <p style="font-size:14px; line-height:1.7; margin-bottom:20px;">{alert_message}</p>

  <hr style="border:none; border-top:1px solid #ddd; margin: 16px 0;">

  <p style="font-size:12px; font-weight:bold; letter-spacing:1px; color:#555; margin-bottom:8px;">
    SIGNAL INTELLIGENCE
  </p>

  <table width="100%" cellpadding="8" cellspacing="0"
         style="font-size:13px; border-collapse:collapse;">
    <thead>
      <tr style="background:#f5f5f5;">
        <th align="left"  style="border:1px solid #ddd;">Signal</th>
        <th align="right" style="border:1px solid #ddd;">Value</th>
        <th align="right" style="border:1px solid #ddd;">Threshold</th>
        <th align="center" style="border:1px solid #ddd;">Severity</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td style="border:1px solid #ddd;">{display_signal}</td>
        <td align="right" style="border:1px solid #ddd;">{signal_value:.4f}</td>
        <td align="right" style="border:1px solid #ddd;">{threshold_value:.4f}</td>
        <td align="center" style="border:1px solid #ddd; color:{severity_color};
                                   font-weight:bold;">{severity.upper()}</td>
      </tr>
    </tbody>
  </table>

  <hr style="border:none; border-top:1px solid #ddd; margin: 24px 0 12px;">

  <p style="font-size:12px; color:#555; line-height:1.6;">
    This alert was generated by the CPOI Platform.<br>
    Full analysis will be included in your next Monthly Intelligence Brief.
  </p>
  <p style="font-size:11px; color:#888;">
    Criterion Partners &nbsp;|&nbsp; intelligence@criterion-partners.com
  </p>

</body>
</html>"""


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def send_alert_email(
    conn: Any,
    alert_id: str,
    recipient_email: str,
    client_name: str,
    drafted_message: str,
) -> bool:
    """
    Send the formatted alert email via SendGrid for a single alert record.

    Fetches the alert record, applies the idempotency guard, builds both
    plain-text and HTML email parts, sends via SendGrid, updates the alert
    record on success, and writes an audit log entry.

    This function never calls conn.commit(). The caller owns the transaction.

    Args:
        conn:             Open, authenticated database connection.
        alert_id:         UUID of the alert record to send.
        recipient_email:  Destination email address for this alert.
        client_name:      Real client name. Used in the email subject,
                          letterhead, and as the CLIENT_A substitution
                          in the drafted message body.
        drafted_message:  The 4-sentence alert message from narrator.py.
                          CLIENT_A is substituted with client_name before
                          building the email body.

    Returns:
        bool:
          True  -- email accepted by SendGrid (HTTP 202). Alert record
                   updated: email_sent=TRUE, email_sent_at set.
          False -- alert_id not found in database, or email already sent
                   (email_sent=TRUE). No send attempted.

    Raises:
        ValueError:   if SENDGRID_API_KEY is not set in the environment.
        RuntimeError: if SendGrid returns a non-202 HTTP response.
                      The alert record is NOT marked as sent. The caller
                      (alerts/queue.py) must handle this for retry.
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    if not api_key:
        raise ValueError(
            "SENDGRID_API_KEY environment variable is not set. "
            "Set it before calling send_alert_email()."
        )

    sender_email = os.environ.get("CPOI_SENDER_EMAIL", _DEFAULT_SENDER_EMAIL)

    # ------------------------------------------------------------------
    # Step 1: Fetch alert record.
    # ------------------------------------------------------------------
    row = conn.execute(
        """
        SELECT alert_id, client_id, signal_name, signal_value,
               threshold_value, alert_severity, email_sent,
               submission_id
        FROM alerts
        WHERE alert_id = ?
        """,
        (alert_id,),
    ).fetchone()

    if row is None:
        log.warning("Alert not found: alert_id=%s. No email sent.", alert_id)
        return False

    (
        _alert_id, client_id, signal_name, signal_value,
        threshold_value, severity, email_sent, submission_id,
    ) = row

    # ------------------------------------------------------------------
    # Step 2: Idempotency guard — do not send if already sent.
    # ------------------------------------------------------------------
    if email_sent:
        log.info(
            "Alert already sent: alert_id=%s. Skipping.", alert_id
        )
        return False

    # ------------------------------------------------------------------
    # Step 3: Resolve period_date for the email letterhead.
    # ------------------------------------------------------------------
    period_row = conn.execute(
        "SELECT reporting_period_end FROM intake_submissions WHERE submission_id = ?",
        (submission_id,),
    ).fetchone()
    period_date = period_row[0] if period_row else "N/A"

    # ------------------------------------------------------------------
    # Step 4: Substitute CLIENT_A with real client name in message body.
    # ------------------------------------------------------------------
    email_message_body = drafted_message.replace("CLIENT_A", client_name)

    # ------------------------------------------------------------------
    # Step 5: Build subject and both body parts.
    # ------------------------------------------------------------------
    subject = _build_subject(
        severity=severity,
        client_name=client_name,
        signal_name=signal_name,
    )

    plain_body = _build_plain_body(
        client_name=client_name,
        signal_name=signal_name,
        signal_value=float(signal_value),
        threshold_value=float(threshold_value),
        severity=severity,
        period_date=period_date,
        alert_message=email_message_body,
    )

    html_body = _build_html_body(
        client_name=client_name,
        signal_name=signal_name,
        signal_value=float(signal_value),
        threshold_value=float(threshold_value),
        severity=severity,
        period_date=period_date,
        alert_message=email_message_body,
    )

    # ------------------------------------------------------------------
    # Step 6: Build and send the SendGrid message.
    # ------------------------------------------------------------------
    message = Mail(
        from_email=Email(sender_email, "Criterion Partners Intelligence"),
        to_emails=To(recipient_email),
        subject=subject,
    )
    message.add_content(Content(MimeType.text, plain_body))
    message.add_content(Content(MimeType.html, html_body))

    log.info(
        "Sending alert email: alert_id=%s recipient=%s subject='%s'",
        alert_id,
        recipient_email,
        subject,
    )

    sg = SendGridAPIClient(api_key)
    response = sg.send(message)

    if response.status_code != 202:
        log.error(
            "SendGrid delivery failed: alert_id=%s status=%d body=%s",
            alert_id,
            response.status_code,
            response.body,
        )
        raise RuntimeError(
            f"SendGrid returned HTTP {response.status_code} for alert_id={alert_id}. "
            f"Body: {response.body}"
        )

    # ------------------------------------------------------------------
    # Step 7: Mark alert as sent in the database.
    # ------------------------------------------------------------------
    sent_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE alerts
        SET email_sent = TRUE, email_sent_at = ?
        WHERE alert_id = ?
        """,
        (sent_at, alert_id),
    )

    # ------------------------------------------------------------------
    # Step 8: Write audit log entry.
    # ------------------------------------------------------------------
    write_audit_log(
        conn=conn,
        event_type="alert_sent",
        entity_type="alert",
        entity_id=alert_id,
        description=(
            f"Alert email sent for signal '{signal_name}' "
            f"(severity: {severity}) to {recipient_email}. "
            f"SendGrid status: {response.status_code}."
        ),
        performed_by="system",
        metadata={
            "alert_id":         alert_id,
            "client_id":        client_id,
            "submission_id":    submission_id,
            "signal_name":      signal_name,
            "signal_value":     float(signal_value),
            "threshold_value":  float(threshold_value),
            "severity":         severity,
            "recipient_email":  recipient_email,
            "sender_email":     sender_email,
            "sendgrid_status":  response.status_code,
            "email_sent_at":    sent_at,
        },
    )

    log.info(
        "Alert email sent successfully: alert_id=%s status=%d sent_at=%s",
        alert_id,
        response.status_code,
        sent_at,
    )

    return True
