"""Saved conversations: prompt, reply and reasoning per request (§7)."""
from datetime import datetime, timedelta, timezone

from flexrouter import redact
from flexrouter.conversations import MAX_CHARS, ConversationStore


def test_save_and_read_back(tmp_path):
    store = ConversationStore(str(tmp_path))
    store.save("req_1", [{"role": "user", "content": "hi"}], "hello", "hmm")
    got = store.get("req_1")
    assert got["messages"] == [{"role": "user", "content": "hi"}]
    assert got["reply"] == "hello" and got["reasoning"] == "hmm"
    assert store.get("req_2") is None


def test_the_off_switch_saves_nothing(tmp_path):
    store = ConversationStore(str(tmp_path), enabled=False)
    store.save("req_1", [{"role": "user", "content": "hi"}], "hello")
    assert store.get("req_1") is None
    assert not (tmp_path / "conversations").exists()


def test_long_messages_are_cut_at_about_20kb(tmp_path):
    store = ConversationStore(str(tmp_path))
    store.save("req_1", [{"role": "user", "content": "x" * (MAX_CHARS * 2)}], "y" * (MAX_CHARS + 5))
    got = store.get("req_1")
    assert len(got["messages"][0]["content"]) < MAX_CHARS + 100
    assert got["reply"].endswith("[cut: longer than 20 KB]")


def test_keys_flexrouter_holds_are_masked(tmp_path):
    secret = "gsk_abcdefghijklmnopqrstuvwxyz0123456789"
    redact.set_known_secrets([secret])
    try:
        store = ConversationStore(str(tmp_path))
        store.save("req_1", [{"role": "user", "content": f"my key is {secret}"}], "ok")
        assert secret not in store.get("req_1")["messages"][0]["content"]
    finally:
        redact.set_known_secrets([])


def test_old_days_are_deleted(tmp_path):
    store = ConversationStore(str(tmp_path), days=7)
    old = datetime.now(timezone.utc) - timedelta(days=9)
    store.save("req_old", [{"role": "user", "content": "a"}], "b", now=old)
    store.save("req_new", [{"role": "user", "content": "c"}], "d")
    assert store.get("req_old") is None and store.get("req_new") is not None


def test_multipart_content_keeps_its_text(tmp_path):
    store = ConversationStore(str(tmp_path))
    store.save("req_1", [{"role": "user", "content": [
        {"type": "text", "text": "what is this"}, {"type": "image_url", "image_url": {}}]}], "a cat")
    assert store.get("req_1")["messages"][0]["content"] == "what is this\n[image_url]"
