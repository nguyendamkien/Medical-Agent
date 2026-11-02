import logging
from collections.abc import Iterator

from app.config import get_settings
from app.observability import NOOP, Observation
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
        self,
        message: str,
        history: list[ChatMessage],
        profile: UserProfile | None = None,
        trace: Observation = NOOP,
    ) -> tuple[str, list[Source]]:
        reply, sources, _, _ = self.ask_with_context(message, history, profile, trace)
        return reply, sources

    def ask_with_context(
        self,
        message: str,
        history: list[ChatMessage],
        profile: UserProfile | None = None,
        trace: Observation = NOOP,
    ) -> tuple[str, list[Source], list[dict], str]:
        """Như ask(), trả thêm các chunk đã retrieve và câu dùng để retrieve (cho đánh giá RAG).

        `profile` chỉ vào prompt trả lời, KHÔNG dùng để tìm kiếm (pipeline này không để model
        tự viết truy vấn), nên "tôi ăn hải sản được không" vẫn tìm chung chung — agent làm tốt hơn.
        `trace`: trace Langfuse của request (app/observability.py); mặc định không ghi."""
        history = trim_history(history, get_settings().history_max_turns)
        search_query = self.build_search_query(message, history, trace)
        results = self._retrieve(search_query, trace)
        # Gemini vẫn nhận câu hỏi gốc + lịch sử; câu viết lại chỉ dùng để tìm tài liệu.
        prompt = build_prompt(message, results, profile)
        usage: dict[str, int] = {}
        generation = self._start_generation(trace, prompt, results)
        try:
            reply = self._gemini.ask(prompt, history, usage=usage)
            generation.update(output=reply)
        finally:
            generation.update(usage=usage)
            generation.end()
        trace.update(metadata={"pipeline": "rag", "n_chunks": len(results)})
        return reply, collect_sources(results), results, search_query

    def ask_stream(
        self,
        message: str,
        history: list[ChatMessage],
        profile: UserProfile | None = None,
        trace: Observation = NOOP,
    ) -> Iterator[tuple[str, dict]]:
        """Như ask(), nhưng phát từng sự kiện `(tên, dữ liệu)` để frontend hiển thị dần:

            ("status",  {"stage": "rewriting" | "retrieving" | "generating"})
            ("sources", {"sources": [...]})      — ngay sau khi retrieve xong
            ("delta",   {"text": "..."})          — từng đoạn câu trả lời
        """
        history = trim_history(history, get_settings().history_max_turns)
        if history and get_settings().query_rewrite_enabled:
            yield "status", {"stage": "rewriting"}
        search_query = self.build_search_query(message, history, trace)

        yield "status", {"stage": "retrieving"}
        results = self._retrieve(search_query, trace)
        yield "sources", {"sources": [source.model_dump() for source in collect_sources(results)]}

        yield "status", {"stage": "generating"}
        prompt = build_prompt(message, results, profile)
        usage: dict[str, int] = {}
        texts: list[str] = []
        generation = self._start_generation(trace, prompt, results)
        try:
            for text in self._gemini.ask_stream(prompt, history, usage=usage):
                texts.append(text)
                yield "delta", {"text": text}
        finally:
            generation.update(output="".join(texts), usage=usage)
            generation.end()
        trace.update(metadata={"pipeline": "rag", "n_chunks": len(results)})

    def build_search_query(self, message: str, history: list[ChatMessage], trace: Observation = NOOP) -> str:
        """Câu dùng để retrieve: câu hỏi nối tiếp được viết lại thành câu độc lập."""
        if not history or not get_settings().query_rewrite_enabled:
            return message
        usage: dict[str, int] = {}
        span = trace.child("gemini.rewrite_query", as_type="generation", model=get_settings().gemini_model, input=message)
        try:
            rewritten = self._gemini.rewrite_query(message, history, usage=usage).strip().strip('"“”').strip()
        except Exception as exc:  # lỗi gọi Gemini → vẫn trả lời được, chỉ retrieve kém hơn
            logger.exception("Viết lại câu hỏi thất bại, dùng câu gốc")
            span.update(level="ERROR", status_message=str(exc))
            span.end()
            return message
        span.update(output=rewritten, usage=usage)
        span.end()
        if not rewritten or len(rewritten) > MAX_REWRITTEN_CHARS:
            return message
        return rewritten

    def _retrieve(self, query: str, trace: Observation) -> list[dict]:
        span = trace.child("retrieve", as_type="retriever", input=query)
        results = self._retriever.search(query)
        span.update(
            output=[f"{r['metadata'].get('title', '')} | {r['metadata'].get('section', '')}" for r in results],
            metadata={
                "n_chunks": len(results),
                "top_score": round(results[0]["score"], 3) if results and results[0].get("score") is not None else None,
            },
        )
        span.end()
        return results

    @staticmethod
    def _start_generation(trace: Observation, prompt: str, results: list[dict]) -> Observation:
        return trace.child(
            "gemini.answer", as_type="generation", model=get_settings().gemini_model, input=prompt,
            metadata={"has_chunks": bool(results)},
        )


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