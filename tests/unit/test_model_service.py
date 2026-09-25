from __future__ import annotations

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


def test_embed_image_reads_the_configured_object_and_normalizes(monkeypatch) -> None:
    from io import BytesIO

    import torch
    from PIL import Image

    image = Image.new("RGB", (2, 2), color="red")
    content = BytesIO()
    image.save(content, format="PNG")

    class _Model:
        def get_image_features(self, **_inputs):
            return torch.ones((1, 768))

    monkeypatch.setattr(app, "_load_object", lambda reference: content.getvalue())
    monkeypatch.setattr(app, "_load_vision_model", lambda: (lambda **_kwargs: {}, _Model(), "fixture-revision"))
    response = app.embed_image(app.ImageEmbeddingRequest(media_id="m1", storage_object_ref="drafts/m1"))
    assert response.model_version == "fixture-revision"
    assert len(response.vector) == 768
    assert round(sum(value * value for value in response.vector), 6) == 1.0
