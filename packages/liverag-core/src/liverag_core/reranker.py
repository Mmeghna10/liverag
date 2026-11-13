"""Cross-encoder reranker: reads the question and a chunk together and scores how well they match."""

from sentence_transformers import CrossEncoder

MODEL_NAME = "BAAI/bge-reranker-base"


class Reranker:
    def __init__(self, model_name: str = MODEL_NAME) -> None:
        self.model_name = model_name
        self.model = CrossEncoder(model_name, max_length=512)

    def score(self, query: str, texts: list[str]) -> list[float]:
        """One relevance score per text, between 0 (unrelated) and 1 (answers the query)."""
        return self.model.predict([(query, text) for text in texts], batch_size=16).tolist()