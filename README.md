# 🔎 Prowlarr qBittorrent Search Plugin

Search all your **Prowlarr torrent indexers directly from qBittorrent** — no Jackett or duplicate setup.

## ✨ Features

- 🔍 Search all enabled Prowlarr torrent indexers
- ⚡ Parallel search
- 🎬 Movies, TV, anime, music, books, games, software
- 🧲 Magnet and `.torrent` downloads
- 📊 Seeds, leechers, size, tracker and publish date

## 🚀 Install

In qBittorrent: **Search → Search plugins… → Install a new one → Web link**

```text
https://raw.githubusercontent.com/caunt/prowlarr-qbittorrent-search-plugin/main/prowlarr.py
```

Run one search with **Prowlarr** selected. It will create `prowlarr.json` and show its location.

## ⚙️ Configure

```json
{
    "url": "http://127.0.0.1:9696",
    "api_key": "YOUR_API_KEY_HERE",
    "tracker_first": false
}
```

API key: **Prowlarr → Settings → General → Security → API Key**

🐳 Docker: use a URL reachable from the qBittorrent container, e.g. `http://prowlarr:9696`.

More options: [`prowlarr.example.json`](prowlarr.example.json)

## 🆘 Problems?

Test the same search/indexer in Prowlarr first. For connection errors, make sure the configured URL is reachable from qBittorrent.

MIT licensed. Community project, not officially affiliated with Prowlarr or qBittorrent.
