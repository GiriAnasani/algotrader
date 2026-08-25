"""Explicit manual-login authentication lifecycle for production."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from kiteconnect import KiteConnect

from core.production_secrets import ProductionSecrets
from core.production_session import ProductionSession, ProductionSessionStore
from trading.ohlc import EXCHANGE_TIMEZONE


class ProductionAuthenticationState(Enum):
    LOGIN_REQUIRED = "LOGIN_REQUIRED"
    AUTHENTICATED = "AUTHENTICATED"


class ProductionAuthenticationError(RuntimeError):
    """Base error for the production authentication lifecycle."""


class ProductionAuthenticationRejectedError(ProductionAuthenticationError):
    """Raised when token exchange or broker validation is rejected."""


class ProductionAuthenticationProtocolError(ProductionAuthenticationError):
    """Raised when the authentication response violates its contract."""


@dataclass(frozen=True, repr=False)
class ProductionAuthenticationResult:
    state: ProductionAuthenticationState
    kite_client: object

    def __post_init__(self):
        if not isinstance(self.state, ProductionAuthenticationState):
            raise TypeError("state must be a ProductionAuthenticationState.")
        if self.kite_client is None:
            raise TypeError("kite_client is required.")

    def __repr__(self):
        return (
            "ProductionAuthenticationResult("
            f"state={self.state!r}, kite_client=<redacted>)"
        )

    __str__ = __repr__


class ProductionAuthenticator:
    """Owns one Kite client through manual login, exchange, and restore."""

    def __init__(self, secrets, kite_client_factory=KiteConnect, session_store=None):
        if not isinstance(secrets, ProductionSecrets):
            raise TypeError("secrets must be ProductionSecrets.")
        if not callable(kite_client_factory):
            raise TypeError("kite_client_factory must be callable.")
        if session_store is not None and not isinstance(
            session_store, ProductionSessionStore
        ):
            raise TypeError("session_store must be a ProductionSessionStore or None.")
        self._secrets = secrets
        self._session_store = session_store
        self._kite_client = kite_client_factory(api_key=secrets.kite_api_key)
        if self._kite_client is None:
            raise TypeError("kite_client_factory must return a client.")

    def __repr__(self):
        return "ProductionAuthenticator(secrets=<redacted>, kite_client=<redacted>)"

    @property
    def kite_client(self):
        return self._kite_client

    def begin(self):
        return ProductionAuthenticationResult(
            ProductionAuthenticationState.LOGIN_REQUIRED,
            self._kite_client,
        )

    def login_url(self):
        try:
            return self._kite_client.login_url()
        except Exception as error:
            raise ProductionAuthenticationRejectedError(
                "Could not create the manual login URL."
            ) from error

    def authenticate(self, request_token, authenticated_at=None):
        token = self._validated_request_token(request_token)
        if authenticated_at is not None:
            self._validated_timestamp(authenticated_at)
        try:
            response = self._kite_client.generate_session(
                token,
                api_secret=self._secrets.kite_api_secret,
            )
        except Exception as error:
            raise ProductionAuthenticationRejectedError(
                "Zerodha authentication was rejected."
            ) from error

        access_token = self._access_token(response)
        self._apply_and_validate(access_token)
        timestamp = authenticated_at
        if timestamp is None:
            timestamp = datetime.now(EXCHANGE_TIMEZONE)
        session = ProductionSession(access_token, timestamp)
        if self._session_store is not None:
            self._session_store.save(session)
        return ProductionAuthenticationResult(
            ProductionAuthenticationState.AUTHENTICATED,
            self._kite_client,
        )

    def restore(self, now=None):
        if self._session_store is None:
            return self.begin()
        session = self._session_store.load()
        if session is None:
            return self.begin()
        evaluated_at = now
        if evaluated_at is None:
            evaluated_at = datetime.now(EXCHANGE_TIMEZONE)
        if not session.is_eligible_at(evaluated_at):
            return self.begin()
        self._apply_and_validate(session.access_token)
        return ProductionAuthenticationResult(
            ProductionAuthenticationState.AUTHENTICATED,
            self._kite_client,
        )

    @staticmethod
    def _validated_request_token(value):
        if not isinstance(value, str):
            raise TypeError("request token must be a string.")
        token = value.strip()
        if not token:
            raise ValueError("request token must not be empty.")
        return token

    @staticmethod
    def _access_token(response):
        if not isinstance(response, Mapping):
            raise ProductionAuthenticationProtocolError(
                "Authentication response must be a mapping."
            )
        if "access_token" not in response:
            raise ProductionAuthenticationProtocolError(
                "Authentication response is missing the access token."
            )
        token = response["access_token"]
        if not isinstance(token, str) or not token.strip():
            raise ProductionAuthenticationProtocolError(
                "Authentication response contains an invalid access token."
            )
        return token.strip()

    @staticmethod
    def _validated_timestamp(value):
        if not isinstance(value, datetime):
            raise TypeError("authenticated_at must be a datetime.")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("authenticated_at must be timezone-aware.")

    def _apply_and_validate(self, access_token):
        try:
            self._kite_client.set_access_token(access_token)
            self._kite_client.profile()
        except Exception as error:
            raise ProductionAuthenticationRejectedError(
                "Broker authentication validation was rejected."
            ) from error
