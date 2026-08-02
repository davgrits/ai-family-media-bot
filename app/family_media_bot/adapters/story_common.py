"""Parsing for the story model's output contract.

Kept apart from the adapter so the format can be tested without a model call.
"""

_ILLUSTRATION_MARKER = "ILLUSTRATION:"


def split_illustration_hint(raw: str) -> tuple[str, str]:
    """Separate the story body from the trailing ILLUSTRATION line, if any."""
    story_lines: list[str] = []
    hint = ""
    for line in raw.splitlines():
        if line.strip().upper().startswith(_ILLUSTRATION_MARKER):
            hint = line.strip()[len(_ILLUSTRATION_MARKER) :].strip()
        else:
            story_lines.append(line)
    return "\n".join(story_lines).strip(), hint
