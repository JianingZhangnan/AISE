from phycode.redaction import redact_text


def test_redacts_openai_style_key():
    text = "Authorization: Bearer sk-testsecret1234567890"
    assert "sk-testsecret" not in redact_text(text)
    assert "[REDACTED_SECRET]" in redact_text(text)


def test_redacts_env_assignment():
    text = "OPENAI_API_KEY=abc123SECRET"
    assert "abc123SECRET" not in redact_text(text)
    assert "OPENAI_API_KEY=[REDACTED_SECRET]" in redact_text(text)
