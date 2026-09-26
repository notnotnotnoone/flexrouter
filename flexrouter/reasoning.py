"""Split inline reasoning tags (Gemma-style `<think>`/`<thought>`) out of
model output. Some providers put reasoning inline in `content` instead of a
separate `reasoning_content` field (grill-decisions.md §7); flexrouter moves
it to `reasoning_content` on the wire either way.
"""
from __future__ import annotations
import re

_TAG_NAMES = ("think", "thought")
_CLOSED_TAG_RE = re.compile(
    r"<(" + "|".join(_TAG_NAMES) + r")>(.*?)</\1>", re.DOTALL,
)
_UNCLOSED_OPEN_TAG_RE = re.compile(
    r"<(" + "|".join(_TAG_NAMES) + r")>(.*)", re.DOTALL,
)


def split_reasoning(content: str) -> tuple[str, str]:
    reasoning_parts: list[str] = []

    def _pull(match: re.Match) -> str:
        reasoning_parts.append(match.group(2).strip())
        return ""

    content = _CLOSED_TAG_RE.sub(_pull, content)

    # A response cut off by max_tokens mid-thought never gets its closing
    # tag - everything from the opening tag on is still reasoning, not a
    # broken reply.
    unclosed = _UNCLOSED_OPEN_TAG_RE.search(content)
    if unclosed:
        reasoning_parts.append(unclosed.group(2).strip())
        content = content[:unclosed.start()]

    return content.strip(), "\n\n".join(reasoning_parts)


_OPEN_TAGS = tuple(f"<{name}>" for name in _TAG_NAMES)
_CLOSE_TAGS = tuple(f"</{name}>" for name in _TAG_NAMES)


def _longest_partial_suffix_match(buffer: str, needles: tuple[str, ...]) -> int:
    """The length of the longest suffix of `buffer` that is itself a prefix
    of one of `needles` - i.e. text already in the buffer that *could* be
    the start of a tag if more chunks arrive. 0 if none."""
    max_len = min(len(buffer), max(len(n) for n in needles) - 1)
    for length in range(max_len, 0, -1):
        suffix = buffer[-length:]
        if any(needle.startswith(suffix) for needle in needles):
            return length
    return 0


class ReasoningStreamSplitter:
    """Splits inline `<think>`/`<thought>` reasoning out of a stream of
    content deltas. Tags can arrive split across chunk boundaries (a chunk
    boundary has no relation to a tag boundary), so this buffers just
    enough text to recognise a tag that might still be forming."""

    def __init__(self) -> None:
        self._buffer = ""
        self._in_reasoning = False

    def feed(self, text: str) -> tuple[str, str]:
        self._buffer += text
        content_out = []
        reasoning_out = []

        while True:
            needles = _CLOSE_TAGS if self._in_reasoning else _OPEN_TAGS
            out = reasoning_out if self._in_reasoning else content_out

            positions = [self._buffer.find(tag) for tag in needles]
            positions = [(p, tag) for p, tag in zip(positions, needles) if p != -1]
            if positions:
                pos, tag = min(positions)
                out.append(self._buffer[:pos])
                self._buffer = self._buffer[pos + len(tag):]
                self._in_reasoning = not self._in_reasoning
                continue

            hold = _longest_partial_suffix_match(self._buffer, needles)
            if hold:
                out.append(self._buffer[:len(self._buffer) - hold])
                self._buffer = self._buffer[len(self._buffer) - hold:]
            else:
                out.append(self._buffer)
                self._buffer = ""
            break

        return "".join(content_out), "".join(reasoning_out)

    def flush(self) -> tuple[str, str]:
        """Call once the stream ends. Anything still buffered is either an
        unclosed reasoning tag (the model got cut off mid-thought) or
        ordinary text that only looked like it might start a tag."""
        remaining, self._buffer = self._buffer, ""
        if self._in_reasoning:
            return "", remaining
        return remaining, ""
