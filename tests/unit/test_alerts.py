"""Tests for feather.alerts module."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock, patch


@dataclass
class FakeAlertsConfig:
    smtp_host: str = "smtp.example.com"
    smtp_port: int = 587
    smtp_user: str = "user@example.com"
    smtp_password: str = "secret"
    alert_to: str = "ops@example.com"
    alert_from: str | None = None


class TestSendAlert:
    def test_noop_when_config_is_none(self):
        from feather_flow.alerts import send_alert

        # Should not raise or attempt SMTP
        send_alert("CRITICAL", "orders", "Pipeline failed", config=None)

    @patch("feather_flow.alerts.smtplib.SMTP")
    def test_sends_email_with_critical_subject(self, mock_smtp_cls):
        from feather_flow.alerts import send_alert

        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        cfg = FakeAlertsConfig()
        send_alert("CRITICAL", "orders", "Pipeline failed", config=cfg)

        mock_smtp.sendmail.assert_called_once()
        call_args = mock_smtp.sendmail.call_args
        msg_str = call_args[0][2]  # from, to, message
        assert "[CRITICAL]" in msg_str
        assert "orders" in msg_str

    @patch("feather_flow.alerts.smtplib.SMTP")
    def test_sends_email_with_warning_subject(self, mock_smtp_cls):
        from feather_flow.alerts import send_alert

        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        cfg = FakeAlertsConfig()
        send_alert("WARNING", "sales", "DQ check failed", config=cfg)

        msg_str = mock_smtp.sendmail.call_args[0][2]
        assert "[WARNING]" in msg_str
        assert "sales" in msg_str

    @patch("feather_flow.alerts.smtplib.SMTP")
    def test_alert_from_defaults_to_smtp_user(self, mock_smtp_cls):
        from feather_flow.alerts import send_alert

        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        cfg = FakeAlertsConfig(alert_from=None)
        send_alert("INFO", "test", "test msg", config=cfg)

        from_addr = mock_smtp.sendmail.call_args[0][0]
        assert from_addr == "user@example.com"

    @patch("feather_flow.alerts.smtplib.SMTP")
    def test_alert_from_uses_explicit_value(self, mock_smtp_cls):
        from feather_flow.alerts import send_alert

        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        cfg = FakeAlertsConfig(alert_from="noreply@example.com")
        send_alert("INFO", "test", "test msg", config=cfg)

        from_addr = mock_smtp.sendmail.call_args[0][0]
        assert from_addr == "noreply@example.com"

    @patch("feather_flow.alerts.smtplib.SMTP")
    def test_smtp_error_caught_and_logged(self, mock_smtp_cls, caplog):
        import smtplib

        from feather_flow.alerts import send_alert

        mock_smtp_cls.side_effect = smtplib.SMTPException("Connection refused")

        cfg = FakeAlertsConfig()
        # Should not raise
        send_alert("CRITICAL", "orders", "Pipeline failed", config=cfg)

        assert "Failed to send alert" in caplog.text

    @patch("feather_flow.alerts.smtplib.SMTP")
    def test_non_smtp_exception_caught_and_logged(self, mock_smtp_cls, caplog):
        """Non-SMTPException raised inside the SMTP block is caught by the
        generic ``except Exception`` guard so send_alert never raises."""
        from feather_flow.alerts import send_alert

        mock_smtp_cls.side_effect = RuntimeError("socket exploded")

        cfg = FakeAlertsConfig()
        # Should not raise — generic exception handler catches it
        send_alert("CRITICAL", "orders", "Pipeline failed", config=cfg)

        assert "Unexpected error sending alert" in caplog.text

    @patch("feather_flow.alerts.smtplib.SMTP")
    def test_starttls_called(self, mock_smtp_cls):
        from feather_flow.alerts import send_alert

        mock_smtp = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_smtp)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        cfg = FakeAlertsConfig()
        send_alert("CRITICAL", "t", "msg", config=cfg)

        mock_smtp.starttls.assert_called_once()
        mock_smtp.login.assert_called_once_with("user@example.com", "secret")


class TestAlertHooks:
    @patch("feather_flow.alerts.send_alert")
    def test_alert_on_failure_sends_critical(self, mock_send):
        from feather_flow.alerts import alert_on_failure

        cfg = FakeAlertsConfig()
        alert_on_failure("orders", "Connection timeout", config=cfg)

        mock_send.assert_called_once()
        args = mock_send.call_args
        assert args[0][0] == "CRITICAL"  # severity
        assert "orders" in args[0][1]  # table_name
        assert "Connection timeout" in args[0][2]  # message

    @patch("feather_flow.alerts.send_alert")
    def test_alert_on_failure_noop_when_no_config(self, mock_send):
        from feather_flow.alerts import alert_on_failure

        alert_on_failure("orders", "error", config=None)
        mock_send.assert_not_called()

    @patch("feather_flow.alerts.send_alert")
    def test_alert_on_dq_failure_sends_warning(self, mock_send):
        from feather_flow.alerts import alert_on_dq_failure

        cfg = FakeAlertsConfig()
        alert_on_dq_failure("sales", "not_null check failed on amount", config=cfg)

        mock_send.assert_called_once()
        assert mock_send.call_args[0][0] == "WARNING"

    @patch("feather_flow.alerts.send_alert")
    def test_alert_on_schema_drift_default_info(self, mock_send):
        from feather_flow.alerts import alert_on_schema_drift

        cfg = FakeAlertsConfig()
        alert_on_schema_drift("items", "column added: discount", config=cfg)

        mock_send.assert_called_once()
        assert mock_send.call_args[0][0] == "INFO"

    @patch("feather_flow.alerts.send_alert")
    def test_alert_on_schema_drift_critical_for_type_change(self, mock_send):
        from feather_flow.alerts import alert_on_schema_drift

        cfg = FakeAlertsConfig()
        alert_on_schema_drift(
            "items", "type changed: price INT→VARCHAR", severity="CRITICAL", config=cfg
        )

        mock_send.assert_called_once()
        assert mock_send.call_args[0][0] == "CRITICAL"
