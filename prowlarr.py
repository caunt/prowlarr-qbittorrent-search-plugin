# VERSION: 1.0.0
# AUTHORS: caunt (https://github.com/caunt)
# SPDX-License-Identifier: MIT
"""Single-file Prowlarr search engine for qBittorrent. Python 3.9+."""

from __future__ import annotations

import argparse
import gzip
import importlib
import io
import json
import os
import posixpath
import re
import socket
import ssl
import sys
import tempfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from threading import Event
from typing import Any, Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import (
    HTTPCookieProcessor, HTTPRedirectHandler, HTTPSHandler, ProxyHandler,
    Request, build_opener,
)

VERSION = "1.0.0"
PROJECT_URL = "https://github.com/caunt/prowlarr-qbittorrent-search-plugin"
DEFAULTS = {
    "url": "http://127.0.0.1:9696",
    "api_key": "YOUR_API_KEY_HERE",
    "tracker_first": False,
    "indexer_ids": [],
    "workers": 8,
    "timeout": 30,
    "page_size": 100,
    "max_pages": 1,
    "use_proxy": True,
    "ca_file": "",
}
CATEGORIES = {
    "all": (), "anime": (5070,), "books": (7000,), "games": (1000, 4000),
    "movies": (2000,), "music": (3000,), "software": (4000,), "tv": (5000,),
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_MAGNET_HASH = re.compile(r"^urn:(?:btih:(?:[a-f\d]{40}|[a-z2-7]{32})|btmh:1220[a-f\d]{64})$", re.I)
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_MAX_JSON = 16 * 1024 * 1024
_MAX_TORRENT = 32 * 1024 * 1024


class PluginError(Exception):
    """A diagnostic that is safe to display without credentials or response bodies."""


def _http_url(value: Any) -> str:
    if not isinstance(value, str) or not value or _CONTROL.search(value):
        raise PluginError("Invalid HTTP(S) URL.")
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username is not None or parts.password is not None):
            raise ValueError
        # Validate the port even when it is not otherwise needed.
        if parts.port is not None and parts.port < 1:
            raise ValueError
    except ValueError:
        raise PluginError("Use an HTTP(S) URL without embedded credentials.") from None
    return quote(value, safe=":/?#[]@!$&'()*+,;=%-._~")


def _origin(value: str) -> tuple[str, str, int]:
    parts = urlsplit(value)
    return parts.scheme, (parts.hostname or "").lower(), parts.port or (443 if parts.scheme == "https" else 80)


def _magnet(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("magnet:?") or _CONTROL.search(value):
        return False
    return any(key == "xt" and _MAGNET_HASH.fullmatch(item)
               for key, item in parse_qsl(urlsplit(value).query))


def _magnet_uri(value: str) -> str:
    return quote(value, safe=":/?[]@!$&'()*+,;=%-._~")


def _text(value: Any) -> str:
    return _CONTROL.sub(" ", str(value)) if value is not None else ""


def _number(value: Any) -> int:
    if isinstance(value, bool):
        return -1
    try:
        number = int(value)
        return number if number >= 0 else -1
    except (ValueError, TypeError, OverflowError):
        return -1


def _published(value: Any) -> int:
    if not isinstance(value, str):
        return -1
    try:
        # .NET timestamps may have seven fractional digits; Python 3.9 accepts
        # a narrower ISO format than newer versions. Normalize to microseconds.
        value = re.sub(r"\.(\d+)", lambda match: "." + (match[1] + "000000")[:6], value)
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(-1, int(date.timestamp()))
    except (ValueError, OverflowError, OSError):
        return -1


@dataclass(frozen=True)
class Settings:
    url: str
    api_key: str
    tracker_first: bool
    indexer_ids: tuple[int, ...]
    workers: int
    timeout: float
    page_size: int
    max_pages: int
    use_proxy: bool
    ca_file: str

    @classmethod
    def load(cls) -> Settings:
        path = Path(os.environ.get("PROWLARR_CONFIG") or Path(__file__).with_suffix(".json")).expanduser()
        try:
            with path.open(encoding="utf-8-sig") as handle:
                raw = handle.read(65537)
            if len(raw) > 65536:
                raise PluginError("The configuration file is larger than 64 KiB.")
            data = json.loads(raw)
        except FileNotFoundError:
            data = {}
            if not os.environ.get("PROWLARR_API_KEY"):
                try:
                    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        json.dump(DEFAULTS, handle, indent=4)
                        handle.write("\n")
                except FileExistsError:
                    return cls.load()
                except OSError:
                    raise PluginError(f"Cannot create configuration: {path}. Check directory permissions.") from None
                raise PluginError(f"Set your Prowlarr URL and API key in {path}.") from None
        except (ValueError, UnicodeError):
            raise PluginError(f"Invalid JSON in {path}; the file was not modified.") from None
        except OSError:
            raise PluginError(f"Cannot read configuration: {path}. Check file permissions.") from None

        if not isinstance(data, dict):
            raise PluginError("Configuration must be a JSON object.")
        unknown = data.keys() - DEFAULTS.keys()
        if unknown:
            raise PluginError("Unknown configuration setting(s): " + ", ".join(sorted(unknown)))
        values = dict(DEFAULTS, **data)
        for env, key in (("PROWLARR_URL", "url"), ("PROWLARR_API_KEY", "api_key")):
            if env in os.environ:
                values[key] = os.environ[env]
        for key in ("url", "api_key", "ca_file"):
            if not isinstance(values[key], str):
                raise PluginError(f"{key} must be a string.")
            values[key] = values[key].strip()
        values["url"] = _http_url(values["url"]).rstrip("/")
        if urlsplit(values["url"]).query or urlsplit(values["url"]).fragment:
            raise PluginError("url must be the Prowlarr base URL, without a query or fragment.")
        if (not values["api_key"] or values["api_key"].startswith("YOUR_")
                or not values["api_key"].isascii() or _CONTROL.search(values["api_key"])
                or any(c.isspace() for c in values["api_key"])):
            raise PluginError("Set api_key to your Prowlarr API key (Settings > General > Security).")
        for key in ("tracker_first", "use_proxy"):
            if type(values[key]) is not bool:
                raise PluginError(f"{key} must be true or false, not a string.")
        for key, low, high in (("workers", 1, 32), ("timeout", 1, 300),
                              ("page_size", 1, 1000), ("max_pages", 1, 100)):
            if type(values[key]) is not int or not low <= values[key] <= high:
                raise PluginError(f"{key} must be an integer between {low} and {high}.")
        ids = values["indexer_ids"]
        if not isinstance(ids, list) or any(type(item) is not int or item <= 0 for item in ids):
            raise PluginError("indexer_ids must be a list of positive integer IDs, or [] for all torrent indexers.")
        values["indexer_ids"] = tuple(dict.fromkeys(ids))
        values["ca_file"] = str(Path(values["ca_file"]).expanduser()) if values["ca_file"] else ""
        return cls(**values)


@contextmanager
def _proxy_policy(settings: Settings):
    """qBittorrent's SOCKS hook is process-global; never toggle it inside workers."""
    restore = None
    if not settings.use_proxy:
        try:
            helpers = importlib.import_module("helpers")
            toggle = getattr(helpers, "enable_socks_proxy", None)
            if toggle:
                toggle(False)
                restore = toggle
            elif os.environ.get("qbt_socks_proxy"):
                raise PluginError("This qBittorrent helper cannot disable SOCKS; update qBittorrent or use use_proxy=true.")
        except ImportError:
            if os.environ.get("qbt_socks_proxy"):
                raise PluginError("Cannot disable the qBittorrent SOCKS proxy: its helpers module is unavailable.") from None
    try:
        yield
    finally:
        if restore:
            restore(bool(os.environ.get("qbt_socks_proxy")))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Handle every redirect ourselves, including redirects to magnet: URIs.
        return None


class Client:
    def __init__(self, settings: Settings):
        self.settings = settings
        try:
            self.context = ssl.create_default_context(cafile=settings.ca_file or None)
        except (OSError, ssl.SSLError):
            raise PluginError("Cannot load ca_file; provide a readable PEM CA bundle.") from None

    def trusted(self, url: str) -> bool:
        """Credentials belong to the configured origin AND reverse-proxy base path."""
        parts = urlsplit(url)
        base = posixpath.normpath(unquote(urlsplit(self.settings.url).path) or "/").rstrip("/")
        path = posixpath.normpath(unquote(parts.path) or "/")
        return _origin(url) == _origin(self.settings.url) and (path == base or path.startswith(base + "/"))

    def clean_link(self, url: str) -> str:
        url = _http_url(url)
        parts = urlsplit(url)
        # Prowlarr emits API keys in its proxy URLs. Use a header instead. Do not
        # change other signed query parameters, their order, or their escaping.
        tokens = []
        for token in parts.query.split("&"):
            name, _, value = token.partition("=")
            if unquote(name).lower() == "apikey" and (self.trusted(url) or unquote(value) == self.settings.api_key):
                continue
            tokens.append(token)
        return urlunsplit(parts._replace(query="&".join(tokens)))

    def get_json(self, endpoint: str, params: Optional[list[tuple[str, Any]]] = None) -> Any:
        url = self.settings.url + "/api/v1/" + endpoint
        if params:
            url += "?" + urlencode(params)
        body = self.read(url, api=True)
        try:
            return json.loads(body.decode("utf-8-sig"))
        except (ValueError, UnicodeError, AttributeError):
            raise PluginError("Prowlarr returned invalid JSON. Check the base URL and reverse-proxy authentication.") from None

    def read(self, url: str, *, api: bool = False) -> Any:
        maximum = _MAX_JSON if api else _MAX_TORRENT
        handlers = [_NoRedirect(), HTTPCookieProcessor(CookieJar()), HTTPSHandler(context=self.context)]
        if not self.settings.use_proxy:
            handlers.append(ProxyHandler({}))
        opener = build_opener(*handlers)
        visited = set()
        for _ in range(6):
            if not api and _magnet(url):
                return _magnet_uri(url)
            url = self.clean_link(url)
            if api and not self.trusted(url):
                raise PluginError("API redirect left the configured Prowlarr origin/base path; update url to its final address.")
            if url in visited:
                raise PluginError("Redirect loop while contacting Prowlarr or downloading a torrent.")
            visited.add(url)
            headers = {"User-Agent": "prowlarr-qbittorrent-search-plugin/" + VERSION,
                       "Accept": "application/json" if api else "application/x-bittorrent, */*",
                       "Accept-Encoding": "identity"}
            request = Request(url, headers=headers)
            if self.trusted(url):
                request.add_unredirected_header("X-Api-Key", self.settings.api_key)
            try:
                with opener.open(request, timeout=self.settings.timeout) as response:
                    encoding = response.headers.get("Content-Encoding", "identity").lower()
                    body = response.read(maximum + 1)
                if len(body) > maximum:
                    raise PluginError("Response exceeds the safety size limit.")
                if encoding == "gzip" or body.startswith(b"\x1f\x8b"):
                    try:
                        with gzip.GzipFile(fileobj=io.BytesIO(body)) as compressed:
                            body = compressed.read(maximum + 1)
                    except (OSError, EOFError, zlib.error):
                        raise PluginError("The server returned an invalid gzip response.") from None
                elif encoding not in ("identity", ""):
                    raise PluginError("Unsupported response compression; configure the proxy to honor Accept-Encoding: identity.")
                if len(body) > maximum:
                    raise PluginError("Response exceeds the safety size limit.")
                return body
            except HTTPError as error:
                location = error.headers.get("Location") if error.headers else None
                code = error.code
                error.close()
                if code in _REDIRECT_CODES and location:
                    target = urljoin(url, location)
                    if urlsplit(url).scheme == "https" and urlsplit(target).scheme == "http":
                        raise PluginError("Refusing an HTTPS-to-HTTP download/API redirect.")
                    url = target
                    continue
                messages = {
                    400: "HTTP 400: request rejected; check this indexer's configuration and Prowlarr logs.",
                    401: "HTTP 401: authentication failed; check your Prowlarr API key or reverse proxy.",
                    403: "HTTP 403: access denied by Prowlarr, the reverse proxy, or the indexer.",
                    404: "HTTP 404: check the base URL, indexer ID, or repeat the search for a fresh download link.",
                    429: "HTTP 429: rate limited. Wait before searching/downloading again.",
                }
                raise PluginError(messages.get(code, f"HTTP {code}: server request failed; check Prowlarr logs.")) from None
            except (URLError, OSError) as error:
                reason = getattr(error, "reason", error)
                if isinstance(reason, ssl.SSLCertVerificationError):
                    raise PluginError("TLS certificate verification failed. Use a valid certificate or set ca_file.") from None
                if isinstance(reason, (TimeoutError, socket.timeout)):
                    raise PluginError("Request timed out. Check the indexer or increase timeout.") from None
                raise PluginError("Connection failed. Check the Prowlarr URL, network, and use_proxy setting.") from None
        raise PluginError("Too many redirects while contacting Prowlarr or downloading a torrent.")

    def indexers(self) -> list[dict[str, Any]]:
        data = self.get_json("indexer")
        if not isinstance(data, list):
            raise PluginError("Unexpected indexer response; expected a JSON array.")
        items = [item for item in data if isinstance(item, dict)
                 and item.get("enable") is True and item.get("supportsSearch", True) is not False
                 and str(item.get("protocol", "")).lower() in ("torrent", "2")
                 and type(item.get("id")) is int and item["id"] > 0]
        if self.settings.indexer_ids:
            selected = set(self.settings.indexer_ids)
            missing = selected - {item["id"] for item in items}
            if missing:
                raise PluginError("Selected indexer IDs are missing, disabled, or not searchable torrent indexers: "
                                  + ", ".join(map(str, sorted(missing))))
            items = [item for item in items if item["id"] in selected]
        if not items:
            raise PluginError("No enabled, searchable torrent indexers found in Prowlarr.")
        return items


def _release(result: Any, indexer: dict[str, Any], client: Client) -> Optional[dict[str, Any]]:
    if not isinstance(result, dict) or not isinstance(result.get("title"), str) or not result["title"].strip():
        return None
    magnet = result.get("magnetUrl")
    candidate = magnet if _magnet(magnet) else result.get("downloadUrl") or magnet
    try:
        link = _magnet_uri(candidate) if _magnet(candidate) else client.clean_link(candidate)
    except (PluginError, ValueError):
        return None
    title = _text(result["title"])
    tracker = _text(result.get("indexer") or indexer.get("name") or indexer["id"])
    name = f"[{tracker}] {title}" if client.settings.tracker_first else f"{title} [{tracker}]"
    description = ""
    for value in (result.get("infoUrl"), result.get("guid")):
        try:
            description = client.clean_link(value)
            break
        except (PluginError, ValueError):
            pass
    return {
        "link": link.replace("|", "%7C"), "name": name,
        "size": _number(result.get("size")), "seeds": _number(result.get("seeders")),
        "leech": _number(result.get("leechers")), "engine_url": PROJECT_URL,
        "desc_link": description.replace("|", "%7C"), "pub_date": _published(result.get("publishDate")),
    }


def _validate_torrent(data: bytes) -> None:
    """Validate bencode structure and a top-level info dictionary, without decoding it."""
    position = 0
    has_info = False

    def string() -> bytes:
        nonlocal position
        end = data.find(b":", position, position + 12)
        if end < 0 or not data[position:end].isdigit():
            raise ValueError
        length = int(data[position:end])
        position = end + 1
        if position + length > len(data):
            raise ValueError
        value = data[position:position + length]
        position += length
        return value

    def value(depth: int = 0) -> None:
        nonlocal position, has_info
        if depth > 64 or position >= len(data):
            raise ValueError
        kind = data[position:position + 1]
        if kind.isdigit():
            string()
            return
        position += 1
        if kind == b"i":
            end = data.find(b"e", position, position + 128)
            if end < 0 or not re.fullmatch(rb"-?(0|[1-9][0-9]*)", data[position:end]):
                raise ValueError
            position = end + 1
        elif kind in (b"l", b"d"):
            while data[position:position + 1] != b"e":
                if kind == b"d":
                    key = string()
                    if depth == 0 and key == b"info":
                        if data[position:position + 1] != b"d":
                            raise ValueError
                        has_info = True
                value(depth + 1)
            position += 1
        else:
            raise ValueError

    try:
        if not data.startswith(b"d"):
            raise ValueError
        value()
        if position != len(data) or not has_info:
            raise ValueError
    except (ValueError, IndexError):
        raise PluginError("The download is not a structurally valid .torrent file (possibly an HTML login/Cloudflare page).") from None


class prowlarr:
    # Keep this stable: qBittorrent uses engine_url to route downloads back here.
    name = "Prowlarr"
    url = PROJECT_URL
    supported_categories = {key: key for key in CATEGORIES}

    def __init__(self, printer: Optional[Callable[[dict[str, Any]], None]] = None):
        self.printer = printer
        self.had_errors = False

    def _emit(self, row: dict[str, Any]) -> None:
        if self.printer:
            self.printer(row)
        else:
            # Let the installed qBittorrent helper handle both legacy and modern
            # title escaping. Never pre-encode the name or replace its pipes.
            importlib.import_module("novaprinter").prettyPrinter(row)

    def _error(self, message: str, query: str) -> None:
        self.had_errors = True
        message = _text(message)
        print("Prowlarr: " + message, file=sys.stderr)
        self._emit({"link": PROJECT_URL + "#troubleshooting", "name": f"Prowlarr: {message} Search: {_text(query)}",
                    "size": -1, "seeds": -1, "leech": -1, "engine_url": self.url,
                    "desc_link": PROJECT_URL + "#troubleshooting", "pub_date": -1})

    def _search_indexer(self, client: Client, indexer: dict[str, Any], query: str,
                        categories: tuple[int, ...], emit: Callable[[dict[str, Any]], None]) -> None:
        seen = set()
        pages = client.settings.max_pages if indexer.get("supportsPagination", True) else 1
        for page in range(pages):
            params = [("query", query), ("type", "search"), ("indexerIds", indexer["id"]),
                      ("limit", client.settings.page_size), ("offset", page * client.settings.page_size)]
            params.extend(("categories", category) for category in categories)
            results = client.get_json("search", params)
            if not isinstance(results, list):
                raise PluginError("Unexpected search response; expected a JSON array.")
            fresh = 0
            for result in results:
                row = _release(result, indexer, client)
                if row is None:
                    continue
                identity = result.get("guid") or result.get("infoHash") or row["link"]
                if not isinstance(identity, str):
                    identity = row["link"]
                if identity in seen:
                    continue
                seen.add(identity)
                fresh += 1
                emit(row)
            if not fresh or len(results) < client.settings.page_size:
                break

    def search(self, what: str, cat: str = "all") -> None:
        # nova2 percent-encodes query text; unquote_plus would corrupt literal '+' signs.
        query = unquote(what)
        self.had_errors = False
        try:
            categories = CATEGORIES.get(cat.lower())
            if categories is None:
                raise PluginError("Unsupported search category.")
            settings = Settings.load()
            client = Client(settings)
            with _proxy_policy(settings):
                indexers = client.indexers()
                # Workers publish into a queue. Only the calling thread writes to
                # qBittorrent's pipe, so records cannot interleave or be buffered by
                # a slow indexer. A bounded queue also bounds pending result memory.
                from queue import Empty, Full, Queue
                queue = Queue(maxsize=256)
                stopped = Event()
                failures = []

                def publish(row):
                    while not stopped.is_set():
                        try:
                            queue.put(row, timeout=0.1)
                            return
                        except Full:
                            pass
                    raise PluginError("Search stopped.")

                with ThreadPoolExecutor(max_workers=min(settings.workers, len(indexers))) as pool:
                    futures = {pool.submit(self._search_indexer, client, item, query, categories, publish): item
                               for item in indexers}
                    pending = set(futures)
                    try:
                        while pending or not queue.empty():
                            try:
                                self._emit(queue.get(timeout=0.05))
                            except Empty:
                                pass
                            finished = {future for future in pending if future.done()}
                            for future in finished:
                                try:
                                    future.result()
                                except PluginError as error:
                                    message = f"{_text(futures[future].get('name') or futures[future]['id'])}: {error}"
                                    failures.append(message)
                                    print("Prowlarr: " + message, file=sys.stderr)
                                except Exception as error:
                                    # Exception text may contain credential-bearing URLs.
                                    failures.append(f"Indexer {futures[future]['id']}: unexpected {type(error).__name__}")
                            pending -= finished
                    finally:
                        stopped.set()
                        for future in pending:
                            future.cancel()
                if failures:
                    self._error(f"{len(failures)}/{len(indexers)} indexers failed. " + "; ".join(failures[:3]), query)
        except PluginError as error:
            self._error(str(error), query)

    def download_torrent(self, download_url: str) -> None:
        try:
            if _magnet(download_url):
                download_url = _magnet_uri(download_url)
                print(f"{download_url} {download_url}")
                return
            settings = Settings.load()
            client = Client(settings)
            with _proxy_policy(settings):
                data = client.read(download_url)
            if isinstance(data, str) and _magnet(data):
                print(f"{data} {client.clean_link(download_url)}")
                return
            # Some trackers serve a text magnet URI instead of torrent bytes.
            if data.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(b"magnet:?"):
                magnet = data.decode("utf-8-sig").strip()
                if not _magnet(magnet):
                    raise PluginError("The server returned an invalid magnet link.")
                print(f"{_magnet_uri(magnet)} {client.clean_link(download_url)}")
                return
            _validate_torrent(data)
            descriptor, path = tempfile.mkstemp(prefix="prowlarr-", suffix=".torrent")
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(data)
            except OSError:
                Path(path).unlink(missing_ok=True)
                raise
            # qBittorrent takes ownership of this temporary file after download.
            print(f"{path} {client.clean_link(download_url)}")
        except (PluginError, OSError, UnicodeError) as error:
            message = str(error) if isinstance(error, PluginError) else "Could not save or decode the downloaded torrent."
            print("Prowlarr: " + message, file=sys.stderr)
            raise SystemExit(1) from None


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="Path to prowlarr.json (also: PROWLARR_CONFIG)")
    parser.add_argument("--version", action="version", version=VERSION)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="Check configuration, API, and enabled torrent indexers")
    action.add_argument("--search", metavar="QUERY", help="Search and print JSON Lines without needing qBittorrent")
    parser.add_argument("--category", choices=CATEGORIES, default="all")
    args = parser.parse_args()
    if args.config:
        os.environ["PROWLARR_CONFIG"] = args.config
    if args.search is not None:
        engine = prowlarr(lambda row: print(json.dumps(row, ensure_ascii=False)))
        engine.search(quote(args.search, safe=""), args.category)
        return 1 if engine.had_errors else 0
    try:
        settings = Settings.load()
        with _proxy_policy(settings):
            client = Client(settings)
            status = client.get_json("system/status")
            indexers = client.indexers()
        print(json.dumps({"plugin_version": VERSION, "prowlarr_version": status.get("version") if isinstance(status, dict) else None,
                          "indexers": [{"id": item["id"], "name": item.get("name", "")} for item in indexers]}, indent=2))
        return 0
    except PluginError as error:
        print("Prowlarr: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(_main())
