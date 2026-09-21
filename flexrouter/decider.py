"""One small structured-output model doing classification jobs that would
otherwise be hand-maintained forever (spec §4). Two consumers share one
interface: classifying an unfamiliar provider error, and describing a
newly-discovered model's capabilities.

`describe_model` is declared here to match the spec's interface exactly,
but nothing calls it yet — there is no catalogue refresh (Stage 7) to call
it with, and its real return shape (spec §4b: vision/tools/reasoning/
context/size_class/provisional_score, each with its own value/source/
confidence) belongs to Stage 6, which will define it. Returning a plain
dict rather than inventing a dataclass now avoids a migration later.

NullDecider is the only implementation this stage ships. `typesafe/jev-1.13`
access and pricing are still unconfirmed (roadmap Open Q4) — every
unrecognized error becomes "unknown" with zero confidence, which is always
below the confidence threshold ErrorBrain enforces, so the router works
fully without any classifier configured.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass(frozen=True)
class ErrorVerdict:
    verdict: str
    source: str
    confidence: float


class Decider(Protocol):
    def classify_error(self, text: str, status: Optional[int]) -> ErrorVerdict: ...
    def describe_model(self, model_id: str, provider: str, published: dict) -> dict: ...


class NullDecider:
    def classify_error(self, text: str, status: Optional[int]) -> ErrorVerdict:
        return ErrorVerdict(verdict="unknown", source="null", confidence=0.0)

    def describe_model(self, model_id: str, provider: str, published: dict) -> dict:
        return {}
