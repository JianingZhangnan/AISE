from phycode.credentials import CredentialStore, InMemoryCredentialBackend


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
