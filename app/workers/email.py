"""Digest email rendering and delivery.

When SMTP is not configured the digest is written to ``data/digests/*.html``
instead of being sent. That is a documented dry-run mode, not a stub: the same
HTML that would have been emailed is produced, so the feature is inspectable
without handing out mail credentials.
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import BASE_DIR, settings

logger = logging.getLogger(__name__)

_env = Environment(
    loader=FileSystemLoader(BASE_DIR / "app" / "templates"),
    autoescape=select_autoescape(["html"]),
)


def render_digest(user: Any, recommendation: dict[str, Any]) -> tuple[str, str]:
    """Return (subject, html)."""
    template = _env.get_template("email/digest.html")
    html = template.render(
        user=user,
        reco=recommendation,
        base_url=settings.public_base_url.rstrip("/"),
        generated_at=datetime.now(timezone.utc),
        app_name=settings.app_name,
    )
    subject = recommendation.get("headline") or "Picked for you from today's browsing"
    return subject[:150], html


def _plain_text(html: str) -> str:
    text = re.sub(r"<(script|style).*?</\1>", "", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _write_dry_run(to_email: str, subject: str, html: str) -> Path:
    directory = settings.digest_dir
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^a-z0-9]+", "-", to_email.lower()).strip("-")
    path = directory / f"{stamp}-{safe}.html"
    path.write_text(
        f"<!-- To: {to_email}\n     Subject: {subject} -->\n{html}", encoding="utf-8"
    )
    return path


def send_digest(to_email: str, subject: str, html: str) -> dict[str, Any]:
    """Send via SMTP, or write to disk when SMTP is unconfigured."""
    if not settings.smtp_configured:
        path = _write_dry_run(to_email, subject, html)
        logger.info("SMTP not configured — digest written to %s", path)
        return {"sent": False, "dry_run": True, "path": str(path)}

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = to_email
    message.set_content(_plain_text(html))
    message.add_alternative(html, subtype="html")

    try:
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, context=ssl.create_default_context(), timeout=30
            ) as server:
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
        else:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
                if settings.smtp_starttls:
                    server.starttls(context=ssl.create_default_context())
                if settings.smtp_user:
                    server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
    except Exception as exc:
        logger.exception("Failed to send digest to %s", to_email)
        return {"sent": False, "dry_run": False, "error": str(exc)}

    logger.info("Digest sent to %s", to_email)
    return {"sent": True, "dry_run": False}


def main() -> int:
    """Send one test email, so SMTP can be verified without waiting for Beat.

        python -m app.workers.email --to you@example.com

    Prints the resolved settings (never the password) and the exact SMTP error
    when it fails, because "digest not delivered" a day later is a miserable way
    to discover a typo in a port number.
    """
    import argparse

    parser = argparse.ArgumentParser(description="Send a SmartReco test email.")
    parser.add_argument("--to", required=True, help="recipient address")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    mode = "SSL" if settings.smtp_port == 465 else (
        "STARTTLS" if settings.smtp_starttls else "plaintext"
    )
    print(f"host     : {settings.smtp_host or '(unset — will write to disk instead)'}")
    print(f"port     : {settings.smtp_port} ({mode})")
    print(f"user     : {settings.smtp_user or '(none — sending unauthenticated)'}")
    print(f"password : {'set' if settings.smtp_password else 'NOT SET'}")
    print(f"from     : {settings.smtp_from}")
    print(f"to       : {args.to}\n")

    result = send_digest(
        args.to,
        "SmartReco SMTP test",
        "<html><body style='font-family:sans-serif'>"
        "<h2>SmartReco SMTP works</h2>"
        "<p>If you are reading this in your inbox, the daily digest will send.</p>"
        "</body></html>",
    )

    if result.get("sent"):
        print("Sent. Check the inbox (and the spam folder).")
        return 0
    if result.get("dry_run"):
        print(f"SMTP is not configured, so it was written to {result['path']}")
        print("Set SMTP_HOST in .env to send for real.")
        return 1
    print(f"FAILED: {result.get('error')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
