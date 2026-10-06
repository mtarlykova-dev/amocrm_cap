"""Read-only client for amoCRM API v4 via the 4teenHub proxy.

The proxy lives at https://4teenhub.ru/amo-api/ and mirrors amoCRM API v4 paths.
Authentication is a personal key passed in the ``X-API-Key`` header; it is read
from the ``FOURTEENHUB_API_KEY`` environment variable and must never be committed.

Only GET is supported by the proxy, so this client exposes no write methods.

CLI usage:
    export FOURTEENHUB_API_KEY=...
    python amo_proxy.py leads --limit 5
    python amo_proxy.py leads/26644877
    python amo_proxy.py contacts --all -p query=Иван
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator, Mapping, Sequence, Union

Params = Union[Mapping[str, Any], Sequence[tuple[str, Any]]]

BASE_URL = "https://4teenhub.ru/amo-api/"
API_PREFIX = "api/v4/"
ENV_KEY = "FOURTEENHUB_API_KEY"

ALLOWED_SECTIONS = frozenset(
    {
        "account",
        "leads",
        "contacts",
        "companies",
        "tasks",
        "events",
        "notes",
        "pipelines",
        "users",
        "custom_fields",
        "tags",
        "customers",
        "segments",
    }
)


class AmoProxyError(Exception):
    """Non-2xx response from the proxy (or amoCRM passed through as-is)."""

    def __init__(self, status: int, url: str, body: Any):
        self.status = status
        self.url = url
        self.body = body
        detail = body.get("detail") if isinstance(body, dict) else body
        super().__init__(f"HTTP {status} for {url}: {detail}")


class AmoProxyClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        max_retries: int = 5,
        backoff: float = 1.0,
    ):
        api_key = api_key or os.environ.get(ENV_KEY)
        if not api_key:
            raise ValueError(f"API key not set: pass api_key or set ${ENV_KEY}")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff

    def url_for(self, path: str, params: Params | None = None) -> str:
        """Build a proxy URL from an amoCRM path like ``leads/1`` or ``/api/v4/leads``."""
        path = path.lstrip("/")
        if not path.startswith(API_PREFIX):
            path = API_PREFIX + path
        section = path[len(API_PREFIX) :].split("/", 1)[0].split("?", 1)[0]
        if section not in ALLOWED_SECTIONS:
            raise ValueError(
                f"Section {section!r} is not whitelisted by the proxy "
                f"(allowed: {', '.join(sorted(ALLOWED_SECTIONS))})"
            )
        url = self.base_url + path
        if params:
            sep = "&" if "?" in url else "?"
            url += sep + urllib.parse.urlencode(params, doseq=True)
        return url

    def get(self, path: str, params: Params | None = None) -> Any:
        """GET an amoCRM path through the proxy and return decoded JSON (None on 204)."""
        return self.get_url(self.url_for(path, params))

    def get_url(self, url: str) -> Any:
        """GET an absolute proxy URL, e.g. ``_links.next.href`` from a previous response."""
        if not url.startswith(self.base_url):
            raise ValueError(f"Refusing to send API key to non-proxy URL: {url}")
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"X-API-Key": self._api_key, "Accept": "application/json"},
        )
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    if resp.status == 204:
                        return None
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as err:
                if err.code == 429 and attempt < self.max_retries:
                    time.sleep(self._retry_delay(err, attempt))
                    attempt += 1
                    continue
                raise AmoProxyError(err.code, url, _decode_body(err.read())) from None

    def paginate(
        self, path: str, params: Params | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield every page of a list endpoint, following ``_links.next``."""
        page = self.get(path, params)
        while page:
            yield page
            next_url = page.get("_links", {}).get("next", {}).get("href")
            if not next_url:
                break
            page = self.get_url(next_url)

    def iter_items(
        self, path: str, params: Params | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield individual entities from ``_embedded`` across all pages."""
        for page in self.paginate(path, params):
            for items in page.get("_embedded", {}).values():
                if isinstance(items, list):
                    yield from items

    def _retry_delay(self, err: urllib.error.HTTPError, attempt: int) -> float:
        retry_after = err.headers.get("Retry-After") if err.headers else None
        if retry_after and retry_after.isdigit():
            return float(retry_after)
        return self.backoff * (2**attempt)


def _decode_body(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def _parse_params(pairs: list[str]) -> list[tuple[str, str]]:
    params = []
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise argparse.ArgumentTypeError(f"Expected key=value, got {pair!r}")
        params.append((key, value))
    return params


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only amoCRM API v4 access via the 4teenHub proxy"
    )
    parser.add_argument("path", help="amoCRM path, e.g. leads, leads/26644877, account")
    parser.add_argument("-p", "--param", action="append", default=[], help="key=value query param")
    parser.add_argument("--limit", type=int, help="shortcut for -p limit=N")
    parser.add_argument("--all", action="store_true", help="follow pagination, print all entities")
    args = parser.parse_args(argv)

    params = _parse_params(args.param)
    if args.limit is not None:
        params.append(("limit", str(args.limit)))

    try:
        client = AmoProxyClient()
        if args.all:
            result: Any = list(client.iter_items(args.path, params or None))
        else:
            result = client.get(args.path, params or None)
    except (ValueError, AmoProxyError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except OSError as err:  # URLError, timeouts, proxy/tunnel failures
        print(f"network error: {getattr(err, 'reason', err)}", file=sys.stderr)
        return 1

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
