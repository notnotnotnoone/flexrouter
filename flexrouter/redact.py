"""Nothing key-shaped leaves this process in full.

A provider's own error text is forwarded to whoever sent the request, because
flattening it to "server_error" throws away the only explanation anyone will
get. But several providers echo the rejected credential back in that text
("Incorrect API key provided: sk-..."), so the text has to be scrubbed on the
way out. Stage 1 shipped two leaks of exactly this shape — one through raw
parser error text, one through an export — and both were caught late.

Round 1 review found three verified ways a credential still escaped the
first version of this rule in full (split by internal separators, too short
for the length floor, or the wrong character class), and round 2 found two
more (the length floor still missed a real short-token example with no cue
word nearby, and a message naming two credentials only had the second one
caught). Both rounds amended the rule rather than patching around it:

  Rule A (the blunt one). Any run of 16 or more characters from the set a
  credential is built from — letters, digits, and the separators real
  formats use internally (``_-./+=:``, covering JWT dots, path-style AWS
  secrets, base64 padding, and ``key: value``/``key=value`` framing) — is
  scrubbed outright. There is no character-class exception: a purely
  alphabetic or purely numeric run of this length is scrubbed the same as a
  mixed one. The floor started at 24, dropped to 20 in round 1, and dropped
  again to 16 in round 2 after a real short-token example (19 characters,
  no cue word nearby) survived a 20-character floor. A false negative here
  costs a key; a false positive costs readability. The trade only goes one
  way, which is also why no floor is "safe enough" without Rule B alongside
  it for anything shorter still.

  Rule B (the cue one). A short credential has no length of its own to give
  it away, so it is caught by the word that introduces it instead: one of
  api_key / api-key / api key / apikey / key / token / bearer / credential /
  secret / authorization (case-insensitive, whole word). Each cue word gets
  its own, independent search — bounded by the next cue word (if any) or 40
  characters, whichever comes first, so one cue's match can never swallow a
  second, later credential whole. Within that bounded span: if a run of 6+
  token characters is preceded by a "strong" delimiter (colon, equals sign,
  or quote — the "key: value" / "key=value" shape), the last such run is
  taken as the value, because a real delimiter is the strongest signal of
  where the filler ends and the value starts. If no strong delimiter is
  found, the first run of 6+ token characters right after the cue is taken
  instead — the plain "key sk-abc123" shape, where there's no delimiter to
  wait for and prose describing what happened comes after the value, not
  before it.

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
    "uncharacteristically") are now scrubbed out of error text by Rule A
    alone, with no cue word needed. No dictionary check, vowel-ratio
    heuristic, or other cleverness is added to spare them — every such
    carve-out is a hole a credential could sit in instead.
  - A cue word followed immediately by ordinary sentence filler ("API key
    provided: ...", "the token is ...") can catch that filler word too when
    there's no delimiter to prefer a later value over it. Readability cost
    only.
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

# Rule B building blocks. Cue words are searched for on their own, each one
# handled independently (see _scrub_cued below) rather than as one big
# cue-to-token regex, precisely so a second cue word later in the same
# message gets its own chance instead of being swallowed as filler by the
# first one's search.
_CUE_WORD = re.compile(
    r"(?i)\b(?:api[ _\-]?key|key|token|bearer|credential|secret|authorization)\b"
)

# The characters a Rule B *value* is made of - the same set as Rule A's,
# minus ':'. ':' stays a pure delimiter for this rule: Rule A's class keeps
# it (a credential can appear after "key:" or inside a scrubbed URL), but
# if a value-matching run were also allowed to swallow a trailing ':', the
# colon that marks "the real value starts here" (as in "key provided: sk-
# ...") would get absorbed into the *filler* word right before it instead
# of surviving to mark the value that follows - "provided:" would consume
# the colon as if it belonged to "provided", leaving nothing to tell the
# real value apart from filler. None of the credential shapes this rule
# needs to catch use a literal ':' in the middle of the token itself.
_VALUE_CHARS = r"[A-Za-z0-9_\-./+=]"

# A short delimiter (space, colon, equals, quote) followed by a value-shaped
# run of 6+ characters. Every non-overlapping occurrence within a cue's
# bounded search span is a candidate; _pick_value below chooses among them.
_GAP_TOKEN = re.compile(r"([ :=\"']{1,4})(" + _VALUE_CHARS + r"{6,})")

# The delimiters strong enough to mark "the value starts here" rather than
# just separating two ordinary words.
_STRONG_GAP = set(':="\'')

# How far past a cue word (or up to the next cue word, if sooner) Rule B
# will look for its value. Bounded, rather than unbounded, so one cue's
# search cannot run across an entire long message and swallow a second,
# later credential as if it were filler.
_CUE_WINDOW = 40


def _tail(token: str) -> str:
    """"…" plus the last four characters — or a bare "…" for a short token,
    so the part meant to preserve a little readability doesn't itself leak
    half of a short credential."""
    if len(token) <= 8:
        return "…"
    return "…" + token[-4:]


def _pick_value(matches: list[re.Match]) -> "re.Match | None":
    """Among the gap+token candidates found after one cue word, the one that
    is actually the value.

    A strong delimiter (":", "=", a quote) is the clearest signal of where
    filler ends and a value begins — "API key provided: sk-..." has one
    right before the real key, and "provided" itself, which only sits
    behind a bare space, does not. When a strong-delimited candidate
    exists, the last one is taken, on the theory that a value near the end
    of the cue's clause is more likely to be the actual value than earlier
    prose that happens to contain a colon.

    Without any strong delimiter, there is no such signal, so the run right
    after the cue word is taken instead — the plain "key sk-abc123 was
    rejected" shape, where nothing between the cue and the value needs
    skipping, and anything after the value is prose describing what
    happened rather than another candidate value.
    """
    if not matches:
        return None
    strong = [m for m in matches if any(c in _STRONG_GAP for c in m.group(1))]
    if strong:
        return strong[-1]
    return matches[0]


def _scrub_cued(text: str) -> str:
    cues = list(_CUE_WORD.finditer(text))
    if not cues:
        return text

    out: list[str] = []
    pos = 0
    for i, cue in enumerate(cues):
        if cue.start() < pos:
            continue  # already inside a span this pass already replaced

        window_end = min(cue.end() + _CUE_WINDOW, len(text))
        if i + 1 < len(cues):
            window_end = min(window_end, cues[i + 1].start())
        span = text[cue.end():window_end]

        chosen = _pick_value(list(_GAP_TOKEN.finditer(span)))
        if chosen is None:
            continue

        token = chosen.group(2)
        token_start = cue.end() + chosen.start(2)
        token_end = cue.end() + chosen.end(2)

        out.append(text[pos:token_start])
        out.append(_tail(token))
        pos = token_end

    out.append(text[pos:])
    return "".join(out)


def scrub(text: str) -> str:
    """The same text with anything that could be a credential cut to a tail."""
    if not text:
        return text

    text = _LONG_RUN.sub(lambda m: _tail(m.group(0)), text)
    text = _scrub_cued(text)
    return text
