"""Agentic RAG (AGENT_ENABLED=true): Gemini tự quyết định tìm tài liệu bao nhiêu lần rồi trả lời.

    câu hỏi + lịch sử ─► Gemini ─┬─► gọi tool ─► ToolSession chạy tool ─► kết quả gửi lại Gemini ─┐
                          ▲      │                                                              │
                          └──────┼──────────────────────────────────────────────────────────────┘
                                 └─► text: câu trả lời, stream về frontend

Vòng lặp viết tay (không dùng chế độ tự động của SDK) để phát sự kiện SSE và đếm lời gọi tool
giữa các vòng. Hai giới hạn:
- AGENT_MAX_TOOL_CALLS: số lần search_medical_docs (mỗi lần 12–30 s vì rerank). Hết lượt thì lần
  tìm tiếp nhận câu "hết lượt tìm", get_article (gần như tức thì) vẫn được gọi.
- AGENT_MAX_TOTAL_CALLS: mọi lời gọi tool, chống lặp vô hạn. Đủ số này thì vòng kế tiếp cấm gọi
  tool, buộc trả lời.

Chốt chặn bằng code (AGENT_NO_CONTEXT_GUARD=true): model định trả lời khi request chưa lấy được
chunk nào — không gọi tool, hoặc mọi lần tìm đều "không có tài liệu" — thì bỏ câu trả lời đó, sinh
lại bằng build_no_context_prompt như pipeline cũ. Nhờ vậy "không có tài liệu thì không được kể
thêm" vẫn do code bảo đảm, không chỉ dựa vào system instruction. Lời chào / câu ngoài lĩnh vực
cũng đi đường này (1 lần gọi Gemini ngắn, không tìm, không rerank).

Xem docs/AGENTIC_RAG.md.
"""

import logging
from collections.abc import Iterator
from typing import Any

from app.config import get_settings
from app.rag.guardrail import StreamRedactor
from app.rag.prompt import build_prompt
from app.rag.retriever import Retriever
from app.rag.tools import ToolSession, tool_declarations
from app.schemas import ChatMessage, Source
from app.services.gemini_service import GeminiService, to_contents
from app.services.rag_service import collect_sources, trim_history

logger = logging.getLogger(__name__)

TOOL_BUDGET_EXHAUSTED = "Đã hết lượt gọi tool cho câu hỏi này. Hãy trả lời bằng các tài liệu đã có."
SEARCH_BUDGET_EXHAUSTED = (
    "Đã hết lượt tìm kiếm cho câu hỏi này. Vẫn có thể dùng get_article với document_id đã có; "
    "nếu không cần thì trả lời bằng các tài liệu đã có."
)


class AgentService:
    def __init__(self, gemini: GeminiService, retriever: Retriever) -> None:
        self._gemini = gemini
        self._retriever = retriever
        self._tools = tool_declarations(retriever.disease_groups)

    def ask(self, message: str, history: list[ChatMessage]) -> tuple[str, list[Source]]:
        result = self.ask_with_trace(message, history)
        return result["reply"], result["sources"]

    def ask_with_context(
        self, message: str, history: list[ChatMessage]
    ) -> tuple[str, list[Source], list[dict], str]:
        """Cùng dạng với RagService.ask_with_context (cho eval_rag.py). "Câu dùng để retrieve" là
        các truy vấn agent đã tìm, nối bằng " | "."""
        result = self.ask_with_trace(message, history)
        queries = [call["args"].get("query", "") for call in result["trace"] if call["tool"] == "search_medical_docs"]
        return result["reply"], result["sources"], result["results"], " | ".join(queries)

    def ask_with_trace(self, message: str, history: list[ChatMessage]) -> dict[str, Any]:
        """Chạy cả request, trả về câu trả lời, nguồn, mọi chunk đã lấy và vết chạy:

            trace       mỗi lần gọi tool: tool, args, n_chunks, seconds, error (nếu có)
            fallback    None | "no_context" (chốt chặn) | "empty_answer" (model không trả text)
            dose_redactions  các cụm liều thuốc guardrail đã che trong câu trả lời
        """
        session = ToolSession(self._retriever)
        info: dict[str, Any] = {}
        reply = "".join(
            data["text"] for event, data in self._run(message, history, session, info) if event == "delta"
        )
        return {
            "reply": reply,
            "sources": collect_sources(session.results),
            "results": session.results,
            "trace": session.trace,
            "fallback": info.get("fallback"),
            "dose_redactions": info.get("dose_redactions", []),
        }

    def ask_stream(self, message: str, history: list[ChatMessage]) -> Iterator[tuple[str, dict]]:
        """Phát từng sự kiện `(tên, dữ liệu)`, cùng định dạng với RagService.ask_stream:

            ("status",  {"stage": "thinking"})                       — trước lần gọi Gemini đầu
            ("status",  {"stage": "retrieving", "query": "..."})     — mỗi lần search_medical_docs
            ("status",  {"stage": "reading", "document_id": "..."})  — mỗi lần get_article
            ("sources", {"sources": [...]})   — sau mỗi vòng gọi tool, danh sách đầy đủ tới lúc đó
            ("status",  {"stage": "generating"})
            ("delta",   {"text": "..."})
        """
        yield from self._run(message, history, ToolSession(self._retriever), {})

    def _run(
        self, message: str, history: list[ChatMessage], session: ToolSession, info: dict[str, Any]
    ) -> Iterator[tuple[str, dict]]:
        """Luồng sự kiện của agent, chữ của câu trả lời đi qua guardrail liều thuốc (DOSE_GUARDRAIL):
        cụm liều bị che trước khi tới frontend. Các cụm đã che ghi vào info["dose_redactions"]."""
        events = self._run_agent(message, history, session, info)
        if not get_settings().dose_guardrail:
            yield from events
            return
        redactor = StreamRedactor()
        for event, data in events:
            if event != "delta":
                yield event, data
                continue
            text = redactor.feed(data["text"])
            if text:
                yield "delta", {"text": text}
        text = redactor.flush()
        if text:
            yield "delta", {"text": text}
        if redactor.redacted:
            info["dose_redactions"] = redactor.redacted
            logger.warning("Guardrail che liều thuốc trong câu trả lời: %s", redactor.redacted)

    def _run_agent(
        self, message: str, history: list[ChatMessage], session: ToolSession, info: dict[str, Any]
    ) -> Iterator[tuple[str, dict]]:
        settings = get_settings()
        history = trim_history(history, settings.history_max_turns)
        contents: list[Any] = to_contents(history) + [{"role": "user", "parts": [message]}]
        searches = 0  # lần tìm (đắt: embed + rerank) — giới hạn bởi AGENT_MAX_TOOL_CALLS
        calls_made = 0  # mọi lời gọi tool, kể cả get_article (gần như tức thì) — AGENT_MAX_TOTAL_CALLS
        answered = False

        yield "status", {"stage": "thinking"}
        while True:
            allow_tools = calls_made < settings.agent_max_total_calls
            parts: list[Any] = []
            calls: list[Any] = []
            guarded = False
            for part in self._gemini.agent_turn(contents, self._tools, allow_tools):
                parts.append(part)
                if part.function_call.name:
                    calls.append(part.function_call)
                elif part.text:
                    if not answered and not session.results and settings.agent_no_context_guard:
                        guarded = True
                        break
                    if not answered:
                        yield "status", {"stage": "generating"}
                        answered = True
                    yield "delta", {"text": part.text}
            if guarded:
                info["fallback"] = "no_context"
                break
            if answered or not calls:
                # Đã trả lời (lời gọi tool lẫn sau text thì bỏ), hoặc model không trả gì.
                break
            if not allow_tools:
                logger.warning("Gemini vẫn gọi tool khi đã cấm (mode NONE), bỏ qua")
                break

            contents.append({"role": "model", "parts": parts})
            responses = []
            for call in calls:
                args = dict(call.args)
                is_search = call.name != "get_article"
                if calls_made >= settings.agent_max_total_calls:
                    output = TOOL_BUDGET_EXHAUSTED
                elif is_search and searches >= settings.agent_max_tool_calls:
                    output = SEARCH_BUDGET_EXHAUSTED
                    calls_made += 1
                else:
                    if is_search:
                        yield "status", {"stage": "retrieving", "query": args.get("query", "")}
                        searches += 1
                    else:
                        yield "status", {"stage": "reading", "document_id": args.get("document_id", "")}
                    output = session.run(call.name, args)
                    calls_made += 1
                responses.append({"function_response": {"name": call.name, "response": {"result": output}}})
            contents.append({"role": "user", "parts": responses})
            yield "sources", {"sources": [source.model_dump() for source in collect_sources(session.results)]}

        if not answered:
            # Chốt chặn (chưa có chunk nào) hoặc model không sinh text: trả lời như pipeline cũ, bằng
            # prompt ghép sẵn — có chunk thì build_rag_prompt, không có thì build_no_context_prompt.
            info.setdefault("fallback", "empty_answer")
            yield "status", {"stage": "generating"}
            for text in self._gemini.ask_stream(build_prompt(message, session.results), history):
                yield "delta", {"text": text}


_service: AgentService | None = None


def get_agent_service() -> AgentService:
    global _service
    if _service is None:
        _service = AgentService(GeminiService(), Retriever())
    return _service
