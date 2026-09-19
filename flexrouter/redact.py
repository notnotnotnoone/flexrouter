"""Nothing key-shaped leaves this process in full.

A provider's own error text is forwarded to whoever sent the request, because
flattening it to "server_error" throws away the only explanation anyone will
get. But several providers echo the rejected credential back in that text
("Incorrect API key provided: sk-..."), so the text has to be scrubbed on the
way out. Stage 1 shipped two leaks of exactly this shape — one through raw
parser error text, one through an export — and both were caught late.

Round 1 review found three verified ways a credential still escaped the
first version of this rule (split by internal separators, too short for the
length floor, or the wrong character class). Round 2 found two more (the
length floor still missed a real short-token example with no cue word
nearby, and a message naming two credentials only had the second one
caught) and fixed them by adding `_pick_value`: for each cue word, look at
the candidates nearby and choose which one is "the" value.

Round 3's re-review found that `_pick_value` was itself the hole: choosing
which candidate near a cue word is the credential is a parsing problem, not
something a heuristic can close, and every attempt to tune it (prefer a
strong delimiter, prefer the first candidate, bound the search window)
just moved the leak rather than closing it — a later colon, an unexpected
punctuation mark, or a newline between the cue and the value was always
enough to make the heuristic pick the wrong thing or nothing at all.
`_pick_value` is deleted, along with `_VALUE_CHARS` and the
`[ :="']`-shaped delimiter pattern it depended on. Rule B no longer tries
to identify *which* run after a cue word is the credential:

  Rule A (the blunt one, unchanged since round 2). Any run of 16 or more
  characters from the set a credential is built from — letters, digits, and
  the separators real formats use internally (``_-./+=:``, covering JWT
  dots, path-style AWS secrets, base64 padding, and ``key: value``/
  ``key=value`` framing) — is scrubbed outright. There is no
  character-class exception: a purely alphabetic or purely numeric run of
  this length is scrubbed the same as a mixed one.

  Rule B (the window one, replacing the cue-then-token rule entirely). For
  every occurrence of a cue word — api_key / api-key / api key / apikey /
  key / token / bearer / credential / secret / authorization
  (case-insensitive, whole word) — take the 48 characters immediately
  after it. Inside that window, scrub *every* run of 6 or more token
  characters, not just one candidate. There is no delimiter requirement and
  no adjacency requirement any more: a newline, a parenthesis, a semicolon,
  or any other character between the cue word and the credential no longer
  matters, because nothing has to directly follow the cue for the window
  scan to reach it.

  The one exemption, and the only one permitted: a run made entirely of
  lowercase letters a-z is left alone. That is what keeps "rejected",
  "upstream", "retrying", "please", "billing", "settings", "expired", and
  "reason" — ordinary words that happen to sit near a cue word — readable.
  A run containing a digit, an uppercase letter, or any of `_ - . / + = :`
  is scrubbed regardless of length (down to the 6-character floor for a
  Rule B match).

  The tail. The scrubbed form keeps "…" plus the token's last four
  characters — but only when the token is longer than 8 characters. An
  8-or-fewer character token becomes a bare "…" with no tail, so a short
  key does not leak half of itself through the part meant to make the
  message merely less readable.

Accepted costs, stated here rather than engineered around:

  - A URL 16+ characters long is now scrubbed whole, because a credential in
    a query string is exactly the case that must not escape. No URL
    exemption is added for this.
  - Ordinary English words of 16 or more characters ("responsibilities",
    "uncharacteristically") are scrubbed out of error text by Rule A alone,
    with no cue word needed. No dictionary check, vowel-ratio heuristic, or
    other cleverness is added to spare them — every such carve-out is a
    hole a credential could sit in instead.
  - Named residual (round 3): a credential of 15 characters or fewer, made
    entirely of lowercase a-z letters, sitting near a cue word, is not
    caught by Rule B — the lowercase exemption above cannot tell such a
    credential apart from an ordinary lowercase word, and Rule A's floor
    doesn't reach it either. This is knowingly accepted: the alternative is
    scrubbing every lowercase word within 48 characters of any cue word,
    which makes error messages unreadable and is exactly the kind of
    overreach that gets a scrubbing mechanism disabled rather than trusted.
    This exemption is not widened, and no other exemption (dictionary,
    vowel ratio, length-based cleverness, model-name, URL) is added beside
    it.
  - The retained last-four tail is the same form the dashboard's `mask()`
    shows, so an error message lets a caller correlate which of their own
    configured keys was rejected. That is a small, known, accepted
    disclosure, not a leak of the credential itself.
  - Neither rule is a model-name exception, and none will be added: a rule
    with a carve-out is a rule with a hole a credential can fit through.
"""
from __future__ import annotations

import re

# The characters a credential is made of, including the separators real
# formats use internally rather than just alphanumerics — without these a
# JWT's '.', an AWS-style secret's '/', or base64's '+'/'=' split the token
# into fragments each below the floor, and every fragment survived.
_TOKEN_CHARS = r"[A-Za-z0-9_\-./+=:]"

# Rule A: any long run of those characters, mixed class or not.
_LONG_RUN = re.compile(_TOKEN_CHARS + r"{16,}")

# Rule B: every occurrence of a cue word gets its own, independent 48
# character window after it (see _scrub_cued below) - there is no "pick
# the right candidate" step any more, every qualifying run inside the
# window is scrubbed, so a second cue word later in the same message, or a
# second credential inside one cue's own window, is never missed.
_CUE_WORD = re.compile(
    r"(?i)\b(?:api[ _\-]?key|key|token|bearer|credential|secret|authorization)\b"
)

# The characters a Rule B *run* is made of inside a cue word's window - the
# same set Rule A uses, minus ':'. Unlike round 2's now-deleted
# _VALUE_CHARS, this isn't about marking a delimiter any more (Rule B has
# no delimiter concept left at all) - it's so an ordinary lowercase word
# directly followed by a colon in running prose ("upstream:", "reason:")
# doesn't get fused with that colon into one run that then fails the
# all-lowercase exemption on the colon's account. The colon simply breaks
# a run the same way a space already does.
_RUN_CHARS = r"[A-Za-z0-9_\-./+=]"
_WINDOW_RUN = re.compile(_RUN_CHARS + r"{6,}")

# The one exemption Rule B makes: a run made entirely of lowercase a-z
# letters. Anything else in a cue word's window - mixed case, digits,
# underscores, hyphens, or any of the other characters _RUN_CHARS allows -
# is scrubbed.
_ALL_LOWER = re.compile(r"^[a-z]+$")

# How far past each cue word Rule B looks. Independent per cue word - two
# cue words close together can have overlapping windows, and a run found
# by more than one of them is simply scrubbed once.
_CUE_WINDOW = 48


def _tail(token: str) -> str:
    """"…" plus the last four characters — or a bare "…" for a short token,
    so the part meant to preserve a little readability doesn't itself leak
    half of a short credential."""
    if len(token) <= 8:
        return "…"
    return "…" + token[-4:]


def _scrub_cued(text: str) -> str:
    """Every run of 6+ token characters within 48 characters of any cue
    word, except a run made entirely of lowercase letters. No candidate is
    chosen over another - every qualifying run in every cue word's window
    is scrubbed."""
    cues = list(_CUE_WORD.finditer(text))
    if not cues:
        return text

    spans: dict[int, tuple[int, str]] = {}
    for cue in cues:
        window_end = min(cue.end() + _CUE_WINDOW, len(text))
        window = text[cue.end():window_end]
        for m in _WINDOW_RUN.finditer(window):
            token = m.group(0)
            if _ALL_LOWER.match(token):
                continue
            start = cue.end() + m.start()
            end = cue.end() + m.end()
            spans[start] = (end, _tail(token))

    if not spans:
        return text

    out: list[str] = []
    pos = 0
    for start in sorted(spans):
        end, replacement = spans[start]
        if start < pos:
            continue  # already covered by an earlier, overlapping span
        out.append(text[pos:start])
        out.append(replacement)
        pos = end
    out.append(text[pos:])
    return "".join(out)


def scrub(text: str) -> str:
    """The same text with anything that could be a credential cut to a tail."""
    if not text:
        return text

    text = _LONG_RUN.sub(lambda m: _tail(m.group(0)), text)
    text = _scrub_cued(text)
    return text
