from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel


class CredentialStatus(BaseModel):
    provider: str
    configured: bool
    source: str
    updated_at: str | None = None


class CredentialBackend(Protocol):
    source_name: str

    def set_password(self, service: str, username: str, password: str) -> None: ...

    def get_password(self, service: str, username: str) -> str | None: ...

    def delete_password(self, service: str, username: str) -> None: ...


@dataclass
class InMemoryCredentialBackend:
    source_name: str = "memory"
    _values: dict[tuple[str, str], str] = field(default_factory=dict)

    def set_password(self, service: str, username: str, password: str) -> None:
        self._values[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._values.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self._values.pop((service, username), None)


class KeyringCredentialBackend:
    source_name = "keyring"

    def __init__(self) -> None:
        import keyring
        self._keyring = keyring

    def set_password(self, service: str, username: str, password: str) -> None:
        self._keyring.set_password(service, username, password)

    def get_password(self, service: str, username: str) -> str | None:
        return self._keyring.get_password(service, username)

    def delete_password(self, service: str, username: str) -> None:
        try:
            self._keyring.delete_password(service, username)
        except Exception:
            return


class CredentialStore:
    def __init__(self, backend: CredentialBackend | None = None, service: str = "phycode") -> None:
        self.backend = backend if backend is not None else KeyringCredentialBackend()
        self.service = service
        self._updated_at: dict[str, str] = {}

    def set_key(self, provider: str, secret: str) -> None:
        self.backend.set_password(self.service, provider, secret)
        self._updated_at[provider] = datetime.now(timezone.utc).isoformat()

    def get_key(self, provider: str) -> str | None:
        return self.backend.get_password(self.service, provider)

    def clear_key(self, provider: str) -> None:
        self.backend.delete_password(self.service, provider)
        self._updated_at.pop(provider, None)

    def status(self, provider: str) -> CredentialStatus:
        return CredentialStatus(
            provider=provider,
            configured=self.get_key(provider) is not None,
            source=self.backend.source_name,
            updated_at=self._updated_at.get(provider),
        )
