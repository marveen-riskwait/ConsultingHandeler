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
compliance action; sending the courtesy note about it is not. With no
transport configured — or with a broken one — `send()` records why it could
not deliver and returns; the caller carries on. Same posture as the media
layer with a broken Cloudinary.

Two transports, tried in that order:

1. **Brevo HTTP API** when BREVO_API_KEY is set. Preferred, because PaaS hosts
   (Render, Fly, many container platforms) block outbound port 587 — an SMTP
   relay that works on a laptop and silently fails in production is a trap.
2. **Plain SMTP** when SMTP_HOST is set. Brevo also works this way
   (smtp-relay.brevo.com:587) as does any other relay.

Configure with BREVO_API_KEY, or SMTP_HOST / SMTP_PORT (default 587) /
SMTP_USER / SMTP_PASSWORD. Both need MAIL_FROM, and want PORTAL_URL so the
message can link where to sign in. MAIL_SUPPRESS=1 keeps it quiet in
development.
"""
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


def is_configured():
    return bool(os.getenv("MAIL_FROM")
                and (os.getenv("BREVO_API_KEY") or os.getenv("SMTP_HOST")))


def transport():
    if os.getenv("BREVO_API_KEY"):
        return "brevo"
    return "smtp" if os.getenv("SMTP_HOST") else None


def _split_from(value):
    """'Acme Compliance <no-reply@acme.io>' -> ('Acme Compliance', 'no-reply@acme.io')."""
    match = re.match(r"^\s*(.*?)\s*<\s*([^>]+?)\s*>\s*$", value or "")
    if match:
        return (match.group(1).strip().strip('"') or None), match.group(2)
    return None, (value or "").strip()


def _send_brevo(to, subject, body, html=None, inline_png=None):
    import base64
    from api.integrations.ai.base import post_json
    name, email = _split_from(os.getenv("MAIL_FROM"))
    sender = {"email": email}
    if name:
        sender["name"] = name
    payload = {"sender": sender, "to": [{"email": to}],
               "subject": subject, "textContent": body}
    if html:
        payload["htmlContent"] = html
    if inline_png:
        # Brevo delivers this as a regular attachment; Gmail strips data: URI
        # images, so an attached PNG is the reliable way the QR arrives.
        payload["attachment"] = [{
            "name": inline_png[0],
            "content": base64.b64encode(inline_png[1]).decode(),
        }]
    result = post_json(BREVO_ENDPOINT, payload,
                       headers={"api-key": os.getenv("BREVO_API_KEY"),
                                "accept": "application/json"})
    return {"sent": True, "transport": "brevo",
            "id": (result or {}).get("messageId")}


def _send_smtp(to, subject, body, html=None, inline_png=None):
    message = EmailMessage()
    message["From"] = os.getenv("MAIL_FROM")
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    if html:
        if inline_png:
            # Referenced from the HTML as <img src="cid:NAME"> — the one way
            # every major client actually displays an embedded image.
            html = html.replace("cid:__QR__", f"cid:{inline_png[0]}")
        message.add_alternative(html, subtype="html")
        if inline_png:
            message.get_payload()[-1].add_related(
                inline_png[1], "image", "png", cid=f"<{inline_png[0]}>")

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user, password = os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD")
    with smtplib.SMTP(host, port, timeout=15) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        if user and password:
            smtp.login(user, password)
        smtp.send_message(message)
    return {"sent": True, "transport": "smtp"}


def portal_url():
    return os.getenv("PORTAL_URL", "").rstrip("/") or None


def send(to, subject, body, html=None, inline_png=None):
    """Best effort. Returns {sent, reason} and never raises.

    `inline_png` is (name, bytes): embedded by CID over SMTP, attached on
    Brevo. `html` may reference it as <img src="cid:__QR__">.
    """
    if not to:
        return {"sent": False, "reason": "no recipient"}
    if os.getenv("MAIL_SUPPRESS") == "1":
        return {"sent": False, "reason": "suppressed"}
    if not is_configured():
        return {"sent": False, "reason": "no mail transport configured "
                "(set BREVO_API_KEY or SMTP_HOST, plus MAIL_FROM)"}

    how = transport()
    try:
        return _send_brevo(to, subject, body, html, inline_png) if how == "brevo" \
            else _send_smtp(to, subject, body, html, inline_png)
    except Exception as exc:
        return {"sent": False, "transport": how,
                "reason": f"{type(exc).__name__}: {exc}"}


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


def notify_verify_email(user, organization_name, link):
    name = (user.full_name or "").split(" ")[0] or "there"
    firm = organization_name or "Compliance OS"
    return send(
        user.email, f"{firm}: confirm your email address",
        f"Hello {name},\n\nConfirm your email address to activate your "
        f"{firm} account:\n{link}\n\nThe link expires in 24 hours. If you did "
        "not create this account, you can ignore this message.\n")


def notify_password_reset(user, organization_name, link):
    name = (user.full_name or "").split(" ")[0] or "there"
    firm = organization_name or "Compliance OS"
    return send(
        user.email, f"{firm}: reset your password",
        f"Hello {name},\n\nWe received a request to reset your password. Use "
        f"this link within one hour:\n{link}\n\nIf you did not ask for this, "
        "ignore this message — your password stays unchanged.\n")


def send_login_otp(user, organization_name, code):
    firm = organization_name or "Compliance OS"
    return send(
        user.email, f"{firm}: your sign-in code",
        f"Your one-time sign-in code is: {code}\n\nIt expires in 10 minutes. "
        "If you were not signing in, someone has your password — change it.\n")
