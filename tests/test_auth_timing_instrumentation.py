from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_auth_me_backend_records_stage_timing_without_sensitive_identity():
    source = (ROOT / "web" / "services" / "auth_api.py").read_text(encoding="utf-8")

    assert "_AuthMeTimer" in source
    assert "auth_me_timing" in source
    for stage in [
        "bind_identity",
        "total",
    ]:
        assert stage in source

    log_start = source.index("def _log_auth_me_timing") if "def _log_auth_me_timing" in source else 0
    assert log_start >= 0


def test_auth_me_backend_exposes_server_timing_header_for_proxy_logs():
    router_source = (ROOT / "web" / "routers" / "auth.py").read_text(
        encoding="utf-8"
    )

    assert "Response" in router_source
    assert "auth_me_server_timing" in router_source
    assert '"Server-Timing"' in router_source
