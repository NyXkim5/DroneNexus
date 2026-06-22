"""
Tests for secrets management: JWT config via settings, password hashing
(bcrypt), and default-secret warning.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from config import DEFAULT_JWT_SECRET


# ---------------------------------------------------------------------------
# Password hashing (bcrypt)
# ---------------------------------------------------------------------------

def test_hash_password_returns_bcrypt_hash():
    from api.auth import _hash_password

    result = _hash_password("test-password")
    assert result.startswith("$2b$")
    assert len(result) == 60


def test_verify_password_accepts_correct():
    from api.auth import _hash_password, _verify_password

    stored = _hash_password("nexus-alpha")
    assert _verify_password("nexus-alpha", stored) is True


def test_verify_password_rejects_wrong():
    from api.auth import _hash_password, _verify_password

    stored = _hash_password("nexus-alpha")
    assert _verify_password("wrong-password", stored) is False


# ---------------------------------------------------------------------------
# Default secret warning
# ---------------------------------------------------------------------------

@pytest.fixture()
def _reload_auth_after():
    """Reload api.auth after tests that modify module-level state.

    Runs after monkeypatch teardown (LIFO order) so env vars are
    already restored before we reload the module.
    """
    yield
    import importlib
    import api.auth as auth_mod
    importlib.reload(auth_mod)


def test_default_secret_triggers_warning(_reload_auth_after, caplog, monkeypatch):
    """Importing auth with the default secret should log a warning."""
    monkeypatch.delenv("OVERWATCH_JWT_SECRET", raising=False)

    import importlib
    import api.auth as auth_mod

    with caplog.at_level(logging.WARNING, logger="overwatch.auth"):
        importlib.reload(auth_mod)

    assert any("default value" in r.message for r in caplog.records)


def test_custom_secret_no_warning(_reload_auth_after, caplog, monkeypatch):
    """A custom secret should not trigger the default-value warning."""
    monkeypatch.setenv("OVERWATCH_JWT_SECRET", "my-secure-random-key-1234")

    import importlib
    import api.auth as auth_mod

    with caplog.at_level(logging.WARNING, logger="overwatch.auth"):
        importlib.reload(auth_mod)

    default_warnings = [
        r for r in caplog.records if "default value" in r.message
    ]
    assert len(default_warnings) == 0


# ---------------------------------------------------------------------------
# Settings load JWT config from env vars
# ---------------------------------------------------------------------------

def test_settings_jwt_secret_from_env(monkeypatch):
    monkeypatch.setenv("OVERWATCH_JWT_SECRET", "env-secret-value")
    from config import OverwatchSettings

    settings = OverwatchSettings()
    assert settings.jwt_secret == "env-secret-value"


def test_settings_jwt_algorithm_from_env(monkeypatch):
    monkeypatch.setenv("OVERWATCH_JWT_ALGORITHM", "HS512")
    from config import OverwatchSettings

    settings = OverwatchSettings()
    assert settings.jwt_algorithm == "HS512"


def test_settings_jwt_expire_from_env(monkeypatch):
    monkeypatch.setenv("OVERWATCH_JWT_EXPIRE_MINUTES", "60")
    from config import OverwatchSettings

    settings = OverwatchSettings()
    assert settings.jwt_expire_minutes == 60


def test_settings_jwt_defaults():
    """Without env overrides, defaults match expected values."""
    from config import OverwatchSettings

    settings = OverwatchSettings()
    assert settings.jwt_secret == DEFAULT_JWT_SECRET
    assert settings.jwt_algorithm == "HS256"
    assert settings.jwt_expire_minutes == 480


# ---------------------------------------------------------------------------
# User store uses hashed passwords
# ---------------------------------------------------------------------------

def test_user_store_has_hashed_passwords():
    from api.auth import _USERS

    for username, record in _USERS.items():
        assert "password" not in record, (
            f"User {username} still has plaintext password"
        )
        assert "password_hash" in record
        assert record["password_hash"].startswith("$2b$")  # bcrypt


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
