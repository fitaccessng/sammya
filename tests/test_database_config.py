from app.factory import resolve_database_url


def test_postgres_url_selects_psycopg3_driver(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgres://user:pass@db.example/app')

    assert resolve_database_url('production') == (
        'postgresql+psycopg://user:pass@db.example/app'
    )


def test_postgresql_url_selects_psycopg3_driver(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@db.example/app')

    assert resolve_database_url('production') == (
        'postgresql+psycopg://user:pass@db.example/app'
    )


def test_explicit_postgres_driver_is_preserved(monkeypatch):
    url = 'postgresql+psycopg2://user:pass@db.example/app'
    monkeypatch.setenv('DATABASE_URL', url)

    assert resolve_database_url('production') == url