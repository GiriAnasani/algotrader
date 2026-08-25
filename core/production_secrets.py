"""Validated static application credentials for production composition."""

from collections.abc import Mapping
from dataclasses import dataclass
import os


REDACTED_SECRET = "***"
KITE_API_KEY_ENVIRONMENT_VARIABLE = "KITE_API_KEY"
KITE_API_SECRET_ENVIRONMENT_VARIABLE = "KITE_API_SECRET"


class ProductionSecretsError(RuntimeError):
    """Base error for production secret acquisition."""


class MissingProductionSecretError(ProductionSecretsError):
    """Raised when a required secret name is absent from its source."""


def _validated_secret(value, field_name):
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string.")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty.")
    return stripped


@dataclass(frozen=True, repr=False)
class ProductionSecrets:
    """Immutable static application credentials with safe representation."""

    kite_api_key: str
    kite_api_secret: str

    def __post_init__(self):
        object.__setattr__(
            self,
            "kite_api_key",
            _validated_secret(self.kite_api_key, "kite_api_key"),
        )
        object.__setattr__(
            self,
            "kite_api_secret",
            _validated_secret(self.kite_api_secret, "kite_api_secret"),
        )

    def __repr__(self):
        return (
            "ProductionSecrets("
            f"kite_api_key={REDACTED_SECRET!r}, "
            f"kite_api_secret={REDACTED_SECRET!r})"
        )

    __str__ = __repr__

    def redacted(self):
        """Returns a fresh representation containing no credential values."""
        return {
            "kite_api_key": REDACTED_SECRET,
            "kite_api_secret": REDACTED_SECRET,
        }


class ProductionSecretsLoader:
    """Loads static credentials from explicit process-owned sources."""

    @classmethod
    def from_mapping(cls, mapping):
        if not isinstance(mapping, Mapping):
            raise TypeError("Secret source must be a mapping.")

        values = {}
        for environment_name, field_name in (
            (KITE_API_KEY_ENVIRONMENT_VARIABLE, "kite_api_key"),
            (KITE_API_SECRET_ENVIRONMENT_VARIABLE, "kite_api_secret"),
        ):
            if environment_name not in mapping:
                raise MissingProductionSecretError(
                    f"Required production secret {environment_name} is missing."
                )
            values[field_name] = mapping[environment_name]
        return ProductionSecrets(**values)

    @classmethod
    def from_environment(cls):
        return cls.from_mapping(os.environ)
