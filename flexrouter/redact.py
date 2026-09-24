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
  is still scrubbed. What the trim does cost is stated as a named
  residual below - the claim once made here, that nothing which was
  caught becomes uncaught, was not true.

  The one exemption, and the only one permitted: a run made entirely of
  lowercase letters a-z, *and not immediately preceded by '=' or ':'*, is
  left alone. A lowercase word standing on its own is prose; a lowercase
  word bolted to an equals sign or a colon is a value, and "key=abcdef",
  "token=letmein" and "Bearer letmein." are exactly the shapes the edge
  trim would otherwise hand back unchanged. The carve-out is one-sided on
  purpose: "upstream:" and "provided:" in ordinary prose are *followed* by
  the colon, not preceded by one, so they stay readable. That is what keeps
  ordinary words
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
  - Named residual, and the cost the edge trim charges: a lowercase value
    separated from its cue word by a character the trim removes on the
    *trailing* side or by plain whitespace - "Bearer letmein." - keeps the
    exemption, because that is character-for-character the same shape as
    the prose the exemption exists to protect ("API key provided:", "...
    rejected by upstream:"). Only the leading side can be told apart, and
    only there is the carve-out made: a run immediately preceded by '=' or
    ':' is a value, not prose, so "key=abcdef" and "token=letmein;" lose
    the exemption. No rule can separate "letmein." from "provided:"
    without either an adjacency heuristic - deleted in round 3, and it was
    a hole - or scrubbing every sentence-final lowercase word near a cue.
  - The retained last-four tail is the same form the dashboard's `mask()`
    shows, so a caller can correlate which of their own configured keys was
    rejected. A small, known, accepted disclosure.
  - Neither rule has a model-name *pattern* exception, and none will be
    added: a rule with a carve-out is a rule with a hole a credential can
    fit through. What exists instead is an exact-match list of strings
    flexrouter itself wrote - configured provider and model ids, and its own
    request field names (`set_known_identifiers`, ADR 0017). A run is spared
    only when it is one of them whole.
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

# ...with one carve-out, and only on the leading side. The edge trim strips
# a leading '=' or ':' off a run before the exemption is judged, which is
# how "key=abcdef" and "token=letmein;" came back in clear: the trim threw
# away the very character that marked the rest as a value. A lowercase word
# standing on its own is prose; a lowercase word bolted to an equals sign or
# a colon is a value. The check is one-sided on purpose - "upstream:" and
# "provided:" are *followed* by their colon, never preceded by one, so they
# keep the exemption and error messages stay readable.
_VALUE_LEAD = ("=", ":")

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
            if not token:
                continue
            # The lowercase exemption, minus its one carve-out: a run sitting
            # directly behind an '=' or a ':' is a value someone assigned,
            # not a word someone wrote, and the edge trim had just thrown
            # that marker away before the exemption was judged.
            if _ALL_LOWER.match(token) and not (
                    start > 0 and text[start - 1] in _VALUE_LEAD):
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


# Names that are known not to be credentials because flexrouter itself wrote
# them: the providers and model ids in the owner's own settings, and the
# request fields flexrouter sends. This is not a pattern exemption. A run is
# spared only when the *whole* run, after the usual edge trim, is exactly one
# of these strings - so a credential gets through only by being character
# for character a public model id, and one that merely contains an id is
# still one run and is still scrubbed whole. See ADR 0017.
_REQUEST_FIELDS = frozenset({
    "frequency_penalty", "presence_penalty", "max_tokens", "max_completion_tokens",
    "response_format", "stream_options", "include_usage", "tool_choice",
    "parallel_tool_calls", "reasoning_effort", "context_length_exceeded",
})
_known: frozenset[str] = _REQUEST_FIELDS
_WHOLE_RUN = re.compile(_TOKEN_CHARS + r"+")
_PLACEHOLDER_BASE = 0xE000  # private-use code points: never token characters


def set_known_identifiers(names) -> None:
    """Replace the owner-configured identifiers scrub() leaves readable.

    Process-wide, like errors.MAX_LENGTH: called at router startup and on
    reload with the providers and model ids from the loaded settings.
    """
    global _known
    _known = _REQUEST_FIELDS | frozenset(n for n in names if n)


_secrets: tuple[str, ...] = ()

# Off by default (2026-09-24): Rule A's blind "16+ token characters is a
# credential" heuristic has no way to tell a provider/model identifier apart
# from an actual secret, and catches one whenever it isn't already in
# `_known` - which an id discovered or scored after the last reload never
# is. That turned "googleai/gemini-3.8-flash" into "...lash" in stored
# errors, with no way to get the real name back. Exact-known-secret
# replacement (the `_secrets` loop below) is unaffected by this flag and
# always runs: it only ever matches a credential flexrouter itself holds,
# character for character, so it has no false-positive cost to weigh
# against - unlike the heuristic rules, which are what this flag gates.
_enabled = False


def set_enabled(value: bool) -> None:
    """Point the heuristic redaction rules (Rule A, Rule B, the cue-word
    window) at a configured on/off value. Called from router startup and on
    reload, like set_max_length and set_known_identifiers."""
    global _enabled
    _enabled = bool(value)


def set_known_secrets(secrets) -> None:
    """Every credential flexrouter holds, for scrub_body. Longest first, so a
    key that is a prefix of another does not scrub the wrong span."""
    global _secrets
    _secrets = tuple(sorted({s for s in secrets if s}, key=len, reverse=True))


def scrub_body(text: str) -> str:
    """A provider's whole response body, readable, minus every credential.

    A provider can only echo back a key flexrouter sent it, and every key
    flexrouter sends is one of `_secrets`, so those are masked exactly,
    always. Rule B (the heuristic backstop for a value right after a cue
    word) only runs when `set_enabled(True)` has been called. Rule A's
    blind cut of every 16-character run never runs here regardless: in a
    response body it mostly destroys error codes ("INVALID_ARGUMENT",
    "labs_not_enabled"), which are the part worth reading. ADR 0017.
    """
    if not text:
        return text
    for secret in _secrets:
        if secret in text:
            text = text.replace(secret, _tail(secret))
    if not _enabled:
        return text
    text, kept = _shield(text)
    return _unshield(_scrub_cued(text), kept)


def _shield(text: str) -> tuple[str, list[str]]:
    """Swap every whole run that is a known identifier for a placeholder."""
    kept: list[str] = []

    def swap(m: re.Match[str]) -> str:
        run = m.group(0)
        lead, trail = _trim(run)
        core = run[lead:len(run) - trail]
        if core not in _known:
            return run
        kept.append(core)
        return run[:lead] + chr(_PLACEHOLDER_BASE + len(kept) - 1) + run[len(run) - trail:]

    return _WHOLE_RUN.sub(swap, text), kept


def _unshield(text: str, kept: list[str]) -> str:
    for i, core in enumerate(kept):
        text = text.replace(chr(_PLACEHOLDER_BASE + i), core)
    return text


def scrub(text: str) -> str:
    """The same text with anything that could be a credential cut to a tail.

    Unlike scrub_body, this has no response body to pull an exact `_secrets`
    match from a fixed list against - it runs on flexrouter's own generated
    text (`str(exc)`, an event's detail), which is why it leans on the
    heuristic rules instead. Those only run when `set_enabled(True)` has
    been called; a known secret is still matched exactly and replaced
    either way, same as scrub_body.
    """
    if not text:
        return text
    if not _enabled:
        for secret in _secrets:
            if secret in text:
                text = text.replace(secret, _tail(secret))
        return text
    text, kept = _shield(text)

    # Rule B first: it needs the cue words intact, and Rule A running
    # first could swallow one into its own replacement and destroy the
    # word boundary Rule B looks for. Rule A then runs over the result -
    # "…" is not a token character, so a value Rule B already cut down
    # cannot be re-matched into anything that exposes more of it.
    text = _scrub_cued(text)
    text = _LONG_RUN.sub(_scrub_long, text)
    return _unshield(text, kept)
