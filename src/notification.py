"""SMTP delivery over TLS, with no credentials included in application logs."""

import smtplib
import ssl
from email.message import EmailMessage

from src.config import Settings
from src.models import Profile


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
