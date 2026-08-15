"""Thin wrapper around a local embedding model.

Model choice: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
via fastembed.

Why this model:
- Multilingual (Chinese + English). JDs are pasted in Chinese; the fact
  bank is written in English. A Chinese-only or English-only model would
  silently fail to match "上下文工程" against "Context Engineering".
- Small (~220MB) and CPU-only via ONNX runtime (fastembed), not torch.
  torch wheels commonly lag new Python releases; this machine runs
  Python 3.14, so avoiding torch sidesteps a real install risk instead
  of a hypothetical one.
- No API key, no network call at query time: the fact bank is private
  data, and a local model keeps it local.

Why not a bigger model: the corpus is ~6 facts. A larger model would cost
more download/compute for no measurable retrieval-quality gain at this
scale. This is a scoping decision, not a default.
"""

from __future__ import annotations

import numpy as np
import warnings

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
POOLING_BEHAVIOR = "fastembed-0.8-default-mean"

_model = None


def _get_model():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        # fastembed 0.8 intentionally changed this model to its declared mean
        # pooling. The version and behavior are pinned in requirements/index meta.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=r".*now uses mean pooling instead of CLS embedding.*")
            _model = TextEmbedding(model_name=MODEL_NAME)
    return _model


def embed_texts(texts: list[str]) -> np.ndarray:
    """Return an (n_texts, dim) float32 array, one row per input text."""
    model = _get_model()
    vectors = list(model.embed(texts))
    return np.array(vectors, dtype=np.float32)


if __name__ == "__main__":
    demo = embed_texts(["上下文工程", "Context Engineering", "香蕉"])
    print("shape:", demo.shape)
