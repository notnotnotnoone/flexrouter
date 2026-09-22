# Issue 04: the fake penalty box can diverge from quarantine, but no test makes it

Status: needs-triage

## What

Stage 8 rewrote `FakePenalties.is_penalized` in `tests/test_app.py` so it models
the real `PenaltyBox.is_penalized` — true for a quarantine *or* an active
backoff penalty — instead of merely aliasing `is_quarantined`. It now *can*
diverge, and a `penalize()` helper exists to make it.

No test actually calls that helper, so the divergence is never exercised.

## Done when

A test puts a model in backoff without quarantining it and asserts the
dashboard treats it as unavailable — which is the behaviour the real
`PenaltyBox` produces and the alias could not.

## Note

Raised by the final Stage 8 review as a residual gap, not a defect. The
requirement it came from did not ask for the test.
