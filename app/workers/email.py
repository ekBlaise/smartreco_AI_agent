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
