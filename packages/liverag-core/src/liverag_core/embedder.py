"""Wrapper around the embedding model."""

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-small-en-v1.5"

# bge models are trained to put this instruction in front of search *queries* only.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder:
    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self.model_name = model_name
        self.model = SentenceTransformer(model_name)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        # Normalised vectors have length 1, so cosine distance only compares direction.
        return self.model.encode(texts, batch_size=32, normalize_embeddings=True,
                                 show_progress_bar=True)

    def embed_query(self, text: str) -> np.ndarray:
        return self.model.encode(QUERY_PREFIX + text, normalize_embeddings=True)