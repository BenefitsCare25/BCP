"""Safe HTTP download response headers."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


def attachment_header(file_name: str) -> str:
    """Build an injection-safe, Unicode-compatible Content-Disposition."""
    display_name = Path(file_name).name
    ascii_name = "".join(
        char if 32 <= ord(char) < 127 and char not in {'"', "\\"} else "_"
        for char in display_name
    ) or "document"
    encoded_name = quote(display_name, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
