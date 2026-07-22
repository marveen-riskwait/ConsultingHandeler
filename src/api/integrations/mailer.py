"""Outbound email — deliberately content-free.

Two rules shape everything here.

**Nothing about the file travels by email.** Not the document that was
returned, not why, not where the review stands. Email is unencrypted, lands in
shared inboxes and on phones over other people's networks, and in an AML
context a message that hints at an assessment can amount to unlawful
disclosure. So a notification says only that something is waiting and where to
sign in. The detail lives behind the login, which is the point of having a
portal at all.

**A missing mail server never breaks a workflow.** Returning a document is a
compliance action; sending the courtesy note about it is not. Without SMTP
configured, `send()` records that it could not deliver and returns — the caller
carries on. Same posture as the media layer with a broken Cloudinary.

Configure with SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
MAIL_FROM and PORTAL_URL. Set MAIL_SUPPRESS=1 to keep it quiet in development.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage


def is_configured():
    return bool(os.getenv("SMTP_HOST") and os.getenv("MAIL_FROM"))


def portal_url():
    return os.getenv("PORTAL_URL", "").rstrip("/") or None


def send(to, subject, body):
    """Best effort. Returns {sent, reason} and never raises."""
    if not to:
        return {"sent": False, "reason": "no recipient"}
    if os.getenv("MAIL_SUPPRESS") == "1":
        return {"sent": False, "reason": "suppressed"}
    if not is_configured():
        return {"sent": False, "reason": "SMTP not configured"}

    message = EmailMessage()
    message["From"] = os.getenv("MAIL_FROM")
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user, password = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if user and password:
                smtp.login(user, password)
            smtp.send_message(message)
        return {"sent": True}
    except Exception as exc:
        return {"sent": False, "reason": f"{type(exc).__name__}: {exc}"}


def _sign_in_line():
    url = portal_url()
    return (f"Please sign in to your secure portal: {url}" if url
            else "Please sign in to your secure portal to see what is needed.")


def notify_action_needed(user, organization_name, what="something"):
    """Tell a customer that the firm is waiting on them — and nothing else.

    `what` is a neutral category ("a document", "some information"), never the
    document, never the reason.
    """
    name = (user.full_name or "").split(" ")[0] or "there"
    firm = organization_name or "your compliance team"
    return send(
        user.email,
        f"{firm}: we need {what} from you",
        f"Hello {name},\n\n"
        f"{firm} is waiting on {what} to continue your onboarding.\n\n"
        f"{_sign_in_line()}\n\n"
        "This message is automated — please do not reply to it. Use the "
        "messages section of the portal to reach the team.\n")
