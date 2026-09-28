# Prowlarr qBittorrent Search Plugin

Search your configured **Prowlarr torrent indexers directly from qBittorrent's Search tab**. One Python file, no Jackett instance, and no third-party Python packages to install.

[![Tests](https://github.com/caunt/prowlarr-qbittorrent-search-plugin/actions/workflows/tests.yml/badge.svg)](https://github.com/caunt/prowlarr-qbittorrent-search-plugin/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Install

In qBittorrent, open **Search → Search plugins… → Install a new one → Web link** and paste:

```text
https://raw.githubusercontent.com/caunt/prowlarr-qbittorrent-search-plugin/main/prowlarr.py
```

Enable the Search tab from **View → Search Engine** in the desktop application when necessary. The plugin also works through qBittorrent's Web UI search interface; Python and the configuration belong on the machine running qBittorrent, not the machine running your browser.

Run one search with **Prowlarr** selected. The plugin creates `prowlarr.json` alongside `prowlarr.py` and displays the exact configuration path in a result row. Edit that file:

```json
{
    "url": "http://127.0.0.1:9696",
    "api_key": "YOUR_API_KEY_HERE",
    "tracker_first": false
}
```

Get the key from **Prowlarr → Settings → General → Security → API Key**. Set `url` to Prowlarr's address **as seen by qBittorrent**. Include a reverse-proxy base path when used, for example `https://nas.example/prowlarr`; do not append `/api/v1`.

Save the file and search again. Configuration is read for each search/download, so editing it does not require restarting Prowlarr. At least one torrent indexer must be enabled and searchable in Prowlarr.

### Synology and Docker

For native qBittorrent and Prowlarr packages on the **same NAS**, `http://127.0.0.1:9696` is appropriate when Prowlarr listens on that port. To locate the generated configuration after your first search:

```sh
sudo find /var/packages /volume*/@appdata /volume*/@appstore -type f -path '*/nova3/engines/prowlarr.json' 2>/dev/null
```

Keep the file readable by the account running the qBittorrent package. Do not make it world-readable to work around permissions.

For **different containers**, `127.0.0.1` points back to the qBittorrent container, not the Prowlarr container. Use Prowlarr's service/container DNS name on a shared network, or a reachable NAS address and published port. Store the plugin and its JSON file in qBittorrent's persistent data volume.

### Moving from an older Prowlarr plugin

Both use the filename `prowlarr.py`. Back up `prowlarr.json`, uninstall the older plugin, and install the URL above. Restore the configuration if needed. Existing `url`, `api_key`, and `tracker_first` settings are supported; missing options get defaults. No code from the abandoned plugin is bundled here.

## Features

| Feature | Behavior |
| --- | --- |
| All torrent indexers | Discovers enabled, searchable torrent indexers from Prowlarr; excludes Usenet. |
| Parallel search | Uses up to eight workers by default. Results appear as they arrive, rather than waiting for the slowest indexer. |
| Partial-failure handling | Working indexers keep their results. Failures produce one summary row and diagnostics on stderr. |
| Categories | All, anime, books, games, movies, music, software, and TV. Multi-category requests use repeated parameters. |
| Unicode and punctuation | Correct query encoding, including literal `+`, `&`, `#`, `%`, and non-Latin text. |
| Pagination | Optional, bounded, per-indexer pagination. Repeated pages are detected; non-paginating indexers are queried once. |
| Result metadata | Size, seeds, leechers, tracker name, description link, and publication timestamp. |
| Downloads | Torrent files, direct v1/v2 magnets, HTTP redirects to magnets, and text magnet responses. |
| Configuration | Separate JSON file, environment overrides, validated settings, and custom CA bundles. |
| Diagnostics | Standalone configuration check and JSON Lines search, without launching qBittorrent. |

Repeated releases are deduplicated **within each indexer**. Results from different trackers are intentionally retained, including private-tracker provenance and different seed counts.

## Configuration

Only `api_key` is required when the default URL is correct. See [`prowlarr.example.json`](prowlarr.example.json) for all options.

| Setting | Default | Meaning |
| --- | --- | --- |
| `url` | `http://127.0.0.1:9696` | Prowlarr base URL, optionally including a reverse-proxy path. |
| `api_key` | Placeholder | Prowlarr API key. Never commit your real key. |
| `tracker_first` | `false` | Display `[Tracker] Title` instead of `Title [Tracker]`. |
| `indexer_ids` | `[]` | All enabled torrent indexers. Specify positive numeric Prowlarr IDs to select a subset. Invalid/unavailable selected IDs produce an explicit error. |
| `workers` | `8` | Concurrent indexer requests, from 1 to 32. Lower this for many slow indexers or a low-powered server. |
| `timeout` | `30` | Socket timeout in seconds, from 1 to 300. This is not a whole-search deadline. |
| `page_size` | `100` | Requested results per page per indexer, from 1 to 1000. Individual indexers may impose smaller limits. |
| `max_pages` | `1` | Maximum pages per indexer, from 1 to 100. Raise to 3 for deeper searches; this can multiply tracker requests. |
| `use_proxy` | `true` | Honor HTTP(S) proxy environment settings and qBittorrent's active SOCKS helper. `false` explicitly connects directly. |
| `ca_file` | `""` | Optional PEM CA bundle for HTTPS; empty uses system trust. Certificate verification is never disabled. |

Pagination stops on a short page, a page without new valid releases, or `max_pages`. Not every indexer honors offsets/limits. This plugin does not guarantee exhaustive results across all trackers and never retries rate-limited requests automatically.

Environment overrides:

```sh
export PROWLARR_URL='http://127.0.0.1:9696'
export PROWLARR_API_KEY='YOUR_API_KEY_HERE'
export PROWLARR_CONFIG='/path/to/prowlarr.json'
```

`PROWLARR_URL` and `PROWLARR_API_KEY` override JSON values. `PROWLARR_CONFIG` changes the configuration location. With an environment-provided key, a missing JSON file is allowed and no secret-bearing file is created. qBittorrent must inherit these variables; variables set in an unrelated SSH shell do not alter an already-running service.

## Check the setup without qBittorrent

Using a local checkout or the installed `prowlarr.py`:

```sh
python3 prowlarr.py --config /path/to/prowlarr.json --check
python3 prowlarr.py --config /path/to/prowlarr.json --search 'Ubuntu Linux' --category software
python3 prowlarr.py --version
```

`--check` prints the Prowlarr version and selected indexers, without the API key. `--search` prints one JSON object per result and exits nonzero if errors occurred; successful results are still printed when some indexers fail. These commands only read/search Prowlarr. They do not add, enable, or modify indexers or download clients.

The Python executable must meet the requirements of your qBittorrent build. The plugin itself uses Python **3.9+** syntax and standard-library modules. qBittorrent supplies its own `novaprinter` and optional SOCKS helper when running plugins.

## Troubleshooting

**No configuration file yet:** select this plugin and run one search. Importing the plugin alone deliberately performs no file writes or network requests. A setup-error result shows the path. Create the JSON manually when the engines directory is read-only, or set `PROWLARR_CONFIG` to a writable location.

**HTTP 401 / HTTP 403:** check the API key and any reverse-proxy authentication. A browser login session does not automatically authenticate this plugin. Embedded `username:password@host` URLs are intentionally rejected.

**Connection failed or timed out:** check connectivity from the qBittorrent host/container. A SOCKS proxy may not be able to reach your NAS's loopback/private address. Set `use_proxy` to `false` only when you intend a direct connection. That choice applies to both API calls and torrent-download redirects; configure tracker-side proxying in Prowlarr as appropriate.

**TLS error:** use a valid server certificate or provide `ca_file`. There is no insecure `verify=false` switch.

**Some indexers failed:** the summary row identifies up to three failures; stderr and Prowlarr logs provide context. Fix dead, blocked, or rate-limited indexers in Prowlarr rather than repeatedly searching all of them. API response bodies are not echoed because they can contain secrets.

**Too few results:** check Prowlarr's category support and individual indexer limits. `max_pages: 3` requests more pages where supported. Indexers that do not support the requested category may return no results.

**Download returns HTML or fails:** the plugin refuses to save login/Cloudflare pages as `.torrent` files. Check the indexer's configuration, cookies, and FlareSolverr setup in Prowlarr. Repeat the search when a download link is stale. A failed download does not erase other search results.

**Copied proxy links do not work in another app:** the plugin removes Prowlarr's API key from same-server result URLs and authenticates with a header when qBittorrent invokes the plugin's downloader. These sanitized links are not portable authenticated download links.

**Older qBittorrent changes a pipe in a title to a space:** the installed qBittorrent printer controls its wire-format escaping. Newer printers preserve the title through percent encoding; the plugin does not pre-encode it a second time.

## Security and network behavior

API calls authenticate with `X-Api-Key`. API redirects cannot leave the configured origin/base path. During torrent downloads, external HTTP(S) redirects are allowed, but the Prowlarr API key header is not forwarded. HTTPS-to-HTTP redirects and non-HTTP(S) download URLs are rejected, except valid BitTorrent magnets. Cookies follow standard cookie rules.

New JSON templates use owner-only permissions on POSIX systems. Existing permissions are not silently changed. Settings are never rewritten during an update. `.gitignore` excludes the default secret file.

JSON responses are limited to 16 MiB; torrent responses to 32 MiB, including decompressed gzip data. Torrent files undergo structural bencode validation before being handed to qBittorrent. This is not a malware scanner or a full BitTorrent semantic validator. Downloaded content and third-party search plugins should only be used from sources you trust.

## Development and testing

```sh
python -m unittest discover -s tests -v
python -m compileall -q prowlarr.py tests
```

Tests use synthetic data, fake credentials, and loopback HTTP servers. They cover configuration, query encoding, null/malformed results, pagination, streaming, concurrency limits, partial failures, redirects, credential scoping, gzip/size limits, timeouts, magnet handling, torrent validation, and CLI checks. No live trackers or real accounts are needed.

The CI matrix runs the suite on Linux with Python 3.9, 3.11, 3.13, and 3.14, plus Windows and macOS with Python 3.13. Passing these tests is not a claim that every qBittorrent/Prowlarr version, NAS package, reverse proxy, or tracker has been tested end to end. Please include redacted diagnostics and exact versions in bug reports.

The interface is based on the official [qBittorrent search-plugin contract](https://github.com/qbittorrent/search-plugins/wiki/How-to-write-a-search-plugin), [qBittorrent downloader](https://github.com/qbittorrent/qBittorrent/blob/master/src/searchengine/nova3/nova2dl.py), and Prowlarr's [API v1 search controller](https://github.com/Prowlarr/Prowlarr/blob/develop/src/Prowlarr.Api.V1/Search/SearchController.cs) and [download mapping](https://github.com/Prowlarr/Prowlarr/blob/develop/src/NzbDrone.Core/Download/DownloadMappingService.cs).

## License

[MIT](LICENSE). An independent community plugin, not an official Prowlarr or qBittorrent project.
