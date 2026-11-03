"""Render HTML reports and send them over authenticated TLS SMTP."""

import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from config import Profile, Result, Settings, Status


def render_report(profile: Profile, results: list[Result], timezone: str) -> str | None:
    selected = [
        result
        for result in results
        if profile.profile_id in result.target_profiles
        and (not profile.only_problems or result.status != Status.OK)
    ]
    if not selected:
        return None
    environment = Environment(
        loader=FileSystemLoader(str(Path(__file__).resolve().parent)),
        autoescape=select_autoescape(["html"]),
    )
    environment.filters["localtime"] = lambda dt: dt.astimezone(ZoneInfo(timezone)).strftime(
        "%Y-%m-%d %H:%M %Z"
    )
    return environment.get_template("daily_report.html").render(
        results=selected,
        profile=profile,
        generated_at=datetime.now(ZoneInfo(timezone)),
        problems=sum(result.status != Status.OK for result in selected),
    )


class EmailSender:
    def __init__(self, settings: Settings):
        self.settings = settings

    def send(self, profile: Profile, html: str, run_id: str) -> None:
        settings = self.settings
        if not settings.smtp_host or not settings.smtp_from:
            raise ValueError("SMTP_HOST and SMTP_FROM are required for notification delivery")
        message = EmailMessage()
        message["Subject"] = f"GCP monitoring report — {profile.profile_id}"
        message["From"] = settings.smtp_from
        message["To"] = ", ".join(profile.email_addresses)
        message["Message-ID"] = f"<{run_id}.{profile.profile_id}@gcp-metadata-monitor.local>"
        message.set_content("Your GCP monitoring report is attached as the HTML message body.")
        message.add_alternative(html, subtype="html")
        context = ssl.create_default_context()
        if settings.smtp_security == "ssl":
            connection = smtplib.SMTP_SSL(
                settings.smtp_host,
                settings.smtp_port,
                timeout=30,
                context=context,
            )
        else:
            connection = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        with connection:
            if settings.smtp_security == "starttls":
                connection.starttls(context=context)
            if settings.smtp_user:
                connection.login(settings.smtp_user, settings.smtp_pass.get_secret_value())
            refused = connection.send_message(message)
            if refused:
                raise RuntimeError("SMTP refused one or more recipients")
