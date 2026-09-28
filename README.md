# 🔎 Prowlarr qBittorrent Search Plugin

Search **all your Prowlarr torrent indexers directly from qBittorrent**. No Jackett, no duplicate indexer setup — just one plugin connected to the Prowlarr you already use.

Works with qBittorrent's desktop Search tab and Web UI search.

## ✨ What you get

- 🔍 Search all enabled Prowlarr torrent indexers from one place
- ⚡ Fast parallel searches
- 🎬 Categories for movies, TV, anime, music, books, games, and software
- 🧲 Magnet links and `.torrent` downloads
- 📊 Seeds, leechers, size, tracker name, and publish date
- 🌍 Unicode and special-character searches
- 🐳 Works with Docker, NAS, Synology, and regular installations
- 🔐 Your Prowlarr API key stays in your local configuration

## 🚀 Install

In qBittorrent open:

**Search → Search plugins… → Install a new one → Web link**

Paste:

```text
https://raw.githubusercontent.com/caunt/prowlarr-qbittorrent-search-plugin/main/prowlarr.py
```

If you don't see the Search tab in the desktop app, enable it from **View → Search Engine**.

Run a search with **Prowlarr** selected. The plugin will create a `prowlarr.json` configuration file and show its location.

## ⚙️ Configure

Open `prowlarr.json` and set your Prowlarr address and API key:

```json
{
    "url": "http://127.0.0.1:9696",
    "api_key": "YOUR_API_KEY_HERE",
    "tracker_first": false
}
```

Your API key is in **Prowlarr → Settings → General → Security → API Key**.

Save the file and search again — that's it. 🎉

## 🐳 Docker / NAS

The `url` must be reachable **from the machine or container running qBittorrent**.

If qBittorrent and Prowlarr are native apps on the same machine, this usually works:

```text
http://127.0.0.1:9696
```

If they run in separate Docker containers, use the Prowlarr container/service name on their shared Docker network, for example:

```text
http://prowlarr:9696
```

For Synology packages on the same NAS, `127.0.0.1:9696` is normally the right place to start.

## 🎛️ Optional settings

The default configuration is designed to work for most users. You can also customize which indexers are searched, concurrency, timeout, pagination, proxy behavior, and custom HTTPS certificates.

See [`prowlarr.example.json`](prowlarr.example.json) for all available options.

## 🆘 Troubleshooting

**No results?** Make sure your indexers are enabled and searchable in Prowlarr, then try the same search directly in Prowlarr.

**Connection error?** Check that the configured Prowlarr URL is reachable from qBittorrent. Inside Docker, `127.0.0.1` points to the qBittorrent container itself.

**401 / 403?** Recheck the API key and any reverse-proxy authentication in front of Prowlarr.

**Downloads fail?** Test the indexer in Prowlarr first. Cloudflare, expired cookies, or tracker-side problems should be fixed there.

## 🔄 Replacing the old Prowlarr plugin

This plugin uses the same `prowlarr.py` filename and supports the familiar `url`, `api_key`, and `tracker_first` settings.

Back up your `prowlarr.json`, remove the old plugin, install this one using the URL above, then restore your configuration.

## ❤️ Project

Community-made qBittorrent search plugin for Prowlarr.

Licensed under the [MIT License](LICENSE). Not affiliated with or officially supported by the Prowlarr or qBittorrent projects.
