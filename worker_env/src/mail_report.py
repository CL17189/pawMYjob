"""Optional SMTP daily summary. No SMTP configuration means no outbound email."""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage
from typing import Any


def send_daily_report(stats: dict[str, Any]) -> bool:
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("REPORT_FROM_EMAIL")
    recipient = os.getenv("REPORT_TO_EMAIL")
    if not all((host, sender, recipient)):
        return False
    msg = EmailMessage()
    msg["Subject"] = f"pawMYjob daily report · {stats.get('date', '')}"
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(
        "pawMYjob daily job report\n\n"
        f"Scraped: {stats.get('total_scraped', 0)}\n"
        f"New or changed: {stats.get('delta_jobs', 0)}\n"
        f"Eligible after citizenship/security filter: {stats.get('eligible_jobs', 0)}\n"
        f"Scored: {stats.get('scored_jobs', 0)}\n"
        f"Applications generated: {stats.get('generated_jobs', 0)}\n"
    )
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    use_ssl = os.getenv("SMTP_SSL", "false").lower() in {"1", "true", "yes", "on"}
    if use_ssl:
        server = smtplib.SMTP_SSL(host, port, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
    with server:
        if not use_ssl:
            server.starttls()
        if username and password:
            server.login(username, password)
        server.send_message(msg)
    return True

