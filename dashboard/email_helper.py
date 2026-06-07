"""
dashboard/email_helper.py
CPOI Platform -- Transactional email for client onboarding and password reset.

Reuses the same SendGrid configuration as alerts/sender.py:
  SENDGRID_API_KEY   -- required
  CPOI_SENDER_EMAIL  -- verified sender (default intelligence@criterion-partners.com)

These are portal/account emails (invites, reset codes), not alert emails, so
they live here rather than in alerts/. All bodies carry Criterion Partners
branding. No client operational data is included in these messages.
"""

from __future__ import annotations

import os

_DEFAULT_SENDER = "intelligence@criterion-partners.com"

NAVY = "#2E4A6A"
GOLD = "#C9A455"


def email_configured() -> bool:
    """True if SendGrid is configured so the UI can warn before relying on it."""
    return bool(os.environ.get("SENDGRID_API_KEY"))


def _shell(title: str, body_html: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"></head>
<body style="font-family:Georgia,serif;color:#1F2A37;max-width:620px;margin:0 auto;padding:28px;">
  <div style="border-bottom:3px solid {GOLD};padding-bottom:14px;margin-bottom:22px;">
    <span style="display:inline-block;background:{NAVY};color:#fff;border:2px solid {GOLD};
                 padding:8px 10px;border-radius:4px;font-weight:700;letter-spacing:1px;">CP</span>
    <span style="font-size:18px;font-weight:700;color:{NAVY};letter-spacing:3px;
                 margin-left:10px;vertical-align:middle;">CRITERION PARTNERS</span>
  </div>
  <h2 style="color:{NAVY};font-size:22px;margin:0 0 14px;">{title}</h2>
  {body_html}
  <hr style="border:none;border-top:1px solid #e5e5e5;margin:26px 0 12px;">
  <p style="font-size:11px;color:#888;">Criterion Partners &nbsp;|&nbsp; Operational Executive Intelligence<br>
     This message was sent from a secure, no-reply platform address.</p>
</body></html>"""


def send_email(to_email: str, subject: str, html_body: str, plain_body: str) -> None:
    """
    Send one transactional email via SendGrid.

    Raises:
        ValueError:   if SENDGRID_API_KEY is not set.
        RuntimeError: if SendGrid returns a non-2xx response.
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    if not api_key:
        raise ValueError(
            "SENDGRID_API_KEY is not set on the server. "
            "Set it before sending portal emails (invites, password resets)."
        )
    sender = os.environ.get("CPOI_SENDER_EMAIL", _DEFAULT_SENDER)

    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail, Email, To, Content, MimeType

    message = Mail(
        from_email=Email(sender, "Criterion Partners"),
        to_emails=To(to_email),
        subject=subject,
    )
    message.add_content(Content(MimeType.text, plain_body))
    message.add_content(Content(MimeType.html, html_body))

    response = SendGridAPIClient(api_key).send(message)
    if response.status_code not in (200, 201, 202):
        raise RuntimeError(
            f"SendGrid returned HTTP {response.status_code} when emailing {to_email}."
        )


def send_client_invite(to_email: str, client_name: str, portal_url: str,
                       temp_password: str) -> None:
    """Email a new client their portal credentials (temporary password)."""
    title = "Your Criterion Partners portal is ready"
    body = f"""
      <p style="font-size:14px;line-height:1.7;">Welcome. A secure portal has been created for
      <strong>{client_name}</strong> on the Criterion Partners Operational Executive
      Intelligence platform.</p>
      <table style="font-size:14px;margin:16px 0;">
        <tr><td style="color:#666;padding:4px 12px 4px 0;">Portal</td>
            <td><a href="{portal_url}" style="color:{NAVY};">{portal_url}</a></td></tr>
        <tr><td style="color:#666;padding:4px 12px 4px 0;">Username</td>
            <td><strong>{to_email}</strong></td></tr>
        <tr><td style="color:#666;padding:4px 12px 4px 0;">Temporary password</td>
            <td><strong style="color:{NAVY};">{temp_password}</strong></td></tr>
      </table>
      <p style="font-size:14px;line-height:1.7;">For your security you will be asked to set your
      own password the first time you sign in.</p>
    """
    plain = (
        f"Welcome to the Criterion Partners portal for {client_name}.\n\n"
        f"Portal: {portal_url}\nUsername: {to_email}\n"
        f"Temporary password: {temp_password}\n\n"
        "You will be asked to set your own password on first sign in."
    )
    send_email(to_email, "Your Criterion Partners portal access", _shell(title, body), plain)


def mp_notify_email() -> str:
    """The Managing Partner notification address."""
    return os.environ.get("CPOI_MP_NOTIFY_EMAIL", "intelligence@criterion-partners.com")


def send_submission_notification(client_name: str, uploaded_by: str, filename: str) -> None:
    """Notify the Managing Partner that a client submitted a workbook for review."""
    recipient = mp_notify_email()
    title = "New client submission awaiting review"
    body = f"""
      <p style="font-size:14px;line-height:1.7;">A client has submitted a data workbook for
      review on the CPOI platform.</p>
      <table style="font-size:14px;margin:16px 0;">
        <tr><td style="color:#666;padding:4px 12px 4px 0;">Client</td><td><strong>{client_name}</strong></td></tr>
        <tr><td style="color:#666;padding:4px 12px 4px 0;">Submitted by</td><td>{uploaded_by}</td></tr>
        <tr><td style="color:#666;padding:4px 12px 4px 0;">File</td><td>{filename}</td></tr>
      </table>
      <p style="font-size:14px;line-height:1.7;">Open <strong>Data Intake</strong> in the platform to
      review and approve or reject this submission. Nothing is processed until you approve it.</p>
    """
    plain = (
        f"New client submission awaiting review.\n\n"
        f"Client: {client_name}\nSubmitted by: {uploaded_by}\nFile: {filename}\n\n"
        "Open Data Intake to review and approve or reject. Nothing is processed until you approve."
    )
    send_email(recipient, "[CPOI] New client submission awaiting review", _shell(title, body), plain)


def send_mp_reset_code(code: str) -> None:
    """Email a Managing Partner password-reset code to the notification address."""
    recipient = mp_notify_email()
    title = "Managing Partner password reset code"
    body = f"""
      <p style="font-size:14px;line-height:1.7;">A request was made to reset the Managing Partner
      password for the CPOI platform. Enter the code below on the reset screen. It expires in
      60 minutes.</p>
      <div style="font-size:30px;font-weight:700;letter-spacing:8px;color:{NAVY};
                  background:#F5F2EA;border:1px solid {GOLD};border-radius:8px;
                  text-align:center;padding:16px;margin:18px 0;">{code}</div>
      <p style="font-size:13px;color:#666;">If you did not request this, ignore this email; the
      password will remain unchanged.</p>
    """
    plain = (
        f"Your CPOI Managing Partner password reset code is: {code}\n\n"
        "This code expires in 60 minutes. If you did not request it, ignore this email."
    )
    send_email(recipient, "[CPOI] Managing Partner password reset code", _shell(title, body), plain)


def send_reset_code(to_email: str, code: str) -> None:
    """Email a password-reset code to a client."""
    title = "Your password reset code"
    body = f"""
      <p style="font-size:14px;line-height:1.7;">We received a request to reset the password for
      your Criterion Partners portal account. Enter the code below on the reset screen. It expires
      in 60 minutes.</p>
      <div style="font-size:30px;font-weight:700;letter-spacing:8px;color:{NAVY};
                  background:#F5F2EA;border:1px solid {GOLD};border-radius:8px;
                  text-align:center;padding:16px;margin:18px 0;">{code}</div>
      <p style="font-size:13px;color:#666;">If you did not request this, you can ignore this email
      and your password will remain unchanged.</p>
    """
    plain = (
        "Your Criterion Partners password reset code is: "
        f"{code}\n\nThis code expires in 60 minutes. "
        "If you did not request it, ignore this email."
    )
    send_email(to_email, "Criterion Partners password reset code", _shell(title, body), plain)
