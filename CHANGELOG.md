# Changelog

## 1.1 — 2026-09-28

- Use qBittorrent-compatible two-part plugin versioning so the installed version is detected correctly.
- Increase the default HTTP request timeout from 30 to 120 seconds.

## 1.0 — 2026-09-28

Initial independent implementation of the Prowlarr qBittorrent search plugin.

- Single-file installation with separate, validated configuration and environment overrides.
- Dynamic torrent-indexer discovery, bounded parallel search, incremental results, optional pagination, and partial-failure reporting.
- Correct Unicode/query/category encoding and publication-date metadata.
- Authenticated torrent downloads, v1/v2 magnet support, redirect scoping, TLS verification, bounded responses, and structural bencode validation.
- Standalone diagnostics, offline regression tests, and a cross-platform CI workflow.
