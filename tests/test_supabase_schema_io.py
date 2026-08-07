from pathlib import Path


def _schema_sql() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "scripts" / "supabase" / "schema.sql").read_text(encoding="utf-8").lower()


def test_supabase_schema_has_identity_indexes_for_hot_ops_queries():
    schema = _schema_sql()

    assert (
        "create index if not exists idx_profiles_email\n"
        "  on public.profiles(email)\n"
        "  include (id)"
    ) in schema
    assert (
        "create index if not exists idx_profiles_id_lookup\n"
        "  on public.profiles(id)\n"
        "  include (email, created_at)"
    ) in schema
    assert "sync_profile_from_auth" in schema
    assert "on_auth_user_created_polyweather" in schema
    assert "subscriptions" not in schema
    assert "payment_intents" not in schema
    assert "points_ledger" not in schema
    assert "user_wallets" not in schema
    assert "referral" not in schema
    assert "trial_claims" not in schema


def test_supabase_io_budget_scripts_are_production_runnable():
    root = Path(__file__).resolve().parents[1]
    indexes = (root / "scripts" / "supabase" / "io_budget_indexes.sql").read_text(encoding="utf-8").lower()
    diagnostics = (root / "scripts" / "supabase" / "disk_io_diagnostics.sql").read_text(encoding="utf-8").lower()

    assert "drop index if exists public.idx_profiles_email" in indexes
    assert (
        "create index if not exists idx_profiles_email\n"
        "  on public.profiles(email)\n"
        "  include (id)"
    ) in indexes
    assert "drop index if exists public.idx_profiles_id_lookup" in indexes
    assert (
        "create index if not exists idx_profiles_id_lookup\n"
        "  on public.profiles(id)\n"
        "  include (email, created_at)"
    ) in indexes
    assert "pg_stat_user_tables" in diagnostics
    assert "pg_stat_statements" in diagnostics
    assert "shared_blks_read" in diagnostics
    assert "pg_stat_user_indexes" in diagnostics
    assert "pg_statio_user_indexes" in diagnostics
    assert "indexrelname" in diagnostics
    assert "idx_blks_read" in diagnostics
    assert "idx_scan = 0" in diagnostics
