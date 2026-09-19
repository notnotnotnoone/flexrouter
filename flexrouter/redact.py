"""Nothing key-shaped leaves this process in full.

A provider's own error text is forwarded to whoever sent the request, because
flattening it to "server_error" throws away the only explanation anyone will
get. But several providers echo the rejected credential back in that text
("Incorrect API key provided: sk-..."), so the text has to be scrubbed on the
way out. Stage 1 shipped two leaks of exactly this shape — one through raw
parser error text, one through an export — and both were caught late.

The rule is deliberately blunt: any long run of key-ish characters that mixes
letters and digits is cut down to its last four characters. That catches some
model names too. A false positive costs a slightly less readable error
message; a false negative costs a key, so the trade only goes one way.
"""
from __future__ import annotations

import re

# Long runs of the characters credentials are made of. The 24-character floor
# is below every provider key format we have seen and above the request ids and
# token counts that show up in provider error text.
_KEYISH = re.compile(r"[A-Za-z0-9_\-]{24,}")


def scrub(text: str) -> str:
    """The same text with anything that could be a credential cut to a tail."""
    if not text:
        return text

    def _cut(m: re.Match) -> str:
        token = m.group(0)
        has_digit = any(c.isdigit() for c in token)
        has_alpha = any(c.isalpha() for c in token)
        # A run this long made only of digits is a number; made only of
        # letters it is a word or a sentence fragment. Credentials mix.
        if not (has_digit and has_alpha):
            return token
        return "…" + token[-4:]

    return _KEYISH.sub(_cut, text)
