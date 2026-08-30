# ============================================================
# EMAIL SERVICE — Resend HTTP API (transactional email)
# ============================================================
# Sends the one-time verification codes for email/password signup
# and password reset (see auth.py's /auth/email/* routes). Unlike
# phone signup, which hands OTP state entirely to Twilio Verify,
# there's no "verify" product here -- this app generates its own
# 6-digit code, sends it via Resend, and verifies it itself (see
# auth.py's _hash_otp / email OTP columns from migration 014).
#
# Uses the `requests` library already used elsewhere in this
# codebase (see notification_service.py) -- no new dependency.
#
# Requires two Railway variables:
#   RESEND_API_KEY   -- from https://resend.com/api-keys
#   RESEND_FROM_EMAIL -- a sender address on a domain you've
#                        verified with Resend (e.g. "Ollie <noreply@yourdomain.com>").
#                        Resend rejects sends from an unverified domain.
# If either is missing, sends are skipped and logged rather than
# crashing the request -- callers treat a failed send as a 500
# ("Could not send verification code"), same as a Twilio failure
# would for phone.

import logging
import os

import requests

logger = logging.getLogger("ollie.email")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL")
RESEND_API_URL = "https://api.resend.com/emails"


def send_otp_email(to_email: str, code: str, purpose: str = "verify") -> bool:
    """
    Sends a 6-digit code by email. purpose is "verify" (signup) or
    "reset" (forgot password) -- only changes the copy. Returns
    whether the send actually succeeded; never raises, so a flaky
    provider can't turn into an unhandled 500 deep in a route.
    """
    if not RESEND_API_KEY or not RESEND_FROM_EMAIL:
        logger.error("RESEND_API_KEY / RESEND_FROM_EMAIL not set -- cannot send verification email")
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
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": RESEND_FROM_EMAIL,
                "to": [to_email],
                "subject": subject,
                "html": html,
            },
            timeout=10,
        )
        if response.status_code >= 400:
            logger.error(f"send_otp_email: Resend returned {response.status_code}: {response.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"send_otp_email: request failed: {e}")
        return False
