from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


class VisualEncoder(Protocol):
    """Small seam that keeps unit tests independent from model weights."""

    model_name: str
    model_version: str
    preprocessing: dict[str, object]
    device: str

    def encode(self, image_paths: list[Path]) -> np.ndarray: ...


@dataclass
class SiglipEncoder:
    """Lazy SigLIP loader; imports heavyweight ML dependencies only for a real build."""

    model_name: str = "google/siglip2-base-patch16-384"
    device: str | None = None

    def __post_init__(self) -> None:
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as err:  # pragma: no cover - exercised by a real build environment
            raise RuntimeError(
                "Visual embeddings require torch, transformers, and Pillow. "
                "Install the project runtime dependencies before running this build."
            ) from err

        self._torch = torch
        self.device = self.device or ("mps" if torch.backends.mps.is_available() else "cpu")
        from huggingface_hub import snapshot_download

        self._snapshot_path = Path(snapshot_download(self.model_name, local_files_only=True))
        # The pinned snapshot is fetched before a build; the build itself must not depend on DNS.
        self._processor = AutoProcessor.from_pretrained(self._snapshot_path, local_files_only=True)
        self._model = AutoModel.from_pretrained(self._snapshot_path, local_files_only=True).to(self.device).eval()
        self.model_version = self._resolved_model_version()
        self.preprocessing = {
            "image_size": [384, 384],
            "interpolation": "bicubic",
            "normalization": "siglip",
        }

    def _resolved_model_version(self) -> str:
        """Use an immutable Hub revision, with a weights digest only as a real fallback."""

        revision = getattr(self._model.config, "_commit_hash", None)
        if isinstance(revision, str) and revision:
            return revision
        snapshot = self._snapshot_path
        if len(snapshot.name) == 40 and all(character in "0123456789abcdef" for character in snapshot.name):
            return snapshot.name
        weight_files = sorted(snapshot.glob("*.safetensors"))
        if not weight_files:
            raise RuntimeError("SigLIP loaded without a resolvable revision or weights file")
        digest = hashlib.sha256(weight_files[0].read_bytes()).hexdigest()
        return f"weights-sha256:{digest}"

    def encode(self, image_paths: list[Path]) -> np.ndarray:
        from PIL import Image

        images = []
        for path in image_paths:
            with Image.open(path) as image:
                images.append(image.convert("RGB"))
        inputs = self._processor(images=images, return_tensors="pt")
        inputs = {name: value.to(self.device) for name, value in inputs.items()}
        with self._torch.no_grad():
            vectors = self._model.get_image_features(**inputs)
        return vectors.detach().float().cpu().numpy()
