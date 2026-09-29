from flexrouter import chats


def test_save_get_list_rename_delete(tmp_path):
    cid = chats.new_id()
    doc = chats.save(cid, {"messages": [{"role": "user", "content": "hello there"},
                                        {"role": "assistant", "content": "hi",
                                         "answered_by": "groq/x"}]}, tmp_path)
    assert doc["title"] == "hello there"
    assert chats.get(cid, tmp_path)["messages"][1]["answered_by"] == "groq/x"
    assert [r["id"] for r in chats.listing(root=tmp_path)] == [cid]
    assert chats.listing("HELLO", tmp_path) and not chats.listing("zzz", tmp_path)
    assert chats.rename(cid, "Renamed", tmp_path)
    assert chats.get(cid, tmp_path)["title"] == "Renamed"
    assert chats.delete(cid, tmp_path) and chats.get(cid, tmp_path) is None


def test_bad_ids_never_touch_the_disk(tmp_path):
    assert chats.get("../evil", tmp_path) is None
    assert not chats.delete("../evil", tmp_path)


def test_the_oldest_chats_are_dropped_past_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "MAX_CHATS", 3)
    ids = []
    for i in range(5):
        ids.append(chats.new_id())
        chats.save(ids[-1], {"messages": [{"role": "user", "content": str(i)}]}, tmp_path)
    assert len(chats.listing(root=tmp_path)) == 3
