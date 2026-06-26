"""Episode manifest manager.

Handles two manifest files:
- manifest.json         → downloader output (episode → filename, size, etc.)
- channel_manifest.json → uploader output  (episode → channel message_id)

Provides helpers for range-based episode grouping (like the reference bot).
"""
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import config


@dataclass
class Episode:
    number: int
    caption: str
    filename: str
    message_id: int | None = None   # channel message ID (for forwarding)
    size_mb: float = 0.0
    downloaded: bool = False


@dataclass
class EpisodeRange:
    start: int
    end: int
    label: str                     # button text, e.g. "( 1 - 25 ) ကြည့်ရန် နှိပ်ပါ။"
    callback: str                  # callback data, e.g. "range:1:25"
    episodes: list[Episode] = field(default_factory=list)


def load_manifest() -> dict[int, Episode]:
    """Load the download manifest and channel manifest, merge into Episodes."""
    episodes: dict[int, Episode] = {}

    # Load download manifest
    if config.MANIFEST_FILE.exists():
        try:
            raw = json.loads(config.MANIFEST_FILE.read_text())
            for key, data in raw.items():
                if key.startswith("_"):
                    continue
                ep_num = int(key)
                episodes[ep_num] = Episode(
                    number=ep_num,
                    caption=data.get("caption", f"One Piece Ep-{ep_num}"),
                    filename=data.get("filename", ""),
                    size_mb=data.get("size_mb", 0),
                    downloaded=data.get("downloaded", False),
                )
        except (json.JSONDecodeError, ValueError):
            pass

    # Merge channel manifest (message IDs for forwarding)
    if config.CHANNEL_MANIFEST_FILE.exists():
        try:
            raw = json.loads(config.CHANNEL_MANIFEST_FILE.read_text())
            for key, data in raw.items():
                if key.startswith("_"):
                    continue
                ep_num = int(key)
                if ep_num in episodes:
                    episodes[ep_num].message_id = data.get("message_id")
                else:
                    episodes[ep_num] = Episode(
                        number=ep_num,
                        caption=data.get("caption", f"One Piece Ep-{ep_num}"),
                        filename=data.get("filename", ""),
                        message_id=data.get("message_id"),
                    )
        except (json.JSONDecodeError, ValueError):
            pass

    return episodes


def get_total_episodes(episodes: dict[int, Episode]) -> int:
    """Get total episode count, either from config or auto-detect."""
    if config.TOTAL_EPISODES > 0:
        return config.TOTAL_EPISODES
    if episodes:
        return max(episodes.keys())
    return 0


def get_ranges(episodes: dict[int, Episode]) -> list[EpisodeRange]:
    """Group episodes into ranges for button display."""
    total = get_total_episodes(episodes)
    if total == 0:
        return []

    size = config.RANGE_SIZE
    ranges = []
    num_ranges = math.ceil(total / size)

    for i in range(num_ranges):
        start = i * size + 1
        end = min((i + 1) * size, total)

        label = f"( {start} - {end} ) ကြည့်ရန် နှိပ်ပါ။"
        callback = f"range:{start}:{end}"

        eps_in_range = [
            episodes[n] for n in range(start, end + 1)
            if n in episodes
        ]

        ranges.append(EpisodeRange(
            start=start,
            end=end,
            label=label,
            callback=callback,
            episodes=eps_in_range,
        ))

    return ranges


def get_episodes_in_range(
    episodes: dict[int, Episode],
    start: int,
    end: int,
) -> list[Episode]:
    """Return sorted episodes in the given range."""
    return sorted(
        [episodes[n] for n in range(start, end + 1) if n in episodes],
        key=lambda e: e.number,
    )


def get_message_ids(episodes_list: list[Episode]) -> list[int]:
    """Extract channel message IDs from a list of episodes."""
    return [
        ep.message_id
        for ep in episodes_list
        if ep.message_id is not None
    ]


def get_channel_id() -> int:
    """Get the storage channel ID from config or channel manifest."""
    if config.STORAGE_CHANNEL_ID:
        return config.STORAGE_CHANNEL_ID

    if config.CHANNEL_MANIFEST_FILE.exists():
        try:
            raw = json.loads(config.CHANNEL_MANIFEST_FILE.read_text())
            cid = raw.get("_channel_id")
            if cid:
                return int(cid)
        except (json.JSONDecodeError, ValueError):
            pass

    return 0
