r"""Nothing key-shaped leaves this process in full.

A provider's own error text is forwarded to whoever sent the request, because
flattening it to "server_error" throws away the only explanation anyone will
get. But several providers echo the rejected credential back in that text
("Incorrect API key provided: sk-..."), so the text has to be scrubbed on the
way out. Stage 1 shipped two leaks of exactly this shape - one through raw
parser error text, one through an export - and both were caught late.

Four rounds of review have narrowed this rule. Round 2's attempt to decide
*which* run near a cue word was the credential (`_pick_value`) was itself a
hole and was deleted in round 3 in favour of a window scan. Round 4 closed
the last three: ':' had been dropped from the run class, a run straddling a
window's far edge was only half scrubbed, and the two rules ran in the wrong
order. Do not reintroduce a candidate-picking heuristic, and do not add an
exemption beside the single one below.

The token character class is ``[A-Za-z0-9_\-./+=:]`` - letters, digits, and
the separators real credential formats use internally (JWT dots, path-style
AWS secrets, base64 padding and ``=``, ``key: value`` colons). Without them a
credential splits into fragments that each fall under the floor.

The two rules, in the order they run:

  Rule B (first - the window one). For every case-insensitive, word-bounded
  occurrence of a cue word - api_key / api-key / api key / apikey / key /
  token / bearer / credential / secret / authorization - take the 48
  characters immediately after it. Inside that window, scrub *every* run of
  6 or more token characters that starts inside it, extended to the run's
  natural end first: a run is never scrubbed in part, because the part left
  outside the window was a piece of the credential in clear. No candidate
  is chosen over another, there is no delimiter class and no adjacency
  requirement, so a newline, parenthesis or semicolon between the cue word
  and the credential changes nothing.

  The edge trim (both rules). Before a run is judged or scrubbed, any
  leading and trailing characters from ``: = . , ;`` are trimmed off it.
  The trimmed characters stay in the output exactly where they were; only
  what is left between them is the run. This is what lets ':' stay in the
  token class - so "ab12:cd34:ef56" is one run and is scrubbed whole -
  without an ordinary word that merely abuts a colon ("provided:",
  "upstream:", "reason:") losing its exemption over that colon alone. It
  is not an exemption: a trimmed run is still judged on its own
  characters, so "abc123:" trims to "abc123", still has digits in it, and
  is still scrubbed. Nothing that was caught becomes uncaught.

  The one exemption, and the only one permitted: a run made entirely of
  lowercase letters a-z is left alone. That is what keeps ordinary words
  that happen to sit near a cue word - "rejected", "retrying", "please",
  "billing", "expired" - readable. A run containing a digit, an uppercase
  letter, or any of ``_ - . / + = :`` is scrubbed regardless of length,
  down to the 6-character floor.

  Rule A (second - the blunt one). Any run of 16 or more token characters
  is scrubbed outright, with no character-class condition at all: a purely
  alphabetic or purely numeric run of that length goes the same way as a
  mixed one. It runs second because running it first could swallow a cue
  word into its own replacement and destroy the word boundary Rule B needs,
  letting a short credential beside it survive. Running second is safe:
  "…" is not a token character, so a value Rule B has already cut down
  cannot be re-matched by Rule A into anything exposing more of it.

  The tail. The scrubbed form keeps "…" plus the token's last four
  characters - but only when the token is longer than 8 characters. An
  8-or-fewer character token becomes a bare "…", so the part meant to
  preserve a little readability does not itself leak half of a short key.

Accepted costs, stated here rather than engineered around:

  - A URL 16+ characters long is scrubbed whole, because a credential in a
    query string is exactly the case that must not escape. No URL exemption.
  - Ordinary English words of 16 or more characters ("responsibilities",
    "uncharacteristically") are scrubbed by Rule A alone, with no cue word
    needed. No dictionary check, vowel-ratio heuristic or other cleverness is added
    to spare them - every such carve-out is a hole a credential can sit in.
  - Named residual: a credential more than 48 characters after its cue word
    is out of Rule B's window, and is caught only if it is long enough for
    Rule A. Widening the window turns most of a long provider message into
    ellipses.
  - Named residual: a credential of 15 characters or fewer made only of
    lowercase a-z letters, sitting near a cue word, is not caught - the
    lowercase exemption cannot tell it apart from an ordinary word, and
    Rule A's floor does not reach it. Knowingly accepted: the alternative
    is scrubbing every lowercase word within 48 characters of any cue word,
    which makes error messages unreadable and is exactly the kind of
    overreach that gets a scrubbing mechanism disabled rather than trusted.
  - The retained last-four tail is the same form the dashboard's `mask()`
    shows, so a caller can correlate which of their own configured keys was
    rejected. A small, known, accepted disclosure.
  - Neither rule has a model-name exception, and none will be added: a rule
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

# The characters a Rule B *run* is made of inside a cue word's window: the
# same full token class Rule A uses, ':' included. Round 3 excluded ':'
# here so an ordinary lowercase word directly followed by a colon in
# running prose ("upstream:", "reason:") would not fuse with that colon
# into a run that then fails the all-lowercase exemption. That softened
# the ruled class, and a short colon-separated credential
# ("ab12:cd34:ef56") was split into sub-floor pieces and survived whole.
# The class is the ruled one again; a lowercase word that happens to abut
# a colon being scrubbed is the accepted cost of that.
_RUN_CHARS = _TOKEN_CHARS
_WINDOW_RUN = re.compile(_RUN_CHARS + r"{6,}")
_RUN_CHAR = re.compile(_RUN_CHARS)

# The one exemption Rule B makes: a run made entirely of lowercase a-z
# letters. Anything else in a cue word's window - mixed case, digits,
# underscores, hyphens, or any of the other characters _RUN_CHARS allows -
# is scrubbed.
_ALL_LOWER = re.compile(r"^[a-z]+$")

# How far past each cue word Rule B looks. Independent per cue word - two
# cue words close together can have overlapping windows, and a run found
# by more than one of them is simply scrubbed once.
_CUE_WINDOW = 48


# Characters trimmed off a run's leading and trailing edge before the run
# is judged and scrubbed, in both rules. They stay in the output text; only
# what is left between them is the run. This is what lets ':' stay in the
# token class (so "ab12:cd34:ef56" is one run and is scrubbed whole)
# without an ordinary word that happens to abut a colon - "provided:",
# "upstream:", "reason:" - losing its exemption over that colon alone. It
# is a trim of the edges, not an exemption: a trimmed run is still judged
# on its own characters, so "abc123:" trims to "abc123", still has digits,
# and is still scrubbed.
_EDGE_CHARS = ":=.,;"


def _trim(run: str) -> tuple[int, int]:
    """How many edge characters to leave alone at each end of a run."""
    lead = len(run) - len(run.lstrip(_EDGE_CHARS))
    trail = len(run) - len(run.rstrip(_EDGE_CHARS))
    return lead, trail


def _tail(token: str) -> str:
    """"…" plus the last four characters — or a bare "…" for a short token,
    so the part meant to preserve a little readability doesn't itself leak
    half of a short credential."""
    if len(token) <= 8:
        return "…"
    return "…" + token[-4:]


def _run_end(text: str, end: int) -> int:
    """Where a run that was cut off at a window's edge actually ends."""
    while end < len(text) and _RUN_CHAR.match(text[end]):
        end += 1
    return end


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
            start = cue.end() + m.start()
            # A run that starts inside the window but continues past its
            # far edge is scrubbed whole, not just the part that happened
            # to fall inside. Cutting at the edge left the tail of a
            # credential in clear.
            end = _run_end(text, cue.end() + m.end())
            # Edge delimiters are not part of the run: trim them off
            # before the exemption is judged and before the scrub, and
            # leave them in the output where they were.
            lead, trail = _trim(text[start:end])
            start += lead
            end -= trail
            token = text[start:end]
            if not token or _ALL_LOWER.match(token):
                continue
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


def _scrub_long(m: re.Match[str]) -> str:
    """Rule A's replacement, with the same edge trim Rule B uses so both
    rules treat a run's boundaries the same way."""
    run = m.group(0)
    lead, trail = _trim(run)
    core = run[lead:len(run) - trail]
    if not core:
        return run
    return run[:lead] + _tail(core) + run[len(run) - trail:]


def scrub(text: str) -> str:
    """The same text with anything that could be a credential cut to a tail."""
    if not text:
        return text

    # Rule B first: it needs the cue words intact, and Rule A running
    # first could swallow one into its own replacement and destroy the
    # word boundary Rule B looks for. Rule A then runs over the result -
    # "…" is not a token character, so a value Rule B already cut down
    # cannot be re-matched into anything that exposes more of it.
    text = _scrub_cued(text)
    text = _LONG_RUN.sub(_scrub_long, text)
    return text
