from __future__ import annotations

import ipaddress
import json
import re
import socket
from collections.abc import Callable
from html.parser import HTMLParser
from io import BytesIO
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from ddgs import DDGS
from pypdf import PdfReader

from phycode.models import ToolCall, ToolResult, ToolRiskLevel, ToolSpec
from phycode.tools.base import ToolRegistry

MAX_RESPONSE_BYTES = 2_000_000
MAX_OUTPUT_CHARS = 20_000
MAX_EXCERPT_CHARS = 16_000
MAX_REDIRECTS = 5
USER_AGENT = "PhyCode/0.1 (GAIA benchmark research agent)"
_SYNTHETIC_PROXY_NETWORK = ipaddress.ip_network("198.18.0.0/15")
_BLOCK_ELEMENTS = {
    "article",
    "br",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "p",
    "section",
    "table",
    "td",
    "th",
    "tr",
}
_HIDDEN_ELEMENTS = {"script", "style", "noscript", "svg"}


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in _HIDDEN_ELEMENTS:
            self.hidden_depth += 1
        elif self.hidden_depth == 0 and tag in _BLOCK_ELEMENTS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _HIDDEN_ELEMENTS and self.hidden_depth:
            self.hidden_depth -= 1
        elif self.hidden_depth == 0 and tag in _BLOCK_ELEMENTS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.hidden_depth == 0:
            self.parts.append(data)


def _html_to_text(value: str) -> str:
    parser = _VisibleTextParser()
    parser.feed(value)
    lines = [re.sub(r"\s+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _relevant_excerpt(content: str, query: str | None) -> str:
    if not query or len(content) <= MAX_EXCERPT_CHARS:
        return content
    raw_terms = {term for term in re.findall(r"[\w'-]{2,}", query.lower()) if term}
    text_terms = {term for term in raw_terms if not term.isdigit()}
    text_terms.update(term[:-1] for term in tuple(text_terms) if len(term) > 3 and term.endswith("s"))
    numeric_terms = raw_terms - text_terms
    terms = text_terms | numeric_terms
    if not terms:
        return content[:MAX_EXCERPT_CHARS]
    lines = content.splitlines()
    ranked: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        lowered = line.lower()
        score = 4 * sum(1 for term in text_terms if term in lowered)
        score += sum(1 for term in numeric_terms if term in lowered)
        if score and line.lstrip().startswith("|"):
            score += 2
        if score:
            ranked.append((score, index))
    if not ranked:
        return content[:MAX_EXCERPT_CHARS]
    seen: set[int] = set()
    chunks: list[str] = []
    excerpt_length = 0
    for _, index in sorted(ranked, key=lambda item: (-item[0], item[1]))[:20]:
        stripped = lines[index].lstrip()
        if stripped.startswith("#"):
            heading_level = len(stripped) - len(stripped.lstrip("#"))
            start = index
            end = index + 1
            while end < len(lines):
                candidate = lines[end].lstrip()
                if candidate.startswith("#"):
                    candidate_level = len(candidate) - len(candidate.lstrip("#"))
                    if candidate_level <= heading_level:
                        break
                end += 1
        elif stripped.startswith("|"):
            start = index
            while start > 0 and lines[start - 1].lstrip().startswith("|"):
                start -= 1
            end = index + 1
            while end < len(lines) and lines[end].lstrip().startswith("|"):
                end += 1
        else:
            start = max(0, index - 2)
            has_text_match = any(term in lines[index].lower() for term in text_terms)
            forward_context = 13 if has_text_match and len(lines[index]) <= 160 else 3
            end = min(len(lines), index + forward_context)
        new_indexes = [line_index for line_index in range(start, end) if line_index not in seen]
        if not new_indexes:
            continue
        chunk = "\n".join(lines[line_index] for line_index in new_indexes)
        separator_length = 2 if chunks else 0
        available = MAX_EXCERPT_CHARS - excerpt_length - separator_length
        if available <= 0:
            break
        if len(chunk) > available:
            marker = "\n...[section clipped]...\n"
            content_budget = max(0, available - len(marker))
            head = (content_budget * 2) // 3
            chunk = chunk[:head] + marker + chunk[-(content_budget - head) :]
        chunks.append(chunk)
        seen.update(new_indexes)
        excerpt_length += len(chunk) + separator_length
        if excerpt_length >= MAX_EXCERPT_CHARS:
            break
    return "\n\n".join(chunks)


def _validate_public_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only public HTTP(S) URLs are allowed")
    if not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("URL must contain a public host and no embedded credentials")

    try:
        literal_ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not literal_ip.is_global:
        raise ValueError("URL contains a non-public network address")

    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"Could not resolve URL host: {parsed.hostname}") from exc
    if not addresses:
        raise ValueError(f"Could not resolve URL host: {parsed.hostname}")
    for address in addresses:
        raw_ip = str(address[4][0]).split("%", 1)[0]
        ip = ipaddress.ip_address(raw_ip)
        # Some controlled execution environments map public hostnames into RFC 2544's
        # benchmarking range before proxying. Literal access to that range remains blocked.
        if not ip.is_global and not (literal_ip is None and ip in _SYNTHETIC_PROXY_NETWORK):
            raise ValueError("URL resolves to a non-public network address")
    return parsed.geturl()


def _wikipedia_fallback_url(url: str) -> str | None:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    if hostname != "wikipedia.org" and not hostname.endswith(".wikipedia.org"):
        return None
    if parsed.path.startswith("/wiki/"):
        title = quote(unquote(parsed.path.removeprefix("/wiki/")), safe="")
        api_query = f"action=parse%26page={title}%26prop=text%26format=json%26formatversion=2%26redirects=1"
        return f"https://r.jina.ai/http://{parsed.netloc}/w/api.php?{api_query}"
    source_url = f"http://{parsed.netloc}{parsed.path or '/'}"
    if parsed.query:
        source_url += f"?{parsed.query}"
    return f"https://r.jina.ai/{source_url}"


def _decode_response(response: httpx.Response, body: bytes) -> str:
    content_type = response.headers.get("content-type", "").lower()
    if "application/pdf" in content_type or response.url.path.lower().endswith(".pdf"):
        reader = PdfReader(BytesIO(body))
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if "json" in content_type:
        try:
            return json.dumps(json.loads(body), ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    text = body.decode(response.encoding or "utf-8", errors="replace")
    if "html" in content_type or "xhtml" in content_type:
        return _html_to_text(text)
    return text


def _fetch_public_url(url: str) -> tuple[str, str, str, bool]:
    current_url = _validate_public_url(url)
    wikipedia_fallback_used = False
    transport = httpx.HTTPTransport(retries=1)
    timeout = httpx.Timeout(20, connect=10)
    with httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=True,
        transport=transport,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise ValueError("Redirect response omitted a Location header")
                        current_url = _validate_public_url(urljoin(current_url, location))
                        continue
                    if response.status_code in {403, 429} and not wikipedia_fallback_used:
                        fallback_url = _wikipedia_fallback_url(current_url)
                        if fallback_url is not None:
                            current_url = _validate_public_url(fallback_url)
                            wikipedia_fallback_used = True
                            continue
                    if response.status_code >= 400:
                        raise ValueError(f"HTTP {response.status_code} while fetching URL")
                    chunks: list[bytes] = []
                    size = 0
                    truncated = False
                    for chunk in response.iter_bytes():
                        remaining = MAX_RESPONSE_BYTES - size
                        if len(chunk) > remaining:
                            chunks.append(chunk[:remaining])
                            truncated = True
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                    body = b"".join(chunks)
                    content_type = response.headers.get("content-type", "application/octet-stream")
                    return current_url, content_type, _decode_response(response, body), truncated
            except httpx.HTTPError:
                fallback_url = None if wikipedia_fallback_used else _wikipedia_fallback_url(current_url)
                if fallback_url is None:
                    raise
                current_url = _validate_public_url(fallback_url)
                wikipedia_fallback_used = True
    raise ValueError(f"URL exceeded {MAX_REDIRECTS} redirects")


def register_web_tools(
    registry: ToolRegistry,
    search_factory: Callable[[], Any] = DDGS,
    fetcher: Callable[[str], tuple[str, str, str, bool]] = _fetch_public_url,
) -> None:
    def web_search(call: ToolCall) -> ToolResult:
        query = str(call.args["query"]).strip()
        if not query:
            return ToolResult(tool_call_id=call.id, status="invalid_tool_args", stderr="query cannot be blank")
        max_results = max(1, min(int(call.args.get("max_results", 5)), 10))
        results = list(search_factory().text(query, max_results=max_results))
        normalized = [
            {
                "title": str(item.get("title", "")),
                "url": str(item.get("href", item.get("url", ""))),
                "snippet": str(item.get("body", item.get("snippet", ""))),
            }
            for item in results[:max_results]
        ]
        return ToolResult(
            tool_call_id=call.id,
            status="ok",
            stdout=json.dumps(normalized, ensure_ascii=False),
        )

    def web_fetch(call: ToolCall) -> ToolResult:
        try:
            final_url, content_type, content, response_truncated = fetcher(str(call.args["url"]))
        except Exception as exc:
            return ToolResult(
                tool_call_id=call.id,
                status="tool_error",
                stderr=f"{exc}. Try web.search for an alternate public source or official API.",
            )
        query = str(call.args.get("query", "")).strip() or None
        output = json.dumps(
            {"url": final_url, "content_type": content_type, "query": query, "content": _relevant_excerpt(content, query)},
            ensure_ascii=False,
        )
        output_truncated = len(output) > MAX_OUTPUT_CHARS
        return ToolResult(
            tool_call_id=call.id,
            status="ok",
            stdout=output[:MAX_OUTPUT_CHARS],
            truncated=response_truncated or output_truncated,
        )

    registry.register(
        ToolSpec(
            name="web.search",
            description="Search the public web and return titles, URLs, and snippets; fetch a source before relying on it",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["query"],
            },
            risk_level=ToolRiskLevel.SAFE,
        ),
        web_search,
    )
    registry.register(
        ToolSpec(
            name="web.fetch",
            description=(
                "Fetch and extract text or JSON from a public HTTP(S) URL; pass query for relevant sections of long "
                "pages; private-network URLs are blocked"
            ),
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}, "query": {"type": "string"}},
                "required": ["url"],
            },
            risk_level=ToolRiskLevel.SAFE,
        ),
        web_fetch,
    )
