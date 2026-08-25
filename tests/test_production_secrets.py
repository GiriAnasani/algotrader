from dataclasses import FrozenInstanceError, fields
import inspect
from pathlib import Path

import pytest

from core.production_config import ProductionConfig
from core.production_secrets import (
    REDACTED_SECRET,
    MissingProductionSecretError,
    ProductionSecrets,
    ProductionSecretsError,
    ProductionSecretsLoader,
)


FAKE_KEY = "fake-test-api-key"
FAKE_SECRET = "fake-test-api-secret"


def secret_mapping(**overrides):
    values = {
        "KITE_API_KEY": FAKE_KEY,
        "KITE_API_SECRET": FAKE_SECRET,
    }
    values.update(overrides)
    return values


def test_secret_model_is_frozen_with_exact_fields():
    secrets = ProductionSecrets(FAKE_KEY, FAKE_SECRET)
    assert [field.name for field in fields(secrets)] == [
        "kite_api_key",
        "kite_api_secret",
    ]
    with pytest.raises(FrozenInstanceError):
        secrets.kite_api_key = "replacement"


def test_valid_values_are_stripped_and_retained():
    secrets = ProductionSecrets(f"  {FAKE_KEY}\t", f"\n{FAKE_SECRET}  ")
    assert secrets.kite_api_key == FAKE_KEY
    assert secrets.kite_api_secret == FAKE_SECRET


@pytest.mark.parametrize("field_name", ["kite_api_key", "kite_api_secret"])
@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_empty_or_whitespace_secret_is_rejected(field_name, value):
    values = {"kite_api_key": FAKE_KEY, "kite_api_secret": FAKE_SECRET}
    values[field_name] = value
    with pytest.raises(ValueError) as error:
        ProductionSecrets(**values)
    assert value not in str(error.value) or not value.strip()


@pytest.mark.parametrize("field_name", ["kite_api_key", "kite_api_secret"])
@pytest.mark.parametrize("value", [None, True, False, 1, object(), b"secret"])
def test_non_string_secret_is_rejected_without_value_echo(field_name, value):
    values = {"kite_api_key": FAKE_KEY, "kite_api_secret": FAKE_SECRET}
    values[field_name] = value
    with pytest.raises(TypeError) as error:
        ProductionSecrets(**values)
    if isinstance(value, (bytes, str)):
        assert repr(value) not in str(error.value)


def test_repr_str_and_redaction_never_expose_values():
    secrets = ProductionSecrets(FAKE_KEY, FAKE_SECRET)
    representations = (repr(secrets), str(secrets), repr(secrets.redacted()))
    assert all(FAKE_KEY not in value for value in representations)
    assert all(FAKE_SECRET not in value for value in representations)
    assert secrets.redacted() == {
        "kite_api_key": REDACTED_SECRET,
        "kite_api_secret": REDACTED_SECRET,
    }


def test_redacted_returns_a_fresh_dictionary():
    secrets = ProductionSecrets(FAKE_KEY, FAKE_SECRET)
    first = secrets.redacted()
    second = secrets.redacted()
    assert first is not second
    first["kite_api_key"] = "changed"
    assert secrets.redacted()["kite_api_key"] == REDACTED_SECRET


@pytest.mark.parametrize("mapping", [None, [], (), "mapping", object(), True])
def test_mapping_loader_requires_mapping(mapping):
    with pytest.raises(TypeError):
        ProductionSecretsLoader.from_mapping(mapping)


def test_mapping_loader_strips_values_ignores_extras_and_does_not_mutate():
    mapping = secret_mapping(
        KITE_API_KEY=f" {FAKE_KEY} ",
        KITE_API_SECRET=f" {FAKE_SECRET} ",
        UNRELATED="ignored",
    )
    before = mapping.copy()
    secrets = ProductionSecretsLoader.from_mapping(mapping)
    assert secrets.kite_api_key == FAKE_KEY
    assert secrets.kite_api_secret == FAKE_SECRET
    assert mapping == before


@pytest.mark.parametrize("missing", ["KITE_API_KEY", "KITE_API_SECRET"])
def test_missing_mapping_secret_raises_typed_error_without_other_value(missing):
    mapping = secret_mapping()
    mapping.pop(missing)
    with pytest.raises(MissingProductionSecretError) as error:
        ProductionSecretsLoader.from_mapping(mapping)
    assert isinstance(error.value, ProductionSecretsError)
    assert missing in str(error.value)
    assert FAKE_KEY not in str(error.value)
    assert FAKE_SECRET not in str(error.value)


def test_environment_loader_reads_exact_names_and_strips(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", f" {FAKE_KEY} ")
    monkeypatch.setenv("KITE_API_SECRET", f" {FAKE_SECRET} ")
    secrets = ProductionSecretsLoader.from_environment()
    assert secrets.kite_api_key == FAKE_KEY
    assert secrets.kite_api_secret == FAKE_SECRET


@pytest.mark.parametrize("missing", ["KITE_API_KEY", "KITE_API_SECRET"])
def test_environment_loader_fails_closed_when_name_is_missing(monkeypatch, missing):
    monkeypatch.setenv("KITE_API_KEY", FAKE_KEY)
    monkeypatch.setenv("KITE_API_SECRET", FAKE_SECRET)
    monkeypatch.delenv(missing)
    with pytest.raises(MissingProductionSecretError, match=missing):
        ProductionSecretsLoader.from_environment()


@pytest.mark.parametrize("name", ["KITE_API_KEY", "KITE_API_SECRET"])
def test_environment_loader_fails_closed_for_empty_value(monkeypatch, name):
    monkeypatch.setenv("KITE_API_KEY", FAKE_KEY)
    monkeypatch.setenv("KITE_API_SECRET", FAKE_SECRET)
    monkeypatch.setenv(name, "   ")
    with pytest.raises(ValueError):
        ProductionSecretsLoader.from_environment()


def test_production_config_remains_non_secret():
    assert [field.name for field in fields(ProductionConfig)] == [
        "deployment_environment",
        "execution_mode",
        "execution_enabled",
        "position_store_path",
        "closed_position_history_store_path",
        "log_directory",
        "strategy_target_points",
    ]


def test_module_has_no_authentication_file_or_external_system_behavior():
    source = Path("core/production_secrets.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "dotenv", "open(", "read_text(", "write_text(", "kiteconnect(",
        "login_url(", "generate_session(", "set_access_token(", "access_token",
        "request_token", "boto3", "secretsmanager", "static_ip", "place_order(",
        ".save(", ".load(", "print(", "logging", "daily_loss", "max_trades",
    )
    assert all(term not in source for term in forbidden)


def test_environment_loading_is_explicit_and_uses_no_file_loader():
    source = inspect.getsource(ProductionSecretsLoader.from_environment)
    assert "os.environ" in source
    assert "from_mapping" in source
