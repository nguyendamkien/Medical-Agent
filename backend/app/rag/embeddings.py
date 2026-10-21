"""OpenAI embedding adapter used by both indexing and retrieval."""

from collections.abc import Sequence
from functools import lru_cache

from openai import OpenAI

from app.config import get_settings


@lru_cache
def _get_client() -> OpenAI:
	settings = get_settings()
	if not settings.openai_api_key:
		raise RuntimeError("Thiếu OPENAI_API_KEY để tạo embedding.")
	# Mặc định SDK chờ tới 600 s mỗi lần gọi (× 3 lần thử) — khi mạng chập, một lần embed câu hỏi
	# treo hàng chục phút. 30 s là quá đủ cho một request embedding / giám khảo bình thường.
	return OpenAI(api_key=settings.openai_api_key, timeout=30.0, max_retries=2)


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
	"""Create embeddings for a batch of texts in a single OpenAI request."""
	if not texts:
		return []
	settings = get_settings()
	response = _get_client().embeddings.create(
		model=settings.embedding_model,
		input=list(texts),
	)
	return [item.embedding for item in sorted(response.data, key=lambda item: item.index)]


def embed_query(text: str) -> list[float]:
	return embed_texts([text])[0]
