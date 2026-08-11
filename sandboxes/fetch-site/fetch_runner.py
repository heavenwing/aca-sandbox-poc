from __future__ import annotations

import argparse
import ipaddress
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, field_validator
from readability import Document

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 512 * 1024
MAX_REDIRECTS = 5
MIN_STATIC_CONTENT_CHARS = 500
METADATA_IPS = {ipaddress.ip_address("169.254.169.254")}


class ExtractOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: bool = True
    main_content: bool = True
    max_chars: int = Field(default=20_000, ge=1, le=100_000)
    include_links: bool = False


class WebFetchJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    render_mode: Literal["auto", "static", "browser"] = "auto"
    extract: ExtractOptions = Field(default_factory=ExtractOptions)
    timeout_seconds: int = Field(default=30, ge=1, le=120)
    output: Literal["homepage.json"] = "homepage.json"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        validate_public_url(value)
        return value


class FetchResult(BaseModel):
    url: str
    final_url: str
    status_code: int
    fetched_at: str
    rendered_with: Literal["static", "browser"]
    title: str = ""
    content: str = ""
    content_truncated: bool = False
    links: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute http(s) URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed")

    try:
        addresses = {
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
        }
    except socket.gaierror as exc:
        raise ValueError("URL host could not be resolved") from exc

    if not addresses:
        raise ValueError("URL host did not resolve to an address")
    for address in addresses:
        if address in METADATA_IPS or not address.is_global:
            raise ValueError(f"URL resolves to a non-public address: {address}")


def _read_limited_response(response: httpx.Response) -> bytes:
    content_length = response.headers.get("content-length")
    if content_length and int(content_length) > MAX_RESPONSE_BYTES:
        raise ValueError("Response body exceeds the configured size limit")
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise ValueError("Response body exceeds the configured size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _extract_html(html: str, final_url: str, job: WebFetchJob) -> dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if job.extract.main_content:
        extracted_html = Document(html).summary()
        content = BeautifulSoup(extracted_html, "lxml").get_text(" ", strip=True)
    else:
        content = ""
    links = []
    if job.extract.include_links:
        links = [
            urljoin(final_url, anchor["href"])
            for anchor in soup.select("a[href]")[:200]
        ]
    truncated = len(content) > job.extract.max_chars
    return {
        "title": title if job.extract.title else "",
        "content": content[: job.extract.max_chars],
        "content_truncated": truncated,
        "links": links,
        "html": html,
    }


def fetch_static(job: WebFetchJob) -> FetchResult:
    current_url = job.url
    with httpx.Client(timeout=job.timeout_seconds, follow_redirects=False) as client:
        for redirect_count in range(MAX_REDIRECTS + 1):
            validate_public_url(current_url)
            with client.stream(
                "GET",
                current_url,
                headers={"User-Agent": "ai-abp-sum-site/1.0"},
            ) as response:
                if response.is_redirect:
                    if redirect_count == MAX_REDIRECTS:
                        raise ValueError("Too many redirects")
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect response is missing Location")
                    current_url = urljoin(current_url, location)
                    validate_public_url(current_url)
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type:
                    raise ValueError("Only HTML responses are supported")
                body = _read_limited_response(response)
                html = body.decode(response.encoding or "utf-8", errors="replace")
                extracted = _extract_html(html, str(response.url), job)
                return FetchResult(
                    url=job.url,
                    final_url=str(response.url),
                    status_code=response.status_code,
                    fetched_at=datetime.now(UTC).isoformat(),
                    rendered_with="static",
                    title=extracted["title"],
                    content=extracted["content"],
                    content_truncated=extracted["content_truncated"],
                    links=extracted["links"],
                    warnings=[],
                )
    raise ValueError("Fetch did not produce a response")


def fetch_browser(job: WebFetchJob) -> FetchResult:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=False)
        page = context.new_page()

        def validate_route(route: Any) -> None:
            try:
                validate_public_url(route.request.url)
            except ValueError:
                route.abort("blockedbyclient")
            else:
                route.continue_()

        page.route("**/*", validate_route)
        response = page.goto(
            job.url,
            wait_until="domcontentloaded",
            timeout=job.timeout_seconds * 1000,
        )
        if response is None:
            raise ValueError("Browser navigation returned no response")
        validate_public_url(page.url)
        html = page.content()
        if len(html.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError("Rendered page exceeds the configured size limit")
        extracted = _extract_html(html, page.url, job)
        result = FetchResult(
            url=job.url,
            final_url=page.url,
            status_code=response.status,
            fetched_at=datetime.now(UTC).isoformat(),
            rendered_with="browser",
            title=extracted["title"],
            content=extracted["content"],
            content_truncated=extracted["content_truncated"],
            links=extracted["links"],
            warnings=[],
        )
        context.close()
        browser.close()
        return result


def _looks_like_spa_shell(result: FetchResult) -> bool:
    if len(result.content.strip()) >= MIN_STATIC_CONTENT_CHARS and result.title:
        return False
    markers = ("__NEXT_DATA__", "webpackJsonp", 'id="root"', 'id="app"', "nuxt", "vite")
    combined = f"{result.title} {result.content}"
    return len(result.content.strip()) < MIN_STATIC_CONTENT_CHARS or any(
        marker.lower() in combined.lower() for marker in markers
    )


def run_job(
    job: WebFetchJob,
    *,
    static_fetcher: Callable[[WebFetchJob], FetchResult] | None = None,
    browser_fetcher: Callable[[WebFetchJob], FetchResult] | None = None,
) -> FetchResult:
    static_fetcher = static_fetcher or fetch_static
    browser_fetcher = browser_fetcher or fetch_browser
    if job.render_mode == "browser":
        return browser_fetcher(job)
    static_result = static_fetcher(job)
    if job.render_mode == "auto" and _looks_like_spa_shell(static_result):
        return browser_fetcher(job)
    return static_result


def _write_output(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        raise ValueError("Output JSON exceeds the configured size limit")
    path.write_bytes(encoded)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job_path", type=Path)
    parser.add_argument("output_path", type=Path)
    args = parser.parse_args()
    try:
        job = WebFetchJob.model_validate_json(args.job_path.read_text(encoding="utf-8"))
        _write_output(args.output_path, run_job(job).model_dump(mode="json"))
        return 0
    except Exception as exc:
        _write_output(
            args.output_path,
            {"error": "fetch_failed", "message": str(exc), "warnings": []},
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
