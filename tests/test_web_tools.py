from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

import phycode.tools.web_tools as web_tools
from phycode.models import PolicyAction, ToolCall
from phycode.policy import PolicyContext
from phycode.tools.base import ToolRegistry, ToolRuntime
from phycode.tools.web_tools import (
    _html_to_text,
    _relevant_excerpt,
    _validate_public_url,
    _wikipedia_fallback_url,
    register_web_tools,
)


class FakeSearch:
    def text(self, query: str, max_results: int):
        assert query == "trial enrollment"
        assert max_results == 2
        return [
            {"title": "Study", "href": "https://example.com/study", "body": "Actual enrollment"},
            {"title": "Other", "href": "https://example.org/other", "body": "Secondary result"},
        ]


def _runtime(fetcher=None) -> ToolRuntime:
    registry = ToolRegistry()
    if fetcher is None:
        register_web_tools(registry, search_factory=FakeSearch)
    else:
        register_web_tools(registry, search_factory=FakeSearch, fetcher=fetcher)
    return ToolRuntime(registry)


def test_web_search_returns_structured_results(tmp_path):
    result = _runtime().run(
        ToolCall(tool_name="web.search", args={"query": "trial enrollment", "max_results": 2}),
        PolicyContext(tmp_path, [], interactive=False),
    )

    assert result.policy.decision == PolicyAction.ALLOW
    assert result.tool_result.status == "ok"
    assert '"url": "https://example.com/study"' in result.tool_result.stdout


def test_web_fetch_returns_extracted_content(tmp_path):
    def fake_fetch(url: str):
        assert url == "https://example.com/data"
        return url, "application/json", '{"count": 90}', False

    result = _runtime(fake_fetch).run(
        ToolCall(tool_name="web.fetch", args={"url": "https://example.com/data"}),
        PolicyContext(tmp_path, [], interactive=False),
    )

    assert result.policy.decision == PolicyAction.ALLOW
    assert result.tool_result.status == "ok"
    assert "90" in result.tool_result.stdout


def test_web_fetch_failure_suggests_searching_for_alternate_source(tmp_path):
    def failing_fetch(url: str):
        raise TimeoutError(f"timed out fetching {url}")

    result = _runtime(failing_fetch).run(
        ToolCall(tool_name="web.fetch", args={"url": "https://example.com/slow"}),
        PolicyContext(tmp_path, [], interactive=False),
    )

    assert result.tool_result.status == "tool_error"
    assert "web.search" in result.tool_result.stderr


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/admin",
        "http://[::1]/admin",
        "http://198.18.0.90/admin",
        "http://user:password@example.com/",
    ],
)
def test_web_fetch_url_validation_blocks_unsafe_targets(url: str):
    with pytest.raises(ValueError):
        _validate_public_url(url)


def test_web_fetch_url_validation_allows_public_target(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
    )
    assert _validate_public_url("https://example.com/source") == "https://example.com/source"


def test_web_fetch_url_validation_allows_synthetic_proxy_dns_for_hostname(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.0.90", 443))],
    )
    assert _validate_public_url("https://example.com/source") == "https://example.com/source"


def test_html_extraction_omits_scripts_and_preserves_visible_text():
    text = _html_to_text("<html><script>secret()</script><body><h1>Title</h1><p>Useful text</p></body></html>")
    assert "secret" not in text
    assert "Title\nUseful text" in text


def test_wikipedia_fallback_is_limited_to_wikipedia_hosts():
    fallback = _wikipedia_fallback_url("https://en.wikipedia.org/wiki/Moon")
    assert fallback is not None
    assert fallback.startswith("https://r.jina.ai/http://en.wikipedia.org/w/api.php?")
    assert "page=Moon" in fallback
    assert _wikipedia_fallback_url("https://example.com/wiki/Moon") is None


def test_relevant_excerpt_finds_late_section_on_long_page():
    content = "\n".join(["intro"] * 5_000 + ["Discography", "2001 album", "2005 album", "2009 album"])

    excerpt = _relevant_excerpt(content, "discography albums 2000 2009")

    assert "Discography" in excerpt
    assert "2009 album" in excerpt
    assert len(excerpt) <= web_tools.MAX_EXCERPT_CHARS


def test_relevant_excerpt_keeps_complete_matching_markdown_table():
    table = [
        "| Year | Album details |",
        "| --- | --- |",
        *(f"| {year} | Album {year} |" for year in range(1960, 2010)),
    ]
    content = "\n".join(["2000 biography note"] * 2_000 + table + ["references"] * 2_000)

    excerpt = _relevant_excerpt(content, "discography studio albums 2000 2009")

    assert "| 1960 | Album 1960 |" in excerpt
    assert "| 2009 | Album 2009 |" in excerpt
    assert len(excerpt) <= web_tools.MAX_EXCERPT_CHARS


def test_relevant_excerpt_keeps_matching_markdown_section_until_next_heading():
    studio_rows = [f"| {year} | Studio record {year} |" for year in range(1960, 2010)]
    content = "\n".join(
        ["intro"] * 5_000
        + ["### Studio albums", "| Year | Album details |", "| --- | --- |", *studio_rows]
        + ["### Live albums", "| 2009 | Live record |"]
    )

    excerpt = _relevant_excerpt(content, "studio albums 2000 2009")

    assert "### Studio albums" in excerpt
    assert "| 2005 | Studio record 2005 |" in excerpt
    assert "| 2009 | Studio record 2009 |" in excerpt


def test_web_fetch_uses_system_proxy_settings_and_identifying_user_agent(monkeypatch):
    captured = {}

    class FakeResponse:
        is_redirect = False
        status_code = 200
        headers = {"content-type": "text/plain"}
        url = SimpleNamespace(path="/source")
        encoding = "utf-8"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def iter_bytes(self):
            yield b"evidence"

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, method, url):
            assert method == "GET"
            assert url == "https://example.com/source"
            return FakeResponse()

    monkeypatch.setattr(web_tools, "_validate_public_url", lambda url: url)
    monkeypatch.setattr(web_tools.httpx, "Client", FakeClient)

    _, _, content, _ = web_tools._fetch_public_url("https://example.com/source")

    assert content == "evidence"
    assert captured["trust_env"] is True
    assert captured["headers"]["User-Agent"] == web_tools.USER_AGENT
