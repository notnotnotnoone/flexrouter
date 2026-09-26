"""is_real / did_you_mean (PLAN-V2.3.md Session 5, grill-decisions.md §12):
the read-only model-list checker used to validate a pasted or hand-typed
model ID against a provider's real, currently-live list.
"""
from pathlib import Path

from flexrouter.catalogue import KNOWN_MODEL_IDS_FILENAME, did_you_mean, is_real
from flexrouter.store import write_json

GOOGLE_IDS_FILE = (
    Path(__file__).parent.parent
    / ".scratch" / "polish" / "evidence" / "google-live-model-ids-2026-09-25.txt"
)


def _real_google_ids() -> list[str]:
    lines = GOOGLE_IDS_FILE.read_text().splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def _seed_cache(state_dir, provider: str, ids: list[str]) -> None:
    write_json(Path(state_dir) / KNOWN_MODEL_IDS_FILENAME,
              {provider: {"checked_at": "2026-09-25T00:00:00Z", "ids": ids}})


def test_a_ready_known_id_is_real(tmp_path):
    _seed_cache(tmp_path, "googleai", _real_google_ids())
    assert is_real("googleai", "gemini-2.5-flash", str(tmp_path)) is True


def test_an_id_the_provider_never_listed_is_not_real(tmp_path):
    _seed_cache(tmp_path, "googleai", _real_google_ids())
    assert is_real("googleai", "gemini-3-flash", str(tmp_path)) is False


def test_a_provider_that_was_never_checked_gets_the_benefit_of_the_doubt(tmp_path):
    # No cache written at all for this provider - "unknown" must never read
    # as "not a real ID".
    assert is_real("groq", "whatever-id", str(tmp_path)) is True


def test_did_you_mean_finds_the_renamed_model_using_the_real_google_ids(tmp_path):
    _seed_cache(tmp_path, "googleai", _real_google_ids())
    suggestions = did_you_mean("googleai", "gemini-3-flash", str(tmp_path))
    assert suggestions[0] == "gemini-3-flash-preview"


def test_did_you_mean_returns_nothing_for_an_unchecked_provider(tmp_path):
    assert did_you_mean("groq", "whatever-id", str(tmp_path)) == []


def test_did_you_mean_returns_nothing_when_there_is_no_close_match(tmp_path):
    _seed_cache(tmp_path, "googleai", _real_google_ids())
    assert did_you_mean("googleai", "completely-unrelated-xyz", str(tmp_path)) == []
