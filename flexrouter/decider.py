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

Two implementations ship: NullDecider, still the default, and HttpDecider,
which talks to any OpenAI-compatible endpoint held to a JSON schema. The plan
named `typesafe/jev-1.13`; that model was never confirmed to exist, so no
vendor is hard-wired — endpoint and model id are settings
(`decider_base_url`, `decider_model`) and the key travels through
service_keys.py. With nothing configured, or configured but keyless, every
unrecognized error becomes "unknown" at zero confidence, which is always
below the confidence threshold ErrorBrain enforces, so the router works
fully without any classifier at all.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

logger = logging.getLogger(__name__)

# The fixed set spec 4a allows. A reply outside it is not a verdict, it is a
# model improvising, and is thrown away rather than trusted.
VERDICTS = (
    "too_fast", "bad_key", "needs_payment", "model_gone",
    "their_end_temporary", "message_too_long", "bad_request", "unknown",
)

# A model asked to rate its own certainty is only loosely calibrated -- good
# enough to tell "sure" from "guessing", not good enough to be taken at its
# word. Capping below 1.0 keeps a guess from ever ranking equal to a rule and
# keeps the review threshold something the model cannot talk its way past.
CONFIDENCE_CEILING = 0.95


@dataclass(frozen=True)
class ErrorVerdict:
    verdict: str
    source: str
    confidence: float


class Decider(Protocol):
    def classify_error(self, text: str, status: Optional[int]) -> ErrorVerdict: ...
    def describe_model(self, model_id: str, provider: str, published: dict) -> dict: ...


class NullDecider:
    # Read by ErrorBrain: with no classifier behind it there is nothing to
    # contest a rule with, so rules keep answering at full confidence.
    configured = False

    def classify_error(self, text: str, status: Optional[int]) -> ErrorVerdict:
        return ErrorVerdict(verdict="unknown", source="null", confidence=0.0)

    def describe_model(self, model_id: str, provider: str, published: dict) -> dict:
        return {}


_NO_OPINION = ErrorVerdict(verdict="unknown", source="classifier", confidence=0.0)


class HttpDecider:
    """Any OpenAI-compatible endpoint that can be held to a JSON schema.

    Deliberately not tied to one vendor: spec 4 asks for the classifier to sit
    behind a swappable interface, and the model named in the plan
    (`typesafe/jev-1.13`) was never confirmed to exist. Endpoint, model id and
    key are all settings, so choosing a vendor is configuration, not code.

    Every failure path returns the same thing NullDecider would. A classifier
    that is down, slow, misconfigured or hallucinating must never be able to
    break routing -- it is an optional opinion, not a dependency.
    """

    configured = True

    def __init__(self, base_url: str, model: str, api_key: str,
                 timeout: float = 3.0, ceiling: float = CONFIDENCE_CEILING) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._ceiling = ceiling

    def _schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "verdict": {"type": "string", "enum": list(VERDICTS)},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["verdict", "confidence"],
            "additionalProperties": False,
        }

    def classify_error(self, text: str, status: Optional[int]) -> ErrorVerdict:
        prompt = (
            "Classify this API error from an LLM provider into exactly one "
            "category. Answer only with the JSON schema given.\n\n"
            f"HTTP status: {status if status is not None else 'none'}\n"
            f"Error text: {text}"
        )
        try:
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "verdict", "strict": True,
                                        "schema": self._schema()},
                    },
                },
                timeout=self._timeout,
            )
            if resp.status_code >= 400:
                logger.warning("decider returned HTTP %s; no opinion", resp.status_code)
                return _NO_OPINION
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
        except Exception:
            logger.warning("decider call failed; no opinion", exc_info=True)
            return _NO_OPINION

        verdict = data.get("verdict")
        if verdict not in VERDICTS:
            logger.warning("decider answered %r, which is not a verdict; no opinion", verdict)
            return _NO_OPINION
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            return _NO_OPINION
        return ErrorVerdict(verdict=verdict, source="classifier",
                            confidence=max(0.0, min(confidence, self._ceiling)))

    def describe_model(self, model_id: str, provider: str, published: dict) -> dict:
        # Spec 4b. Out of scope for this build: nothing calls it yet and its
        # attribute shape belongs to Stage 6, which has not defined it.
        return {}


def build_decider(cfg, key: Optional[str]):
    """The one place that decides whether a real classifier exists.

    Configured-but-keyless falls back to NullDecider on purpose: firing
    unauthenticated calls at every novel error to collect a 401 each time is
    strictly worse than having no classifier at all.
    """
    if not getattr(cfg, "enabled", False) or not key:
        return NullDecider()
    return HttpDecider(base_url=cfg.base_url, model=cfg.model, api_key=key,
                       timeout=getattr(cfg, "timeout_seconds", 3.0),
                       ceiling=getattr(cfg, "confidence_ceiling", CONFIDENCE_CEILING))
