# IPTV Working Links

This repository publishes verified IPTV streams from the India playlist source.

## M3U playlist

Use this URL in VLC or any IPTV player that supports M3U playlists:

```text
https://raw.githubusercontent.com/madanithishreddy/IPTV-working-links/main/playlists/index.m3u
```

### VLC

1. Open VLC.
2. Select **Media → Open Network Stream**.
3. Paste the M3U URL.
4. Select **Play**.

### IPTV players

Add the URL as an **M3U Playlist URL**.

## Updates

GitHub Actions checks the source playlist and refreshes `playlists/index.m3u` weekly,
every Sunday at 02:00 UTC. Manual workflow runs are also supported.
