import json

from phycode.redaction import redact_obj, redact_text


def test_redacts_openai_style_key():
    text = "Authorization: Bearer sk-testsecret1234567890"
    assert "sk-testsecret" not in redact_text(text)
    assert "[REDACTED_SECRET]" in redact_text(text)


def test_redacts_env_assignment():
    text = "OPENAI_API_KEY=abc123SECRET"
    assert "abc123SECRET" not in redact_text(text)
    assert "OPENAI_API_KEY=[REDACTED_SECRET]" in redact_text(text)


def test_key_aware_object_redaction_masks_entire_ordinary_secret_values() -> None:
    original = {
        "api_key": "0123456789abcdef",
        "access-token": "feedface",
        "client_secret": "plain-hex-value",
        "password": "cafebabe",
        "Authorization": "ordinary-value",
        "nested": {"refresh_token": "deadbeef", "token_count": 7},
        "credential_ref": "keyring:phycode/default",
    }

    redacted = redact_obj(original)
    serialized = json.dumps(redacted)

    for secret in ("0123456789abcdef", "feedface", "plain-hex-value", "cafebabe", "deadbeef"):
        assert secret not in serialized
    assert redacted["Authorization"] == "[REDACTED_SECRET]"
    assert redacted["nested"]["token_count"] == 7
    assert redacted["credential_ref"] == "keyring:phycode/default"
    assert json.loads(serialized) == redacted


def test_object_redaction_recurses_through_tuples_and_fails_closed_on_sets() -> None:
    ordinary_hex_secret = "a1b2c3d4"
    original = {
        "nested": ({"api_key": ordinary_hex_secret}, "safe"),
        "unsupported": {"password=cafebabe"},
    }

    redacted = redact_obj(original)

    assert isinstance(redacted["nested"], tuple)
    assert redacted["nested"] == ({"api_key": "[REDACTED_SECRET]"}, "safe")
    assert redacted["unsupported"] == "[REDACTED_UNSUPPORTED_TYPE]"
    assert ordinary_hex_secret not in str(redacted)
