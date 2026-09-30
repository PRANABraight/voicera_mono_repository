"""email_service tests: patch the Mailtrap client, never hit the network."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services import email_service


def test_send_password_reset_email_no_token_returns_false(monkeypatch):
    monkeypatch.setattr(email_service.settings, "MAILTRAP_API_TOKEN", "")
    with patch("app.services.email_service.MailtrapClient") as client_cls:
        result = email_service.send_password_reset_email(
            "u@example.com", "tok-1", "https://example.com/reset?token=tok-1"
        )
    assert result is False
    client_cls.assert_not_called()


def test_send_password_reset_email_success(monkeypatch):
    monkeypatch.setattr(email_service.settings, "MAILTRAP_API_TOKEN", "fake-token")
    with patch("app.services.email_service.MailtrapClient") as client_cls:
        client_cls.return_value.send = MagicMock()
        result = email_service.send_password_reset_email(
            "u@example.com", "tok-1", "https://example.com/reset?token=tok-1"
        )
    assert result is True
    client_cls.return_value.send.assert_called_once()


def test_send_password_reset_email_client_raises_returns_false(monkeypatch):
    monkeypatch.setattr(email_service.settings, "MAILTRAP_API_TOKEN", "fake-token")
    with patch("app.services.email_service.MailtrapClient") as client_cls:
        client_cls.return_value.send = MagicMock(side_effect=RuntimeError("network down"))
        result = email_service.send_password_reset_email(
            "u@example.com", "tok-1", "https://example.com/reset?token=tok-1"
        )
    assert result is False
