from src.utils.config_validation import validate_runtime_env


def test_validate_runtime_env_bot_requires_telegram_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    report = validate_runtime_env("bot", load_env_file=False)
    assert not report.ok
    assert any("TELEGRAM_BOT_TOKEN" in err for err in report.errors)


def test_validate_runtime_env_web_auth_requires_supabase(monkeypatch):
    monkeypatch.setenv("POLYWEATHER_AUTH_ENABLED", "true")
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)

    report = validate_runtime_env("web", load_env_file=False)

    assert not report.ok
    assert any("SUPABASE_URL" in err for err in report.errors)


def test_validate_runtime_env_web_optional_auth_does_not_require_service_role(monkeypatch):
    monkeypatch.setenv("POLYWEATHER_AUTH_ENABLED", "true")
    monkeypatch.setenv("POLYWEATHER_AUTH_REQUIRED", "false")
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key")
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    report = validate_runtime_env("web", load_env_file=False)

    assert report.ok
    assert not any("SUPABASE_SERVICE_ROLE_KEY" in err for err in report.errors)
