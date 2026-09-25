from __future__ import annotations

import json
from pathlib import Path

from model_service import app


class _Tokenizer:
    def encode(self, text: str, *, add_special_tokens: bool) -> list[str]:
        assert add_special_tokens is False
        return text.split()


def test_append_preserves_header_order_and_inline_media(monkeypatch) -> None:
    monkeypatch.setattr(app, "_load_tokenizer", lambda: _Tokenizer())
    first = app.append_chunk(
        app.AppendRequest(
            channel_id="demo_channel",
            day="2026-09-23",
            new_message={"message_id": "m1", "sender_id": "an", "text": "hello", "media_ids": ["img-1"]},
        )
    )
    second = app.append_chunk(
        app.AppendRequest(
            channel_id="demo_channel",
            day="2026-09-23",
            existing_chunk={"message_ids": first.assembled_message_ids, "text": first.assembled_text},
            new_message={"message_id": "m2", "sender_id": "binh", "text": "reply"},
        )
    )
    assert second.assembled_message_ids == ["m1", "m2"]
    assert second.assembled_text == "[conversation:demo_channel] [day:2026-09-23]\nan: hello [image:img-1]\nbinh: reply"


def test_lexical_rebuild_is_idempotent_and_keeps_prior_chunks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app, "_LEXICAL_ROOT", tmp_path / "lexical")
    app.index_lexical(app.LexicalRequest(chunk_id="chunk-a", text="blockchain wallet"))
    app.index_lexical(app.LexicalRequest(chunk_id="chunk-b", text="restaurant photo"))
    app.index_lexical(app.LexicalRequest(chunk_id="chunk-a", text="blockchain wallet"))
    current = json.loads((tmp_path / "lexical" / "current.json").read_text(encoding="utf-8"))
    assert current["chunk_ids"] == ["chunk-a", "chunk-b"]
