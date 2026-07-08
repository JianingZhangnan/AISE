from phycode.credentials import CredentialStore, InMemoryCredentialBackend


class FakeKeyringBackend:
    source_name = "keyring"

    def __init__(self):
        self._values = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self._values[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._values.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        self._values.pop((service, username), None)


def test_key_status_does_not_reveal_secret():
    store = CredentialStore(backend=InMemoryCredentialBackend())
    store.set_key("openai-compatible", "sk-secret-value")
    status = store.status("openai-compatible")
    assert status.configured is True
    assert "secret" not in status.model_dump_json()


def test_clear_key_removes_secret():
    store = CredentialStore(backend=InMemoryCredentialBackend())
    store.set_key("openai-compatible", "sk-secret-value")
    store.clear_key("openai-compatible")
    assert store.get_key("openai-compatible") is None
    assert store.status("openai-compatible").configured is False


def test_default_store_uses_keyring_not_memory(monkeypatch):
    monkeypatch.setattr("phycode.credentials.KeyringCredentialBackend", FakeKeyringBackend)
    store = CredentialStore()
    assert not isinstance(store.backend, InMemoryCredentialBackend)
    assert store.status("openai-compatible").source == "keyring"
