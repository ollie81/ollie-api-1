# ============================================================
# EMAIL SERVICE — SendGrid HTTP API (transactional email)
# ============================================================
# Sends the one-time verification codes for email/password signup
# and password reset (see auth.py's /auth/email/* routes). Unlike
# phone signup, which hands OTP state entirely to Twilio Verify,
# there's no "verify" product here -- this app generates its own
# 6-digit code, sends it via SendGrid, and verifies it itself (see
# auth.py's _hash_otp / email OTP columns from migration 014).
#
# Uses the `requests` library already used elsewhere in this
# codebase (see notification_service.py) -- no new dependency.
#
# SendGrid over other providers specifically because of Single
# Sender Verification: free forever (100 emails/day), and the
# sender can be a single email address you already own (verified by
# clicking a link SendGrid emails you), not a domain you have to buy
# and configure DNS for. See https://app.sendgrid.com/settings/sender_auth
# -- "Verify a Single Sender", not the domain-authentication flow.
#
# Requires two Railway variables:
#   SENDGRID_API_KEY   -- Settings -> API Keys -> Create API Key
#   SENDGRID_FROM_EMAIL -- the exact address you verified above
#                          (e.g. "Ollie <you@gmail.com>" or just "you@gmail.com")
# If either is missing, sends are skipped and logged rather than
# crashing the request -- callers treat a failed send as a 500
# ("Could not send verification code"), same as a Twilio failure
# would for phone.

import logging
import os
import re

import requests

logger = logging.getLogger("ollie.email")

SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
SENDGRID_FROM_EMAIL = os.environ.get("SENDGRID_FROM_EMAIL")
SENDGRID_API_URL = "https://api.sendgrid.com/v3/mail/send"

_NAME_ADDR_RE = re.compile(r"^\s*(?P<name>[^<]+)<(?P<email>[^>]+)>\s*$")


def _parse_from_address(raw: str) -> dict:
    """
    SendGrid wants {"email": ..., "name": ...} separately, not a
    combined "Name <email>" string the way Resend accepted it --
    parse that same format here so the env var can be set either
    way ("Ollie <you@gmail.com>" or plain "you@gmail.com").
    """
    match = _NAME_ADDR_RE.match(raw)
    if match:
        return {"email": match.group("email").strip(), "name": match.group("name").strip()}
    return {"email": raw.strip()}


def send_otp_email(to_email: str, code: str, purpose: str = "verify") -> bool:
    """
    Sends a 6-digit code by email. purpose is "verify" (signup) or
    "reset" (forgot password) -- only changes the copy. Returns
    whether the send actually succeeded; never raises, so a flaky
    provider can't turn into an unhandled 500 deep in a route.
    """
    if not SENDGRID_API_KEY or not SENDGRID_FROM_EMAIL:
        logger.error("SENDGRID_API_KEY / SENDGRID_FROM_EMAIL not set -- cannot send verification email")
        return False

    if purpose == "reset":
        subject = "Reset your Ollie password"
        heading = "Reset your password"
        body_line = "Use this code to reset your Ollie password:"
    else:
        subject = "Verify your email for Ollie"
        heading = "Verify your email"
        body_line = "Use this code to finish creating your Ollie account:"

    html = f"""
    <div style="font-family: -apple-system, sans-serif; max-width: 420px; margin: 0 auto;">
      <h2 style="color: #1A1035;">{heading}</h2>
      <p style="color: #333; font-size: 15px;">{body_line}</p>
      <p style="font-size: 32px; font-weight: 700; letter-spacing: 6px; color: #E86B4A;">{code}</p>
      <p style="color: #888; font-size: 13px;">This code expires in 10 minutes. If you didn't request this, you can ignore this email.</p>
    </div>
    """

    try:
        response = requests.post(
            SENDGRID_API_URL,
            headers={
                "Authorization": f"Bearer {SENDGRID_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "personalizations": [{"to": [{"email": to_email}]}],
                "from": _parse_from_address(SENDGRID_FROM_EMAIL),
                "subject": subject,
                "content": [{"type": "text/html", "value": html}],
            },
            timeout=10,
        )
        # SendGrid returns 202 Accepted on success, not 200.
        if response.status_code >= 400:
            logger.error(f"send_otp_email: SendGrid returned {response.status_code}: {response.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"send_otp_email: request failed: {e}")
        return False
