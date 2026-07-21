"""SMTP alerting for feather-flow pipeline events."""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_alert(
    severity: str,
    table_name: str,
    message: str,
    *,
    config: object | None = None,
) -> None:
    """Send an email alert via SMTP. No-op if config is None."""
    if config is None:
        return

    sender = getattr(config, "alert_from", None) or config.smtp_user
    subject = f"[{severity}] feather-flow: {message} - {table_name}"

    msg = MIMEText(f"Table: {table_name}\nSeverity: {severity}\n\n{message}")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = config.alert_to

    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
            server.starttls()
            server.login(config.smtp_user, config.smtp_password)
            server.sendmail(sender, config.alert_to, msg.as_string())
    except smtplib.SMTPException:
        logger.exception("Failed to send alert email for table %s", table_name)
    except Exception:
        logger.exception("Unexpected error sending alert for table %s", table_name)


def alert_on_failure(
    table_name: str,
    error_message: str,
    *,
    config: object | None = None,
) -> None:
    """Send a CRITICAL alert on pipeline failure."""
    if config is None:
        return
    send_alert(
        "CRITICAL", table_name, f"Pipeline failure: {error_message}", config=config
    )


def alert_on_dq_failure(
    table_name: str,
    check_details: str,
    *,
    config: object | None = None,
) -> None:
    """Send a WARNING alert on data quality check failure. Hook for V9."""
    if config is None:
        return
    send_alert("WARNING", table_name, f"DQ failure: {check_details}", config=config)


def alert_on_schema_drift(
    table_name: str,
    drift_details: str,
    *,
    severity: str = "INFO",
    config: object | None = None,
) -> None:
    """Send an alert on schema drift detection. Hook for V10.

    severity defaults to INFO; V10 passes CRITICAL for type_changed per FR12.6.
    """
    if config is None:
        return
    send_alert(severity, table_name, f"Schema drift: {drift_details}", config=config)
