"""Filename helpers for exported meeting notes."""

import re
import time


def obsidian_filename(mp3_stem: str, context, fallback_date: str | None = None) -> str:
    """Build a smart Obsidian filename from a recording stem and meeting context."""
    bare = mp3_stem.replace("meeting_", "")
    parts = bare.split("_")
    date_str = parts[0] if parts and parts[0] else fallback_date or time.strftime("%Y-%m-%d")
    time_str = parts[1].replace("-", ":") if len(parts) > 1 else ""

    if context and getattr(context, "title", None):
        safe_title = re.sub(r'[\\/*?:"<>|]', "", context.title).strip()
        return f"{date_str} {safe_title}.md"
    if context and getattr(context, "meeting_type", None):
        mtype = context.meeting_type.capitalize()
        suffix = f" {time_str[:5]}" if time_str else ""
        return f"{date_str} {mtype}{suffix}.md"

    suffix = f" {time_str[:5]}" if time_str else ""
    return f"{date_str} Meeting{suffix}.md"
