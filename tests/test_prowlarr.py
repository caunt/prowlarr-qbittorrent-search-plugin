"""Offline unit and real loopback-HTTP integration tests; no trackers or real keys."""

import contextlib
import gzip
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prowlarr as plugin

KEY = "test-key-never-a-real-secret"
MAGNET = "magnet:?xt=urn:btih:" + "a" * 40
TORRENT = b'd4:infod6:lengthi0e4:name10:ubuntu.iso12:piece lengthi16384e6:pieces0:ee'


def settings(**changes):
    values = dict(plugin.DEFAULTS, api_key=KEY, use_proxy=False)
    values.update(changes)
    values["indexer_ids"] = tuple(values["indexer_ids"])
    return plugin.Settings(**values)


def indexer(identity=1, **changes):
    return dict({"id": identity, "name": f"Indexer {identity}", "protocol": "torrent",
                 "enable": True, "supportsSearch": True, "supportsPagination": True}, **changes)


def release(identity="release-1", **changes):
    return dict({"title": "Ubuntu | Linux", "guid": identity, "magnetUrl": MAGNET,
                 "size": 1234, "seeders": 10, "leechers": 2,
                 "publishDate": "2026-01-02T03:04:05Z"}, **changes)


class Server:
    def __init__(self, routes):
        self.routes = routes
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append((self.path, dict(self.headers.items())))
                status, headers, body = outer.routes(self)
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                    headers = {"Content-Type": "application/json", **headers}
                elif isinstance(body, str):
                    body = body.encode()
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def run_search(config, query="Ubuntu", category="all", printer=None):
    rows = []
    engine = plugin.prowlarr(printer or rows.append)
    with patch.object(plugin.Settings, "load", return_value=config), contextlib.redirect_stderr(io.StringIO()):
        engine.search(quote(query, safe=""), category)
    return engine, rows


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name, "prowlarr.json")
        self.env = patch.dict(os.environ, {"PROWLARR_CONFIG": str(self.path)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.directory.cleanup)

    def write(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")

    def test_minimal_config_merges_defaults(self):
        self.write({"api_key": KEY})
        config = plugin.Settings.load()
        self.assertEqual(config.workers, 8)
        self.assertEqual(config.indexer_ids, ())

    def test_creates_template_not_secret(self):
        with self.assertRaisesRegex(plugin.PluginError, "Set your Prowlarr"):
            plugin.Settings.load()
        self.assertEqual(json.loads(self.path.read_text()), plugin.DEFAULTS)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_environment_only_does_not_write_secret(self):
        os.environ["PROWLARR_API_KEY"] = KEY
        self.assertEqual(plugin.Settings.load().api_key, KEY)
        self.assertFalse(self.path.exists())

    def test_environment_overrides_file(self):
        self.write({"api_key": "old", "url": "http://localhost:9696"})
        os.environ.update(PROWLARR_API_KEY=KEY, PROWLARR_URL="https://example.test/prowlarr/")
        config = plugin.Settings.load()
        self.assertEqual(config.api_key, KEY)
        self.assertEqual(config.url, "https://example.test/prowlarr")
        self.assertEqual(json.loads(self.path.read_text())["api_key"], "old")

    def test_malformed_config_not_overwritten(self):
        self.path.write_text('{"api_key": ', encoding="utf-8")
        with self.assertRaisesRegex(plugin.PluginError, "Invalid JSON"):
            plugin.Settings.load()
        self.assertEqual(self.path.read_text(), '{"api_key": ')

    def test_bom_config(self):
        self.path.write_text(json.dumps({"api_key": KEY}), encoding="utf-8-sig")
        self.assertEqual(plugin.Settings.load().api_key, KEY)

    def test_config_must_be_object(self):
        self.write([])
        with self.assertRaisesRegex(plugin.PluginError, "JSON object"):
            plugin.Settings.load()

    def test_unknown_setting(self):
        self.write({"api_key": KEY, "workres": 3})
        with self.assertRaisesRegex(plugin.PluginError, "Unknown configuration"):
            plugin.Settings.load()

    def test_invalid_config_values(self):
        cases = [("workers", 0), ("workers", True), ("workers", 33), ("timeout", 0),
                 ("max_pages", "5"), ("page_size", -1), ("tracker_first", "false"),
                 ("use_proxy", 1), ("indexer_ids", [True]), ("indexer_ids", [-2]),
                 ("indexer_ids", "all"), ("api_key", ""), ("api_key", "YOUR_API_KEY_HERE"),
                 ("api_key", "bad\r\nheader"), ("api_key", "bad\x00key"), ("api_key", "ключ"),
                 ("url", "file:///tmp/test"), ("url", "http://user:pass@localhost"),
                 ("url", "http://localhost/?apikey=secret"), ("url", "http://localhost/#fragment"),
                 ("url", "http://localhost:0"), ("url", "http://localhost:99999"),
                 ("url", None), ("ca_file", None)]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                self.write(dict({"api_key": KEY}, **{name: value}))
                with self.assertRaises(plugin.PluginError):
                    plugin.Settings.load()

    def test_deduplicates_selected_ids(self):
        self.write({"api_key": KEY, "indexer_ids": [2, 1, 2]})
        self.assertEqual(plugin.Settings.load().indexer_ids, (2, 1))

    def test_import_has_no_configuration_side_effect(self):
        spec = importlib.util.spec_from_file_location("import_probe", ROOT / "prowlarr.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"import_probe": module}):
            spec.loader.exec_module(module)
        self.assertEqual(module.prowlarr.url, plugin.PROJECT_URL)
        self.assertFalse(self.path.exists())


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.client = plugin.Client(settings())

    def row(self, **changes):
        return plugin._release(release(**changes), indexer(), self.client)

    def test_standard_fields_and_date(self):
        row = self.row()
        self.assertEqual((row["size"], row["seeds"], row["leech"]), (1234, 10, 2))
        self.assertEqual(row["pub_date"], 1767323045)
        self.assertEqual(row["engine_url"], plugin.prowlarr.url)

    def test_magnet_names_are_url_encoded(self):
        self.assertIn("dn=My%20Linux%20%7C%20OS", self.row(magnetUrl=MAGNET + "&dn=My Linux | OS")["link"])

    def test_names_are_not_preencoded(self):
        self.assertEqual(self.row(title="A | B %7C Книга\nNext")["name"], "A | B %7C Книга Next [Indexer 1]")

    def test_null_download_falls_back_to_magnet(self):
        self.assertEqual(self.row(downloadUrl=None)["link"], MAGNET)

    def test_prefers_real_magnet_over_proxy(self):
        self.assertEqual(self.row(downloadUrl="http://127.0.0.1:9696/1/download")["link"], MAGNET)

    def test_proxied_magnet_url(self):
        row = self.row(downloadUrl=None, magnetUrl="http://127.0.0.1:9696/1/download?apikey=" + KEY + "&link=a%2Fb")
        self.assertEqual(row["link"], "http://127.0.0.1:9696/1/download?link=a%2Fb")

    def test_invalid_rows_skipped(self):
        for item in (None, [], 1, {}, release(title=""), release(downloadUrl=None, magnetUrl=None),
                     release(magnetUrl="file:///etc/passwd"), release(magnetUrl="javascript:alert(1)")):
            with self.subTest(item=item):
                self.assertIsNone(plugin._release(item, indexer(), self.client))

    def test_null_invalid_counts_are_unknown(self):
        row = self.row(size=None, seeders="bad", leechers=True)
        self.assertEqual((row["size"], row["seeds"], row["leech"]), (-1, -1, -1))
        self.assertEqual(self.row(seeders=0)["seeds"], 0)

    def test_description_fallback_and_pipe_safety(self):
        self.assertEqual(self.row(infoUrl=None, guid="https://example.test/a|b")["desc_link"], "https://example.test/a%7Cb")
        self.assertEqual(self.row(infoUrl=None, guid=None)["desc_link"], "")

    def test_tracker_first(self):
        self.client = plugin.Client(settings(tracker_first=True))
        self.assertTrue(self.row()["name"].startswith("[Indexer 1] "))

    def test_dates_missing_invalid_timezone_and_fractional(self):
        self.assertEqual(plugin._published("2026-01-02T05:04:05+02:00"), 1767323045)
        self.assertEqual(plugin._published("2026-01-02T03:04:05.9999999Z"), 1767323045)
        self.assertEqual(plugin._published("2026-01-02T03:04:05"), 1767323045)
        self.assertEqual(plugin._published(None), -1)
        self.assertEqual(plugin._published("not a date"), -1)

    def test_v1_v2_and_base32_magnets(self):
        self.assertTrue(plugin._magnet(MAGNET))
        self.assertTrue(plugin._magnet("magnet:?xt=urn:btih:" + "A" * 32))
        self.assertTrue(plugin._magnet("magnet:?xt=urn:btmh:1220" + "a" * 64))
        self.assertFalse(plugin._magnet("magnet:?dn=NoHash"))
        self.assertFalse(plugin._magnet(MAGNET + "\nInjected"))


class HTTPTests(unittest.TestCase):
    def test_authentication_header_not_query(self):
        with Server(lambda request: (200, {}, [])) as server:
            plugin.Client(settings(url=server.url)).get_json("indexer")
            path, headers = server.requests[0]
            self.assertEqual(headers.get("X-Api-Key"), KEY)
            self.assertNotIn(KEY, path)

    def test_reverse_proxy_base_path(self):
        with Server(lambda request: (200, {}, [])) as server:
            client = plugin.Client(settings(url=server.url + "/prowlarr"))
            client.get_json("search", [("query", "Ubuntu")])
            self.assertEqual(server.requests[0][0], "/prowlarr/api/v1/search?query=Ubuntu")
            self.assertFalse(client.trusted(server.url + "/other"))
            self.assertFalse(client.trusted(server.url + "/prowlarr-other"))

    def test_traversal_cannot_escape_authenticated_base_path(self):
        client = plugin.Client(settings(url="https://example.test/prowlarr"))
        self.assertFalse(client.trusted("https://example.test/prowlarr/../other"))
        self.assertFalse(client.trusted("https://example.test/prowlarr/%2e%2e/other"))
        self.assertTrue(client.trusted("https://example.test/prowlarr/1/download"))

    def test_api_redirect_cannot_leak_credentials(self):
        with Server(lambda request: (200, {}, [])) as target:
            with Server(lambda request: (302, {"Location": target.url + "/steal"}, b"")) as source:
                with self.assertRaisesRegex(plugin.PluginError, "redirect left"):
                    plugin.Client(settings(url=source.url)).get_json("indexer")
            self.assertEqual(target.requests, [])

    def test_download_redirect_strips_prowlarr_header_and_query_key(self):
        with Server(lambda request: (200, {}, TORRENT)) as target:
            location = target.url + "/torrent?apikey=" + KEY + "&signature=x%2fy%2Bz"
            with Server(lambda request: (302, {"Location": location}, b"")) as source:
                client = plugin.Client(settings(url=source.url))
                self.assertEqual(client.read(source.url + "/1/download?apikey=" + KEY), TORRENT)
                self.assertEqual(source.requests[0][1].get("X-Api-Key"), KEY)
            path, headers = target.requests[0]
            self.assertNotIn("X-Api-Key", headers)
            self.assertNotIn(KEY, path)
            self.assertIn("signature=x%2fy%2Bz", path)

    def test_tracker_own_key_not_removed(self):
        client = plugin.Client(settings())
        self.assertEqual(client.clean_link("https://tracker.test/t?apikey=trackerkey&x=%2f"),
                         "https://tracker.test/t?apikey=trackerkey&x=%2f")

    def test_relative_redirect_retains_auth_and_cookies(self):
        def route(request):
            if request.path.endswith("/start"):
                return 302, {"Location": "/end", "Set-Cookie": "session=ok; Path=/"}, b""
            return 200, {}, []
        with Server(route) as server:
            client = plugin.Client(settings(url=server.url))
            self.assertEqual(client.get_json("start"), [])
            headers = server.requests[1][1]
            self.assertEqual(headers.get("X-Api-Key"), KEY)
            self.assertEqual(headers.get("Cookie"), "session=ok")

    def test_magnet_redirect(self):
        with Server(lambda request: (302, {"Location": MAGNET}, b"")) as server:
            self.assertEqual(plugin.Client(settings(url=server.url)).read(server.url), MAGNET)

    def test_redirect_loop(self):
        with Server(lambda request: (302, {"Location": request.path}, b"")) as server:
            with self.assertRaisesRegex(plugin.PluginError, "Redirect loop"):
                plugin.Client(settings(url=server.url)).read(server.url + "/start")

    def test_file_redirect_rejected(self):
        with Server(lambda request: (302, {"Location": "file:///etc/passwd"}, b"")) as server:
            with self.assertRaises(plugin.PluginError):
                plugin.Client(settings(url=server.url)).read(server.url)

    def test_https_downgrade_rejected(self):
        error = HTTPError("https://example.test/t", 302, "redirect", {"Location": "http://example.test/t"}, io.BytesIO())
        opener = Mock()
        opener.open.side_effect = error
        with patch.object(plugin, "build_opener", return_value=opener):
            with self.assertRaisesRegex(plugin.PluginError, "HTTPS-to-HTTP"):
                plugin.Client(settings(url="https://example.test")).read("https://example.test/t")

    def test_error_bodies_not_leaked(self):
        for status in (400, 401, 403, 404, 429, 500, 503):
            with self.subTest(status=status), Server(lambda request: (status, {}, "APIKEY=" + KEY)) as server:
                with self.assertRaises(plugin.PluginError) as caught:
                    plugin.Client(settings(url=server.url)).get_json("indexer")
                self.assertIn(str(status), str(caught.exception))
                self.assertNotIn(KEY, str(caught.exception))
                self.assertEqual(len(server.requests), 1)

    def test_invalid_json(self):
        with Server(lambda request: (200, {}, "<html>login</html>")) as server:
            with self.assertRaisesRegex(plugin.PluginError, "invalid JSON"):
                plugin.Client(settings(url=server.url)).get_json("indexer")

    def test_gzip_json(self):
        with Server(lambda request: (200, {"Content-Encoding": "gzip"}, gzip.compress(b"[]"))) as server:
            self.assertEqual(plugin.Client(settings(url=server.url)).get_json("indexer"), [])

    def test_response_size_bounded(self):
        with patch.object(plugin, "_MAX_JSON", 32), Server(lambda request: (200, {}, b"a" * 33)) as server:
            with self.assertRaisesRegex(plugin.PluginError, "size limit"):
                plugin.Client(settings(url=server.url)).get_json("indexer")

    def test_gzip_expansion_bounded(self):
        with patch.object(plugin, "_MAX_JSON", 64), Server(lambda request: (200, {"Content-Encoding": "gzip"}, gzip.compress(b"a" * 1000))) as server:
            with self.assertRaisesRegex(plugin.PluginError, "size limit"):
                plugin.Client(settings(url=server.url)).get_json("indexer")

    def test_timeout(self):
        def slow(request):
            time.sleep(0.1)
            return 200, {}, []
        with Server(slow) as server:
            with self.assertRaisesRegex(plugin.PluginError, "timed out"):
                plugin.Client(settings(url=server.url, timeout=0.01)).get_json("indexer")

    def test_proxy_state_restored(self):
        helpers = Mock()
        with patch.dict(sys.modules, {"helpers": helpers}), patch.dict(os.environ, {"qbt_socks_proxy": "socks5://proxy"}):
            with self.assertRaises(ValueError):
                with plugin._proxy_policy(settings(use_proxy=False)):
                    raise ValueError
        self.assertEqual([call.args[0] for call in helpers.enable_socks_proxy.call_args_list], [False, True])


class SearchTests(unittest.TestCase):
    def test_query_encoding_and_repeated_categories(self):
        query = "C++ & C# 100% / Україна | тест"
        def route(request):
            return (200, {}, [indexer()]) if request.path == "/api/v1/indexer" else (200, {}, [release()])
        with Server(route) as server:
            engine, rows = run_search(settings(url=server.url), query, "games")
            self.assertFalse(engine.had_errors)
            params = parse_qs(urlsplit(server.requests[1][0]).query)
            self.assertEqual(params["query"], [query])
            self.assertEqual(params["categories"], ["1000", "4000"])
            self.assertEqual(params["limit"], ["100"])
            self.assertEqual(len(rows), 1)

    def test_books_use_books_category_not_other(self):
        self.assertEqual(plugin.CATEGORIES["books"], (7000,))

    def test_skips_disabled_usenet_and_unsearchable_indexers(self):
        items = [indexer(1), indexer(2, enable=False), indexer(3, protocol="usenet"), indexer(4, supportsSearch=False)]
        with patch.object(plugin.Client, "get_json", return_value=items):
            self.assertEqual([i["id"] for i in plugin.Client(settings()).indexers()], [1])

    def test_selected_indexers(self):
        with patch.object(plugin.Client, "get_json", return_value=[indexer(1), indexer(2)]):
            self.assertEqual([i["id"] for i in plugin.Client(settings(indexer_ids=[2])).indexers()], [2])

    def test_missing_selected_indexer_is_explicit(self):
        with patch.object(plugin.Client, "get_json", return_value=[indexer(1)]):
            with self.assertRaisesRegex(plugin.PluginError, "missing, disabled"):
                plugin.Client(settings(indexer_ids=[2])).indexers()

    def test_no_indexers_error(self):
        with patch.object(plugin.Client, "get_json", return_value=[]):
            with self.assertRaisesRegex(plugin.PluginError, "No enabled"):
                plugin.Client(settings()).indexers()

    def test_partial_failure_preserves_results(self):
        def route(request):
            if request.path == "/api/v1/indexer":
                return 200, {}, [indexer(1), indexer(2)]
            params = parse_qs(urlsplit(request.path).query)
            return (500, {}, KEY) if params["indexerIds"] == ["2"] else (200, {}, [release()])
        with Server(route) as server:
            engine, rows = run_search(settings(url=server.url))
        self.assertTrue(engine.had_errors)
        self.assertEqual(sum(row["link"].startswith("magnet:") for row in rows), 1)
        self.assertIn("1/2 indexers failed", rows[-1]["name"])
        self.assertNotIn(KEY, json.dumps(rows))

    def test_empty_results_are_not_errors(self):
        def route(request):
            return 200, {}, [indexer()] if request.path == "/api/v1/indexer" else []
        with Server(route) as server:
            engine, rows = run_search(settings(url=server.url))
        self.assertFalse(engine.had_errors)
        self.assertEqual(rows, [])

    def test_pagination_and_repeat_guard(self):
        def route(request):
            return 200, {}, [indexer()] if request.path == "/api/v1/indexer" else [release()]
        with Server(route) as server:
            engine, rows = run_search(settings(url=server.url, page_size=1, max_pages=10))
            self.assertEqual(len(server.requests), 3)  # discovery + first + repeated page
        self.assertEqual(len(rows), 1)

    def test_pagination_offsets_and_max_pages(self):
        def route(request):
            if request.path == "/api/v1/indexer":
                return 200, {}, [indexer()]
            offset = parse_qs(urlsplit(request.path).query)["offset"][0]
            return 200, {}, [release(identity=offset)]
        with Server(route) as server:
            engine, rows = run_search(settings(url=server.url, page_size=1, max_pages=3))
            offsets = [parse_qs(urlsplit(path).query)["offset"][0] for path, _ in server.requests[1:]]
        self.assertEqual(offsets, ["0", "1", "2"])
        self.assertEqual(len(rows), 3)

    def test_nonpagination_indexer_searched_once(self):
        def route(request):
            return 200, {}, [indexer(supportsPagination=False)] if request.path == "/api/v1/indexer" else [release()]
        with Server(route) as server:
            run_search(settings(url=server.url, page_size=1, max_pages=3))
            self.assertEqual(len(server.requests), 2)

    def test_same_torrent_on_different_trackers_not_collapsed(self):
        def route(request):
            return 200, {}, [indexer(1), indexer(2)] if request.path == "/api/v1/indexer" else [release()]
        with Server(route) as server:
            engine, rows = run_search(settings(url=server.url))
        self.assertEqual(len(rows), 2)

    def test_fast_results_stream_before_slow_indexer_completes(self):
        printed = threading.Event()
        streamed = []
        def route(request):
            if request.path == "/api/v1/indexer":
                return 200, {}, [indexer(1), indexer(2)]
            if parse_qs(urlsplit(request.path).query)["indexerIds"] == ["1"]:
                streamed.append(printed.wait(timeout=2))
            return 200, {}, [release()]
        with Server(route) as server:
            run_search(settings(url=server.url, workers=2), printer=lambda row: printed.set())
        self.assertEqual(streamed, [True])

    def test_worker_limit(self):
        lock = threading.Lock()
        active = maximum = 0
        def route(request):
            nonlocal active, maximum
            if request.path == "/api/v1/indexer":
                return 200, {}, [indexer(i) for i in range(1, 9)]
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.02)
            with lock:
                active -= 1
            return 200, {}, [release()]
        with Server(route) as server:
            engine, rows = run_search(settings(url=server.url, workers=2))
        self.assertLessEqual(maximum, 2)
        self.assertEqual(len(rows), 8)

    def test_broken_output_does_not_deadlock_full_queue(self):
        def route(request):
            return 200, {}, [indexer()] if request.path == "/api/v1/indexer" else [release(identity=str(i)) for i in range(600)]
        def broken(row):
            raise BrokenPipeError
        finished = threading.Event()
        with Server(route) as server:
            def invoke():
                try:
                    run_search(settings(url=server.url), printer=broken)
                except BrokenPipeError:
                    finished.set()
            thread = threading.Thread(target=invoke, daemon=True)
            thread.start()
            self.assertTrue(finished.wait(3), "search deadlocked after its output pipe closed")
            thread.join(timeout=1)


class DownloadTests(unittest.TestCase):
    def download(self, config, url):
        output = io.StringIO()
        with patch.object(plugin.Settings, "load", return_value=config), contextlib.redirect_stdout(output):
            plugin.prowlarr().download_torrent(url)
        return output.getvalue().strip()

    def test_raw_magnet_does_not_need_configuration_or_http(self):
        with patch.object(plugin.Settings, "load", side_effect=AssertionError("must not read config")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                plugin.prowlarr().download_torrent(MAGNET)
            self.assertEqual(output.getvalue().strip(), MAGNET + " " + MAGNET)

    def test_torrent_downloaded_once_and_saved_without_modification(self):
        with Server(lambda request: (200, {}, TORRENT)) as server:
            output = self.download(settings(url=server.url), server.url + "/1/download?apikey=" + KEY)
            path, _, url = output.rpartition(" ")
            try:
                self.assertEqual(Path(path).read_bytes(), TORRENT)
                self.assertNotIn(KEY, output)
                self.assertEqual(len(server.requests), 1)
            finally:
                Path(path).unlink()

    def test_text_magnet(self):
        with Server(lambda request: (200, {}, MAGNET + "\n")) as server:
            output = self.download(settings(url=server.url), server.url)
            self.assertEqual(output, MAGNET + " " + server.url)

    def test_bom_text_magnet(self):
        with Server(lambda request: (200, {}, b"\xef\xbb\xbf" + MAGNET.encode() + b"\n")) as server:
            output = self.download(settings(url=server.url), server.url)
            self.assertEqual(output, MAGNET + " " + server.url)

    def test_redirect_magnet(self):
        with Server(lambda request: (302, {"Location": MAGNET}, b"")) as server:
            output = self.download(settings(url=server.url), server.url)
            self.assertEqual(output, MAGNET + " " + server.url)

    def test_html_download_rejected_without_file(self):
        with Server(lambda request: (200, {}, "<html>login</html>")) as server, patch.object(plugin.tempfile, "mkstemp") as mkstemp:
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                self.download(settings(url=server.url), server.url)
            mkstemp.assert_not_called()

    def test_bencode_validation(self):
        plugin._validate_torrent(TORRENT)
        bad = [b"", b"de", b"<html>", b"d4:info5:wrong e", b"d4:infod", TORRENT + b"junk",
               b"d4:infod1:ali-1e", b"d4:info" + b"l" * 70 + b"e" * 71,
               b"d99999999999999999999999999999999:boom", b"d4:infod1:xiBADeee"]
        for data in bad:
            with self.subTest(data=data[:40]), self.assertRaises(plugin.PluginError):
                plugin._validate_torrent(data)


class CLITests(unittest.TestCase):
    def test_check_without_qbittorrent(self):
        def route(request):
            return 200, {}, {"version": "test-version"} if request.path.endswith("system/status") else [indexer()]
        with Server(route) as server:
            env = dict(os.environ, PROWLARR_API_KEY=KEY, PROWLARR_URL=server.url)
            with tempfile.TemporaryDirectory() as directory:
                env["PROWLARR_CONFIG"] = str(Path(directory, "missing.json"))
                result = subprocess.run([sys.executable, str(ROOT / "prowlarr.py"), "--check"],
                                        capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["prowlarr_version"], "test-version")
        self.assertNotIn(KEY, result.stdout + result.stderr)

    def test_python39_syntax(self):
        import ast
        ast.parse((ROOT / "prowlarr.py").read_text(), feature_version=(3, 9))

    def test_version_header_matches_runtime(self):
        self.assertEqual((ROOT / "prowlarr.py").read_text().splitlines()[0], "# VERSION: " + plugin.VERSION)

    def test_qbittorrent_version_format(self):
        self.assertRegex(plugin.VERSION, r"^\\d+\\.\\d+$")

    def test_default_timeout_is_120_seconds(self):
        self.assertEqual(plugin.DEFAULTS["timeout"], 120)
        self.assertEqual(json.loads((ROOT / "prowlarr.example.json").read_text())["timeout"], 120)


if __name__ == "__main__":
    unittest.main()
