# Contributor notes

Keep `prowlarr.py` a single installable file with Python 3.9+ syntax and no external packages. qBittorrent provides its helper modules at runtime; standalone diagnostics must not require them.

Do not perform configuration I/O, network I/O, or output at module import. The plugin class name must match its filename. Keep its `url` identical to each emitted result's `engine_url` so qBittorrent routes downloads correctly. Let qBittorrent's printer encode names; sanitize URL delimiters and control characters separately.

Configuration is data, not code. Discover indexers through Prowlarr rather than embedding tracker-specific behavior. Preserve the existing three-key configuration format through defaults. Never rewrite the user's configuration or store a real key in examples, tests, logs, commits, or fixtures.

Authenticate only to the configured origin/base path. Do not forward Prowlarr credentials across download redirects, disable TLS verification, run shell commands, or add silent proxy bypasses. Bound concurrency, pages, response sizes, and queue capacity. Preserve successful results during partial failures and guard against producer deadlock when output closes.

Before committing, run `python -m unittest discover -s tests -v` and `python -m compileall -q prowlarr.py tests`. Add a regression test for fixes. Tests must not depend on live trackers or real credentials. Keep the top `# VERSION:` comment synchronized with `VERSION`, and update the changelog for behavioral changes. Do not describe mock/loopback tests as successful real-NAS or GUI integration tests.
