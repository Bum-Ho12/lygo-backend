

def test_smtp_errors_explain_themselves() -> None:
    """Regression: a missing login surfaced only as 'SMTP error 530', which didn't say what to fix."""
    from app.integrations.email.smtp import _explain

    assert "SMTP_USERNAME and SMTP_PASSWORD" in _explain(530)
    assert "SMTP key" in _explain(535) and "EMAIL_FROM" in _explain(550)
    assert _explain(599)  # unknown codes still get a pointer


def test_email_startup_summary_never_shows_the_password(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from app.core.config import settings
    from app.integrations.email.smtp import describe

    monkeypatch.setattr(settings, "smtp_host", "smtp-relay.brevo.com")
    monkeypatch.setattr(settings, "smtp_username", "user@smtp-brevo.com")
    monkeypatch.setattr(settings, "smtp_password", "xsmtpsib-secret-value")
    assert "login set" in describe() and "secret" not in describe()
    monkeypatch.setattr(settings, "smtp_password", "")
    assert "NO PASSWORD" in describe()
