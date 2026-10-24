"""Download, validate, and publish language-specific IPTV playlists."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import aiohttp


INPUT_M3U_URL = os.environ.get("INPUT_M3U_URL", "").strip()
PLAYLIST_DIR = Path("playlists")
CHECK_CONCURRENCY = 150
REQUEST_TIMEOUT_SECONDS = 5
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ATTRIBUTE_RE = re.compile(
    r"""([\w-]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s,]+))"""
)


@dataclass(frozen=True)
class Channel:
    extinf: str
    url: str
    language: str
    group_title: str
    name: str


def _parse_attributes(extinf: str) -> dict[str, str]:
    return {
        match.group(1).lower(): next(
            value for value in match.groups()[1:] if value is not None
        )
        for match in ATTRIBUTE_RE.finditer(extinf)
    }


def parse_playlist(contents: str) -> list[Channel]:
    """Parse EXTINF records and their following stream URLs."""
    lines = contents.splitlines()
    channels: list[Channel] = []
    index = 0

    while index < len(lines):
        extinf = lines[index].strip()
        if not extinf.upper().startswith("#EXTINF"):
            index += 1
            continue

        url_index = index + 1
        while url_index < len(lines):
            candidate = lines[url_index].strip()
            if candidate and not candidate.startswith("#"):
                break
            url_index += 1

        if url_index < len(lines):
            url = lines[url_index].strip()
            if url.startswith(("http://", "https://")):
                attributes = _parse_attributes(extinf)
                channels.append(
                    Channel(
                        extinf=extinf,
                        url=url,
                        language=attributes.get("tvg-language", "Unknown").strip()
                        or "Unknown",
                        group_title=attributes.get("group-title", "").strip(),
                        name=extinf.partition(",")[2].strip(),
                    )
                )
            index = url_index
        index += 1

    return channels


async def fetch_source(session: aiohttp.ClientSession) -> str:
    if not INPUT_M3U_URL:
        raise RuntimeError(
            "INPUT_M3U_URL is not set. Configure it as an environment variable "
            "or a GitHub repository variable."
        )

    async with session.get(INPUT_M3U_URL) as response:
        response.raise_for_status()
        return await response.text(errors="replace")


async def is_live(session: aiohttp.ClientSession, url: str) -> bool:
    """Use HEAD first, then a minimal GET for servers that block HEAD."""
    try:
        async with session.head(url, allow_redirects=True) as response:
            if response.status == 200:
                return True
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass

    try:
        async with session.get(url, allow_redirects=True) as response:
            if response.status != 200:
                return False
            await response.content.read(1)
            return True
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return False


async def check_channels(channels: Iterable[Channel]) -> list[Channel]:
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    headers = {"User-Agent": USER_AGENT}
    connector = aiohttp.TCPConnector(limit=CHECK_CONCURRENCY)
    semaphore = asyncio.Semaphore(CHECK_CONCURRENCY)
    unique_channels = list(dict.fromkeys(channels))

    async with aiohttp.ClientSession(
        timeout=timeout, headers=headers, connector=connector
    ) as session:
        async def check(channel: Channel) -> Channel | None:
            async with semaphore:
                return channel if await is_live(session, channel.url) else None

        results = await asyncio.gather(*(check(channel) for channel in unique_channels))

    return [channel for channel in results if channel is not None]


def write_playlists(channels: Iterable[Channel]) -> None:
    PLAYLIST_DIR.mkdir(parents=True, exist_ok=True)
    for old_file in PLAYLIST_DIR.glob("*.m3u"):
        old_file.unlink()

    all_lines = ["#EXTM3U"]
    for channel in channels:
        all_lines.extend((channel.extinf, channel.url))

    (PLAYLIST_DIR / "index.m3u").write_text(
        "\n".join(all_lines) + "\n", encoding="utf-8"
    )


async def main() -> None:
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(
        timeout=timeout, headers={"User-Agent": USER_AGENT}
    ) as session:
        source = await fetch_source(session)
    channels = parse_playlist(source)
    live_channels = await check_channels(channels)
    write_playlists(live_channels)
    print(f"Verified {len(live_channels)} of {len(channels)} streams.")


if __name__ == "__main__":
    asyncio.run(main())
