# Issue 04: one wrong example in the scrubber's docstring

Status: ready-for-agent

## What

`flexrouter/redact.py` around lines 56-58 lists `"Bearer letmein."` among the shapes the
`=`/`:` carve-out catches. It is not caught — it is exactly the shape that is still
missed, and the named residual forty lines further down says so. One of the two is
wrong and it is this one.

## Why it matters at all

It is a one-word edit, but it is in the module a future maintainer reads to decide
whether the scrubbing rule is safe, and the two statements contradict each other.

## Done when

- The example list no longer claims a shape the rule does not catch.
- The two statements agree.

## Do not

Do not "fix" it by widening the rule. Closing that residual was tried: making the
lead-in skip whitespace causes the word `retrying` to be scrubbed out of
`"token gsk_BBB222 rejected by upstream: retrying"`, which breaks
`tests/test_redact.py::test_ordinary_lowercase_words_near_a_cue_word_stay_readable`.
That path needs a fresh decision from the owner, not a quiet widening.
