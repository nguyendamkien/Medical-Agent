import logging
from collections.abc import Iterator

from app.config import get_settings
from app.rag.profile import UserProfile
from app.rag.prompt import build_prompt, source_key
from app.rag.retriever import Retriever
from app.schemas import ChatMessage, Source
from app.services.gemini_service import GeminiService

logger = logging.getLogger(__name__)

# Câu viết lại dài bất thường thường là Gemini trả lời luôn thay vì viết lại → bỏ, dùng câu gốc.
MAX_REWRITTEN_CHARS = 300


def trim_history(history: list[ChatMessage], max_turns: int) -> list[ChatMessage]:
    """Giữ `max_turns` lượt hỏi–đáp gần nhất; bỏ tin nhắn bot đứng đầu (vd lời chào) để
    lịch sử luôn bắt đầu bằng câu của người dùng."""
    trimmed = history[-2 * max_turns:] if max_turns > 0 else []
    while trimmed and trimmed[0].role != "user":
        trimmed = trimmed[1:]
    return trimmed


class RagService:
    def __init__(self, gemini: GeminiService, retriever: Retriever) -> None:
        self._gemini = gemini
        self._retriever = retriever

    def ask(
        self, message: str, history: list[ChatMessage], profile: UserProfile | None = None
    ) -> tuple[str, list[Source]]:
        reply, sources, _, _ = self.ask_with_context(message, history, profile)
        return reply, sources

    def ask_with_context(
        self, message: str, history: list[ChatMessage], profile: UserProfile | None = None
    ) -> tuple[str, list[Source], list[dict], str]:
        """Như ask(), trả thêm các chunk đã retrieve và câu dùng để retrieve (cho đánh giá RAG).

        `profile` chỉ vào prompt trả lời, KHÔNG dùng để tìm kiếm (pipeline này không để model
        tự viết truy vấn), nên "tôi ăn hải sản được không" vẫn tìm chung chung — agent làm tốt hơn."""
        history = trim_history(history, get_settings().history_max_turns)
        search_query = self.build_search_query(message, history)
        results = self._retriever.search(search_query)
        # Gemini vẫn nhận câu hỏi gốc + lịch sử; câu viết lại chỉ dùng để tìm tài liệu.
        prompt = build_prompt(message, results, profile)
        reply = self._gemini.ask(prompt, history)
        return reply, collect_sources(results), results, search_query

    def ask_stream(
        self, message: str, history: list[ChatMessage], profile: UserProfile | None = None
    ) -> Iterator[tuple[str, dict]]:
        """Như ask(), nhưng phát từng sự kiện `(tên, dữ liệu)` để frontend hiển thị dần:

            ("status",  {"stage": "rewriting" | "retrieving" | "generating"})
            ("sources", {"sources": [...]})      — ngay sau khi retrieve xong
            ("delta",   {"text": "..."})          — từng đoạn câu trả lời
        """
        history = trim_history(history, get_settings().history_max_turns)
        if history and get_settings().query_rewrite_enabled:
            yield "status", {"stage": "rewriting"}
        search_query = self.build_search_query(message, history)

        yield "status", {"stage": "retrieving"}
        results = self._retriever.search(search_query)
        yield "sources", {"sources": [source.model_dump() for source in collect_sources(results)]}

        yield "status", {"stage": "generating"}
        prompt = build_prompt(message, results, profile)
        for text in self._gemini.ask_stream(prompt, history):
            yield "delta", {"text": text}

    def build_search_query(self, message: str, history: list[ChatMessage]) -> str:
        """Câu dùng để retrieve: câu hỏi nối tiếp được viết lại thành câu độc lập."""
        if not history or not get_settings().query_rewrite_enabled:
            return message
        try:
            rewritten = self._gemini.rewrite_query(message, history).strip().strip('"“”').strip()
        except Exception:  # lỗi gọi Gemini → vẫn trả lời được, chỉ retrieve kém hơn
            logger.exception("Viết lại câu hỏi thất bại, dùng câu gốc")
            return message
        if not rewritten or len(rewritten) > MAX_REWRITTEN_CHARS:
            return message
        return rewritten


def collect_sources(results: list[dict]) -> list[Source]:
    """Mỗi bài một nguồn, theo đúng thứ tự đánh số [Nguồn i] của build_context."""
    sources: dict[str, Source] = {}
    for result in results:
        metadata = result["metadata"]
        key = source_key(metadata)
        if key not in sources:
            sources[key] = Source(
                title=metadata.get("title", ""),
                url=metadata.get("url", ""),
                citation=metadata.get("citation") or None,
            )
    return list(sources.values())


_service: RagService | None = None


def get_rag_service() -> RagService:
    global _service
    if _service is None:
        _service = RagService(GeminiService(), Retriever())
    return _service