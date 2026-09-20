# Stage 2 follow-ups: known issues left open at merge

Stage 2 of the v2 design (`docs/superpowers/specs/2026-09-18-flexrouter-v2-design.md`)
completed the OpenAI-shaped surface and turned the importable library into a client of
the one service. It finished with a clean suite and a whole-branch review. These items
were surfaced during that work and deliberately deferred rather than fixed on the
branch.

Each is real. They are numbered in rough priority order.

Issue 02 is a credential sitting in a file on the owner's own machine. It is not
reachable over the network today, but it is the most serious item here and should be
picked up with Stage 3, which rewrites that recording path anyway.
