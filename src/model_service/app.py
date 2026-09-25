from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from threading import Lock

import bm25s
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from transformers import AutoModel, AutoTokenizer

app = FastAPI(title="VSF local model service")

_MODEL_NAME = os.environ.get("TEXT_EMBEDDING_MODEL", "BAAI/bge-m3")
_LEXICAL_ROOT = Path(os.environ.get("LEXICAL_INDEX_ROOT", "data/indexes/lexical"))
_models_lock = Lock()
_lexical_lock = Lock()
_tokenizer = None
_text_model = None


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


class LexicalRequest(BaseModel):
    chunk_id: str
    text: str


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


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


@app.post("/v1/index/lexical")
def index_lexical(request: LexicalRequest) -> dict[str, str]:
    """Rebuild a versioned BM25 artifact from immutable closed chunks.

    The Go worker is deliberately single-threaded. The local lock is retained
    as a defensive guard for accidental duplicate HTTP callers, not a scaling primitive.
    """

    with _lexical_lock:
        try:
            _LEXICAL_ROOT.mkdir(parents=True, exist_ok=True)
            docs_path = _LEXICAL_ROOT / "documents.json"
            documents = json.loads(docs_path.read_text(encoding="utf-8")) if docs_path.exists() else {}
            documents[request.chunk_id] = request.text
            _atomic_json(docs_path, documents)
            ordered_ids = sorted(documents)
            corpus = [documents[chunk_id] for chunk_id in ordered_ids]
            tokenized = bm25s.tokenize(corpus, stopwords=None, show_progress=False)
            index = bm25s.BM25(k1=1.2, b=0.75)
            index.index(tokenized, show_progress=False)
            version = hashlib.sha256(json.dumps(ordered_ids, separators=(",", ":")).encode()).hexdigest()[:16]
            versions = _LEXICAL_ROOT / "versions"
            temporary = Path(tempfile.mkdtemp(prefix="build-", dir=versions if versions.exists() else _LEXICAL_ROOT))
            try:
                index.save(temporary, corpus=[{"chunk_id": chunk_id, "text": documents[chunk_id]} for chunk_id in ordered_ids])
                final = versions / version
                versions.mkdir(parents=True, exist_ok=True)
                if not final.exists():
                    temporary.replace(final)
                else:
                    shutil.rmtree(temporary)
                _atomic_json(_LEXICAL_ROOT / "current.json", {"version": version, "chunk_ids": ordered_ids})
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
            return {"status": "indexed"}
        except Exception as error:
            raise HTTPException(status_code=500, detail={"error_code": "INTERNAL", "message": str(error)}) from error


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ready"}
