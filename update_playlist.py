"""Download, validate, and publish language-specific IPTV playlists."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

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
    """Check HTTP streams and validate HLS manifests and their first segment."""
    async def validate_get() -> bool:
        try:
            async with session.get(url, allow_redirects=True) as response:
                if response.status != 200:
                    return False
                body = await response.content.read(131072)
                text = body.decode("utf-8", errors="replace")
                if "#EXTM3U" not in text:
                    return bool(body)

                media_url = next(
                    (
                        line.strip()
                        for line in text.splitlines()
                        if line.strip() and not line.startswith("#")
                    ),
                    None,
                )
                if not media_url:
                    return False

                # A master playlist points to a media playlist; validate both.
                if "#EXT-X-STREAM-INF" in text:
                    media_url = urljoin(str(response.url), media_url)
                    async with session.get(
                        media_url, allow_redirects=True
                    ) as media_response:
                        if media_response.status != 200:
                            return False
                        media_text = (
                            await media_response.content.read(131072)
                        ).decode("utf-8", errors="replace")
                else:
                    media_url = str(response.url)
                    media_text = text

                segment_url = next(
                    (
                        line.strip()
                        for line in media_text.splitlines()
                        if line.strip() and not line.startswith("#")
                    ),
                    None,
                )
                if not segment_url:
                    return False

                async with session.get(
                    urljoin(media_url, segment_url), allow_redirects=True
                ) as segment_response:
                    if segment_response.status != 200:
                        return False
                    return bool(await segment_response.content.read(1))
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
            return False

    try:
        async with session.head(url, allow_redirects=True) as response:
            if response.status == 200:
                return await validate_get()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass

    return await validate_get()


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
