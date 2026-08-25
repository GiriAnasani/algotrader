from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timedelta
from enum import Enum
import inspect
from pathlib import Path

import pytest

from core.production_auth import (
    ProductionAuthenticationProtocolError,
    ProductionAuthenticationRejectedError,
    ProductionAuthenticationResult,
    ProductionAuthenticationState,
    ProductionAuthenticator,
)
from core.production_secrets import ProductionSecrets
from core.production_session import ProductionSession, ProductionSessionStore
from trading.ohlc import EXCHANGE_TIMEZONE


API_KEY = "fake-api-key"
API_SECRET = "fake-api-secret"
REQUEST_TOKEN = "fake-request-token"
ACCESS_TOKEN = "fake-access-token"
NOW = datetime(2026, 8, 25, 9, 0, tzinfo=EXCHANGE_TIMEZONE)


class FakeKiteClient:
    def __init__(self, api_key):
        self.api_key = api_key
        self.login_url_calls = 0
        self.generate_session_calls = []
        self.set_access_token_calls = []
        self.profile_calls = 0
        self.positions_calls = 0
        self.orders_calls = 0
        self.order_history_calls = 0
        self.place_order_calls = 0
        self.modify_order_calls = 0
        self.cancel_order_calls = 0
        self.response = {"access_token": ACCESS_TOKEN}
        self.exchange_error = None
        self.profile_error = None

    def login_url(self):
        self.login_url_calls += 1
        return "https://example.test/manual-login"

    def generate_session(self, request_token, api_secret):
        self.generate_session_calls.append((request_token, api_secret))
        if self.exchange_error:
            raise self.exchange_error
        return self.response

    def set_access_token(self, access_token):
        self.set_access_token_calls.append(access_token)

    def profile(self):
        self.profile_calls += 1
        if self.profile_error:
            raise self.profile_error
        return {"user_id": "fake-user"}


class Factory:
    def __init__(self):
        self.arguments = []
        self.client = None

    def __call__(self, **kwargs):
        self.arguments.append(kwargs)
        self.client = FakeKiteClient(**kwargs)
        return self.client


def authenticator(store=None):
    factory = Factory()
    auth = ProductionAuthenticator(
        ProductionSecrets(API_KEY, API_SECRET),
        kite_client_factory=factory,
        session_store=store,
    )
    return auth, factory, factory.client


def assert_no_trading_calls(client):
    assert client.positions_calls == 0
    assert client.orders_calls == 0
    assert client.order_history_calls == 0
    assert client.place_order_calls == 0
    assert client.modify_order_calls == 0
    assert client.cancel_order_calls == 0


def test_authentication_states_are_exact():
    assert issubclass(ProductionAuthenticationState, Enum)
    assert [(state.name, state.value) for state in ProductionAuthenticationState] == [
        ("LOGIN_REQUIRED", "LOGIN_REQUIRED"),
        ("AUTHENTICATED", "AUTHENTICATED"),
    ]


@pytest.mark.parametrize("value", [None, {}, "secrets", object(), True])
def test_authenticator_requires_production_secrets(value):
    with pytest.raises(TypeError):
        ProductionAuthenticator(value, kite_client_factory=Factory())


def test_factory_receives_exact_api_key_only_and_construction_has_no_calls():
    auth, factory, client = authenticator()
    assert factory.arguments == [{"api_key": API_KEY}]
    assert auth.kite_client is client
    assert API_SECRET not in repr(auth)
    assert_no_trading_calls(client)
    assert client.profile_calls == 0


def test_begin_returns_frozen_safe_login_required_result():
    auth, _, client = authenticator()
    result = auth.begin()
    assert [field.name for field in fields(result)] == ["state", "kite_client"]
    assert result.state is ProductionAuthenticationState.LOGIN_REQUIRED
    assert result.kite_client is client
    assert API_KEY not in repr(result)
    assert API_SECRET not in str(result)
    with pytest.raises(FrozenInstanceError):
        result.state = ProductionAuthenticationState.AUTHENTICATED


def test_login_url_is_explicit_without_exchange_validation_or_trading_calls():
    auth, _, client = authenticator()
    assert auth.login_url() == "https://example.test/manual-login"
    assert client.login_url_calls == 1
    assert client.generate_session_calls == []
    assert client.profile_calls == 0
    assert_no_trading_calls(client)


@pytest.mark.parametrize("token", [None, True, False, 1, object()])
def test_request_token_requires_string(token):
    auth, _, client = authenticator()
    with pytest.raises(TypeError):
        auth.authenticate(token, NOW)
    assert client.generate_session_calls == []


@pytest.mark.parametrize("token", ["", "   ", "\t"])
def test_request_token_rejects_empty_values(token):
    auth, _, client = authenticator()
    with pytest.raises(ValueError):
        auth.authenticate(token, NOW)
    assert client.generate_session_calls == []


@pytest.mark.parametrize(
    "timestamp", ["time", object(), True, datetime(2026, 8, 25, 9, 0)]
)
def test_explicit_authentication_timestamp_fails_before_exchange(timestamp):
    auth, _, client = authenticator()
    with pytest.raises((TypeError, ValueError)):
        auth.authenticate(REQUEST_TOKEN, timestamp)
    assert client.generate_session_calls == []
    assert client.profile_calls == 0


def test_fresh_authentication_uses_one_exact_client_and_validates_profile():
    auth, _, client = authenticator()
    result = auth.authenticate(f"  {REQUEST_TOKEN}  ", NOW)
    assert result.state is ProductionAuthenticationState.AUTHENTICATED
    assert result.kite_client is client is auth.kite_client
    assert client.generate_session_calls == [(REQUEST_TOKEN, API_SECRET)]
    assert client.set_access_token_calls == [ACCESS_TOKEN]
    assert client.profile_calls == 1
    assert REQUEST_TOKEN not in repr(result)
    assert ACCESS_TOKEN not in repr(result)
    assert_no_trading_calls(client)


@pytest.mark.parametrize(
    "response",
    [None, [], {}, {"access_token": None}, {"access_token": True},
     {"access_token": ""}, {"access_token": "   "}],
)
def test_malformed_session_response_fails_before_token_application(response):
    auth, _, client = authenticator()
    client.response = response
    with pytest.raises(ProductionAuthenticationProtocolError) as error:
        auth.authenticate(REQUEST_TOKEN, NOW)
    assert client.set_access_token_calls == []
    assert client.profile_calls == 0
    assert REQUEST_TOKEN not in str(error.value)
    assert API_SECRET not in str(error.value)


def test_exchange_failure_is_safely_translated():
    auth, _, client = authenticator()
    client.exchange_error = RuntimeError(f"rejected {REQUEST_TOKEN} {API_SECRET}")
    with pytest.raises(ProductionAuthenticationRejectedError) as error:
        auth.authenticate(REQUEST_TOKEN, NOW)
    assert REQUEST_TOKEN not in str(error.value)
    assert API_SECRET not in str(error.value)


def test_profile_failure_is_safely_translated_without_trading_calls():
    auth, _, client = authenticator()
    client.profile_error = RuntimeError(f"invalid {ACCESS_TOKEN}")
    with pytest.raises(ProductionAuthenticationRejectedError) as error:
        auth.authenticate(REQUEST_TOKEN, NOW)
    assert ACCESS_TOKEN not in str(error.value)
    assert client.profile_calls == 1
    assert_no_trading_calls(client)


def test_fresh_authentication_persists_session_without_returning_token(tmp_path):
    store = ProductionSessionStore(tmp_path / "session.json")
    auth, _, client = authenticator(store)
    result = auth.authenticate(REQUEST_TOKEN, NOW)
    assert store.load() == ProductionSession(ACCESS_TOKEN, NOW)
    assert result.kite_client is client
    assert not hasattr(result, "access_token")
    assert not hasattr(result, "request_token")


def test_restore_without_store_or_missing_session_requires_login(tmp_path):
    auth, _, client = authenticator()
    assert auth.restore(NOW).state is ProductionAuthenticationState.LOGIN_REQUIRED
    missing_auth, _, missing_client = authenticator(
        ProductionSessionStore(tmp_path / "missing.json")
    )
    assert missing_auth.restore(NOW).state is ProductionAuthenticationState.LOGIN_REQUIRED
    assert client.profile_calls == missing_client.profile_calls == 0


def test_expired_restore_requires_login_without_applying_or_probing(tmp_path):
    store = ProductionSessionStore(tmp_path / "session.json")
    store.save(ProductionSession(ACCESS_TOKEN, NOW))
    auth, _, client = authenticator(store)
    result = auth.restore(
        datetime(2026, 8, 26, 6, 0, tzinfo=EXCHANGE_TIMEZONE)
    )
    assert result.state is ProductionAuthenticationState.LOGIN_REQUIRED
    assert client.set_access_token_calls == []
    assert client.profile_calls == 0


def test_eligible_restore_applies_token_and_requires_remote_profile(tmp_path):
    store = ProductionSessionStore(tmp_path / "session.json")
    store.save(ProductionSession(ACCESS_TOKEN, NOW))
    auth, _, client = authenticator(store)
    result = auth.restore(NOW + timedelta(hours=1))
    assert result.state is ProductionAuthenticationState.AUTHENTICATED
    assert result.kite_client is client
    assert client.set_access_token_calls == [ACCESS_TOKEN]
    assert client.profile_calls == 1
    assert client.generate_session_calls == []
    assert_no_trading_calls(client)


def test_remote_invalid_restore_fails_closed(tmp_path):
    store = ProductionSessionStore(tmp_path / "session.json")
    store.save(ProductionSession(ACCESS_TOKEN, NOW))
    auth, _, client = authenticator(store)
    client.profile_error = RuntimeError(f"invalid {ACCESS_TOKEN}")
    with pytest.raises(ProductionAuthenticationRejectedError) as error:
        auth.restore(NOW + timedelta(hours=1))
    assert ACCESS_TOKEN not in str(error.value)
    assert_no_trading_calls(client)


def test_module_preserves_manual_login_and_execution_separation():
    source = Path("core/production_auth.py").read_text(encoding="utf-8").lower()
    forbidden = (
        "password", "totp", "pyotp", "requests.session", "username",
        "liveauthor", "livereadiness", "execution_enabled", "place_order(",
        "positions(", "orders(", "order_history(", "websocket", "boto3",
        "static_ip", "daily_loss", "logging",
    )
    assert all(term not in source for term in forbidden)


def test_authenticator_has_no_request_or_access_token_attributes():
    auth, _, _ = authenticator()
    assert "request_token" not in vars(auth)
    assert "access_token" not in vars(auth)
    source = inspect.getsource(ProductionAuthenticationResult)
    assert "request_token" not in source
    assert "access_token" not in source
