from __future__ import annotations

import os
from io import BytesIO
from threading import Lock

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModel, AutoProcessor, AutoTokenizer

app = FastAPI(title="VSF local model service")

_MODEL_NAME = os.environ.get("TEXT_EMBEDDING_MODEL", "BAAI/bge-m3")
_models_lock = Lock()
_tokenizer = None
_text_model = None
_vision_model = None
_vision_processor = None
_vision_model_version = None


class ExistingChunk(BaseModel):
    message_ids: list[str]
    text: str


class NewMessage(BaseModel):
    message_id: str
    sender_id: str
    text: str
    media_ids: list[str] = Field(default_factory=list)


class AppendRequest(BaseModel):
    channel_id: str
    day: str
    existing_chunk: ExistingChunk | None = None
    new_message: NewMessage


class AppendResponse(BaseModel):
    assembled_text: str
    assembled_message_ids: list[str]
    assembled_media_ids: list[str]
    token_count: int


class TextEmbeddingRequest(BaseModel):
    first_message_id: str
    text: str


class TextEmbeddingResponse(BaseModel):
    model_version: str
    vector: list[float]


class ImageEmbeddingRequest(BaseModel):
    media_id: str
    storage_object_ref: str


class ImageEmbeddingResponse(BaseModel):
    model_version: str
    vector: list[float]


def _load_tokenizer():
    global _tokenizer
    with _models_lock:
        if _tokenizer is None:
            _tokenizer = AutoTokenizer.from_pretrained(_MODEL_NAME)
        return _tokenizer


def _load_text_model():
    global _text_model
    with _models_lock:
        if _text_model is None:
            _text_model = AutoModel.from_pretrained(_MODEL_NAME).eval()
        return _text_model


def _load_vision_model():
    """Load the pinned SigLIP image encoder only when a live upload needs it."""

    global _vision_model, _vision_processor, _vision_model_version
    with _models_lock:
        if _vision_model is None:
            model_name = os.environ.get("VISUAL_EMBEDDING_MODEL", "google/siglip2-base-patch16-384")
            _vision_processor = AutoProcessor.from_pretrained(model_name, local_files_only=True)
            _vision_model = AutoModel.from_pretrained(model_name, local_files_only=True).eval()
            _vision_model_version = getattr(_vision_model.config, "_commit_hash", None) or model_name
        return _vision_processor, _vision_model, _vision_model_version


def _load_object(storage_object_ref: str) -> bytes:
    """Read only the configured local MinIO object; callers never provide a URL."""

    from minio import Minio

    endpoint = os.environ.get("MINIO_ENDPOINT", "http://localhost:9000")
    client = Minio(
        endpoint.removeprefix("http://").removeprefix("https://"),
        access_key=os.environ.get("MINIO_ROOT_USER", "vsf_minio"),
        secret_key=os.environ.get("MINIO_ROOT_PASSWORD", "vsf_minio_local_only"),
        secure=endpoint.startswith("https://"),
    )
    response = client.get_object(os.environ.get("MINIO_BUCKET", "vsf-media"), storage_object_ref)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def _message_line(message: NewMessage) -> str:
    """Preserve media at its message position so later readers see its local caption."""

    media = " ".join(f"[image:{media_id}]" for media_id in message.media_ids)
    return f"{message.sender_id}: {message.text}" + (f" {media}" if media else "")


@app.post("/v1/chunk/append", response_model=AppendResponse)
def append_chunk(request: AppendRequest) -> AppendResponse:
    try:
        tokenizer = _load_tokenizer()
        line = _message_line(request.new_message)
        if request.existing_chunk is None:
            # The header is created only once, so reassembly is deterministic across seed/live paths.
            text = f"[conversation:{request.channel_id}] [day:{request.day}]\n{line}"
            message_ids = [request.new_message.message_id]
            media_ids = list(request.new_message.media_ids)
        else:
            text = request.existing_chunk.text + "\n" + line
            message_ids = [*request.existing_chunk.message_ids, request.new_message.message_id]
            # Go owns the existing media list; the RPC contract intentionally sends
            # only existing text/ids, so return this message's new media here.
            media_ids = list(request.new_message.media_ids)
        token_count = len(tokenizer.encode(text, add_special_tokens=False))
        return AppendResponse(
            assembled_text=text,
            assembled_message_ids=message_ids,
            assembled_media_ids=list(dict.fromkeys(media_ids)),
            token_count=token_count,
        )
    except Exception as error:  # Model availability must degrade search, not masquerade as a bad request.
        raise HTTPException(status_code=503, detail={"error_code": "MODEL_UNAVAILABLE", "message": str(error)}) from error


@app.post("/v1/embed/text", response_model=TextEmbeddingResponse)
def embed_text(request: TextEmbeddingRequest) -> TextEmbeddingResponse:
    try:
        tokenizer, model = _load_tokenizer(), _load_text_model()
        inputs = tokenizer(request.text, return_tensors="pt", truncation=True, max_length=8192)
        with torch.inference_mode():
            output = model(**inputs).last_hidden_state
            mask = inputs["attention_mask"].unsqueeze(-1)
            vector = (output * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            vector = torch.nn.functional.normalize(vector, p=2, dim=1)[0].cpu().tolist()
        if len(vector) != 1024:
            raise ValueError(f"bge-m3 dimension is {len(vector)}, expected 1024")
        return TextEmbeddingResponse(model_version=_MODEL_NAME, vector=vector)
    except Exception as error:
        raise HTTPException(status_code=503, detail={"error_code": "MODEL_UNAVAILABLE", "message": str(error)}) from error


@app.post("/v1/embed/image", response_model=ImageEmbeddingResponse)
def embed_image(request: ImageEmbeddingRequest) -> ImageEmbeddingResponse:
    """Embed a newly bound image without exposing object-store credentials to Go callers."""

    try:
        from PIL import Image

        processor, model, model_version = _load_vision_model()
        with Image.open(BytesIO(_load_object(request.storage_object_ref))) as image:
            inputs = processor(images=image.convert("RGB"), return_tensors="pt")
        with torch.inference_mode():
            vector = model.get_image_features(**inputs)
            vector = torch.nn.functional.normalize(vector, p=2, dim=1)[0].cpu().tolist()
        if len(vector) != 768:
            raise ValueError(f"SigLIP dimension is {len(vector)}, expected 768")
        return ImageEmbeddingResponse(
            model_version=model_version,
            vector=vector,
        )
    except Exception as error:
        raise HTTPException(status_code=503, detail={"error_code": "MODEL_UNAVAILABLE", "message": str(error)}) from error


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ready"}
