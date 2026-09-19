"""Nothing key-shaped leaves this process in full.

A provider's own error text is forwarded to whoever sent the request, because
flattening it to "server_error" throws away the only explanation anyone will
get. But several providers echo the rejected credential back in that text
("Incorrect API key provided: sk-..."), so the text has to be scrubbed on the
way out. Stage 1 shipped two leaks of exactly this shape — one through raw
parser error text, one through an export — and both were caught late.

Round 1 review found three verified ways a credential still escaped the
first version of this rule in full, so this is the amended version:

  Rule A (the blunt one). Any run of 20 or more characters from the set a
  credential is built from — letters, digits, and the separators real
  formats use internally (``_-./+=:``, covering JWT dots, path-style AWS
  secrets, base64 padding, and ``key: value``/``key=value`` framing) — is
  scrubbed outright. There is no character-class exception any more: a
  purely alphabetic or purely numeric run of this length is scrubbed the
  same as a mixed one. The old version required a run to mix letters and
  digits, which is exactly what let an all-letter or all-digit key-shaped
  string survive intact — a hole a credential could sit in.

  Rule B (the cue one). A short credential has no length of its own to give
  it away, so it is caught by the word that introduces it instead: one of
  api_key / api-key / api key / apikey / key / token / bearer / credential /
  secret / authorization (case-insensitive, whole word), followed — allowing
  a little filler, such as "provided" or "is" — by up to four separator
  characters (space, colon, equals sign, quote) and then a run of 6 or more
  token characters. That run is scrubbed too.

  The tail. The scrubbed form keeps "…" plus the token's last four
  characters, as before — but only when the token is longer than 8
  characters. An 8-or-fewer character token becomes a bare "…" with no
  tail, so a short key does not leak half of itself through the part meant
  to make the message merely less readable.

Accepted costs, stated here rather than engineered around:

  - A URL 20+ characters long is now scrubbed whole, because a credential in
    a query string is exactly the case that must not escape. No URL
    exemption is added for this.
  - "API key provided: invalid" scrubs the word "invalid" — Rule B has no
    way to know the cue word's actual subject ends there rather than
    earlier in the sentence, so it takes the last thing in the sentence
    that looks token-shaped. Readability cost only.
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
_LONG_RUN = re.compile(_TOKEN_CHARS + r"{20,}")

# Rule B: a cue word, a little sentence filler (bounded so this can't run
# away across an unrelated later cue elsewhere in a long message), then a
# short delimiter, then a token-shaped run of 6 or more characters.
_CUE_THEN_TOKEN = re.compile(
    r"(?is)\b(?:api[ _\-]?key|key|token|bearer|credential|secret|authorization)\b"
    r".{0,40}"
    r"[ :=\"']{1,4}"
    r"(" + _TOKEN_CHARS + r"{6,})"
)


def _tail(token: str) -> str:
    """"…" plus the last four characters — or a bare "…" for a short token,
    so the part meant to preserve a little readability doesn't itself leak
    half of a short credential."""
    if len(token) <= 8:
        return "…"
    return "…" + token[-4:]


def scrub(text: str) -> str:
    """The same text with anything that could be a credential cut to a tail."""
    if not text:
        return text

    text = _LONG_RUN.sub(lambda m: _tail(m.group(0)), text)

    def _cut_cued(m: re.Match) -> str:
        token = m.group(1)
        prefix = m.group(0)[: m.start(1) - m.start(0)]
        return prefix + _tail(token)

    text = _CUE_THEN_TOKEN.sub(_cut_cued, text)
    return text
