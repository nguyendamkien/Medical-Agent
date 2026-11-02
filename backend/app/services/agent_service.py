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
from app.observability import NOOP, Observation
from app.rag.guardrail import StreamRedactor
from app.rag.profile import UserProfile, profile_instruction
from app.rag.prompt import build_prompt
from app.rag.retriever import Retriever
from app.rag.tools import FACILITY_TOOL, ToolSession, tool_declarations
from app.schemas import ChatMessage, Source
from app.services.gemini_service import GeminiService, to_contents
from app.services.rag_service import collect_sources, trim_history

logger = logging.getLogger(__name__)

TOOL_BUDGET_EXHAUSTED = "Đã hết lượt gọi tool cho câu hỏi này. Hãy trả lời bằng các tài liệu đã có."
FACILITY_FALLBACK_TEXT = (
    "Dưới đây là một số cơ sở y tế ở Hà Nội phù hợp. Bạn nên gọi trước để xác nhận lịch khám. "
    "Nếu có dấu hiệu cấp cứu, hãy gọi ngay 115."
)
SEARCH_BUDGET_EXHAUSTED = (
    "Đã hết lượt tìm kiếm cho câu hỏi này. Vẫn có thể dùng get_article với document_id đã có; "
    "nếu không cần thì trả lời bằng các tài liệu đã có."
)


class AgentService:
    def __init__(self, gemini: GeminiService, retriever: Retriever) -> None:
        self._gemini = gemini
        self._retriever = retriever
        self._tools = tool_declarations(retriever.disease_groups)

    def ask(
        self,
        message: str,
        history: list[ChatMessage],
        profile: UserProfile | None = None,
        trace: Observation = NOOP,
    ) -> tuple[str, list[Source]]:
        result = self.ask_with_trace(message, history, profile, trace)
        return result["reply"], result["sources"]

    def ask_with_context(
        self,
        message: str,
        history: list[ChatMessage],
        profile: UserProfile | None = None,
        trace: Observation = NOOP,
    ) -> tuple[str, list[Source], list[dict], str]:
        """Cùng dạng với RagService.ask_with_context (cho eval_rag.py). "Câu dùng để retrieve" là
        các truy vấn agent đã tìm, nối bằng " | "."""
        result = self.ask_with_trace(message, history, profile, trace)
        queries = [call["args"].get("query", "") for call in result["trace"] if call["tool"] == "search_medical_docs"]
        return result["reply"], result["sources"], result["results"], " | ".join(queries)

    def ask_with_trace(
        self,
        message: str,
        history: list[ChatMessage],
        profile: UserProfile | None = None,
        trace: Observation = NOOP,
    ) -> dict[str, Any]:
        """Chạy cả request, trả về câu trả lời, nguồn, mọi chunk đã lấy và vết chạy:

            trace       mỗi lần gọi tool: tool, args, n_chunks, seconds, error / allergy_hits (nếu có)
            fallback    None | "no_context" (chốt chặn) | "empty_answer" (model không trả text)
            dose_redactions  các cụm liều thuốc guardrail đã che trong câu trả lời

        `profile`: hồ sơ sức khoẻ của người hỏi (None = không có / người dùng tắt).
        `trace`: trace Langfuse của request (app/observability.py); mặc định không ghi."""
        session = self._session(profile)
        info: dict[str, Any] = {}
        reply = "".join(
            data["text"]
            for event, data in self._run(message, history, session, info, profile, trace)
            if event == "delta"
        )
        return {
            "reply": reply,
            "sources": collect_sources(session.results),
            "results": session.results,
            "trace": session.trace,
            "fallback": info.get("fallback"),
            "dose_redactions": info.get("dose_redactions", []),
            "facilities": session.facilities,
        }

    def ask_stream(
        self,
        message: str,
        history: list[ChatMessage],
        profile: UserProfile | None = None,
        trace: Observation = NOOP,
    ) -> Iterator[tuple[str, dict]]:
        """Phát từng sự kiện `(tên, dữ liệu)`, cùng định dạng với RagService.ask_stream:

            ("status",  {"stage": "thinking"})                       — trước lần gọi Gemini đầu
            ("status",  {"stage": "retrieving", "query": "..."})     — mỗi lần search_medical_docs
            ("status",  {"stage": "reading", "document_id": "..."})  — mỗi lần get_article
            ("status",  {"stage": "finding_facility"})               — mỗi lần find_healthcare_facility
            ("sources", {"sources": [...]})   — sau mỗi vòng gọi tool, danh sách đầy đủ tới lúc đó
            ("facilities", {"facilities": [...]})  — sau vòng có gợi ý nơi khám, danh sách đầy đủ
            ("status",  {"stage": "generating"})
            ("delta",   {"text": "..."})
        """
        yield from self._run(message, history, self._session(profile), {}, profile, trace)

    def _session(self, profile: UserProfile | None) -> ToolSession:
        return ToolSession(self._retriever, allergies=profile.allergies if profile else None)

    def _run(
        self,
        message: str,
        history: list[ChatMessage],
        session: ToolSession,
        info: dict[str, Any],
        profile: UserProfile | None = None,
        trace: Observation = NOOP,
    ) -> Iterator[tuple[str, dict]]:
        """Luồng sự kiện của agent, chữ của câu trả lời đi qua guardrail liều thuốc (DOSE_GUARDRAIL):
        cụm liều bị che trước khi tới frontend. Các cụm đã che ghi vào info["dose_redactions"]."""
        events = self._run_agent(message, history, session, info, profile, trace)
        redactor = StreamRedactor() if get_settings().dose_guardrail else None
        for event, data in events:
            if event != "delta" or redactor is None:
                yield event, data
                continue
            text = redactor.feed(data["text"])
            if text:
                yield "delta", {"text": text}
        if redactor is not None:
            text = redactor.flush()
            if text:
                yield "delta", {"text": text}
            if redactor.redacted:
                info["dose_redactions"] = redactor.redacted
                logger.warning("Guardrail che liều thuốc trong câu trả lời: %s", redactor.redacted)
                guard = trace.child(
                    "guardrail.dose", as_type="guardrail", input=redactor.redacted,
                    metadata={"n_redactions": len(redactor.redacted)},
                )
                guard.update(level="WARNING", status_message="Che liều thuốc trong câu trả lời")
                guard.end()
        # Tóm tắt request lên trace gốc: chỉ số đếm, không có nội dung.
        trace.update(metadata={
            "pipeline": "agent",
            "fallback": info.get("fallback"),
            "n_tool_calls": len(session.trace),
            "n_searches": session.searches,
            "n_chunks": len(session.results),
            "n_facilities": len(session.facilities),
            "n_dose_redactions": len(info.get("dose_redactions", [])),
        })

    def _run_agent(
        self,
        message: str,
        history: list[ChatMessage],
        session: ToolSession,
        info: dict[str, Any],
        profile: UserProfile | None,
        trace: Observation = NOOP,
    ) -> Iterator[tuple[str, dict]]:
        settings = get_settings()
        # Hồ sơ người hỏi nằm trong system instruction ở MỌI vòng, để model dùng nó ngay khi viết
        # truy vấn tìm kiếm đầu tiên (docs/HEALTH_PROFILE.md).
        extra_instruction = profile_instruction(profile)
        history = trim_history(history, settings.history_max_turns)
        contents: list[Any] = to_contents(history) + [{"role": "user", "parts": [message]}]
        searches = 0  # lần tìm (đắt: embed + rerank) — giới hạn bởi AGENT_MAX_TOOL_CALLS
        calls_made = 0  # mọi lời gọi tool, kể cả get_article (gần như tức thì) — AGENT_MAX_TOTAL_CALLS
        answered = False
        turn_number = 0

        yield "status", {"stage": "thinking"}
        while True:
            allow_tools = calls_made < settings.agent_max_total_calls
            parts: list[Any] = []
            calls: list[Any] = []
            texts: list[str] = []
            guarded = False
            turn_number += 1
            usage: dict[str, int] = {}
            # Mỗi lần gọi Gemini là một "generation" trên trace. Chỉ dựng input khi thật sự gửi nội dung.
            turn = trace.child(
                "gemini.agent_turn", as_type="generation", model=settings.gemini_model,
                input=_contents_for_trace(contents) if trace.enabled and not trace.masked else None,
                metadata={"turn": turn_number, "allow_tools": allow_tools, "has_profile": bool(extra_instruction)},
            )
            try:
                for part in self._gemini.agent_turn(
                    contents, self._tools, allow_tools, extra_instruction, usage=usage
                ):
                    parts.append(part)
                    if part.function_call.name:
                        calls.append(part.function_call)
                    elif part.text:
                        if not answered and not session.has_content and settings.agent_no_context_guard:
                            guarded = True
                            break
                        if not answered:
                            yield "status", {"stage": "generating"}
                            answered = True
                        texts.append(part.text)
                        yield "delta", {"text": part.text}
            finally:
                turn.update(
                    output={"text": "".join(texts) or None, "tool_calls": [_call_for_trace(c) for c in calls]},
                    usage=usage,
                    level="WARNING" if guarded else None,
                    status_message="Chốt chặn: model định trả lời khi chưa có tài liệu" if guarded else None,
                )
                turn.end()
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
                # Chỉ search_medical_docs tính vào AGENT_MAX_TOOL_CALLS (đắt vì rerank); get_article và
                # find_healthcare_facility gần như tức thì, chỉ tính vào AGENT_MAX_TOTAL_CALLS.
                is_search = call.name == "search_medical_docs"
                # Tham số không phải nội dung (nhóm bệnh, chuyên khoa…) luôn ghi; query thì theo mask.
                tool_span = trace.child(
                    f"tool.{call.name}", as_type="tool", input=args, metadata=_safe_args(args)
                )
                if calls_made >= settings.agent_max_total_calls:
                    output = TOOL_BUDGET_EXHAUSTED
                    tool_span.update(level="WARNING", status_message="Hết lượt gọi tool (AGENT_MAX_TOTAL_CALLS)")
                elif is_search and searches >= settings.agent_max_tool_calls:
                    output = SEARCH_BUDGET_EXHAUSTED
                    calls_made += 1
                    tool_span.update(level="WARNING", status_message="Hết lượt tìm (AGENT_MAX_TOOL_CALLS)")
                else:
                    if is_search:
                        yield "status", {"stage": "retrieving", "query": args.get("query", "")}
                        searches += 1
                    elif call.name == FACILITY_TOOL:
                        yield "status", {"stage": "finding_facility"}
                    else:
                        yield "status", {"stage": "reading", "document_id": args.get("document_id", "")}
                    output = session.run(call.name, args)
                    calls_made += 1
                    entry = session.trace[-1]
                    tool_span.update(
                        metadata={key: entry[key] for key in _TOOL_STATS if key in entry},
                        level="WARNING" if entry.get("error") else None,
                        status_message=entry.get("error"),
                    )
                tool_span.update(output=output)
                tool_span.end()
                responses.append({"function_response": {"name": call.name, "response": {"result": output}}})
            contents.append({"role": "user", "parts": responses})
            yield "sources", {"sources": [source.model_dump() for source in collect_sources(session.results)]}
            if session.facilities:
                yield "facilities", {"facilities": session.facilities}

        if not answered:
            # Chốt chặn (chưa có chunk nào) hoặc model không sinh text: trả lời như pipeline cũ, bằng
            # prompt ghép sẵn — có chunk thì build_rag_prompt, không có thì build_no_context_prompt.
            info.setdefault("fallback", "empty_answer")
            yield "status", {"stage": "generating"}
            if session.facilities and not session.results:
                # Chỉ có danh sách nơi khám: prompt "không có tài liệu" sẽ nói "chưa có thông tin",
                # lệch với các thẻ đang hiện. Dùng câu dẫn cố định.
                yield "delta", {"text": FACILITY_FALLBACK_TEXT}
                return
            prompt = build_prompt(message, session.results, profile)
            usage = {}
            texts = []
            fallback = trace.child(
                "gemini.fallback", as_type="generation", model=settings.gemini_model, input=prompt,
                metadata={"reason": info["fallback"], "has_chunks": bool(session.results)},
            )
            try:
                for text in self._gemini.ask_stream(prompt, history, usage=usage):
                    texts.append(text)
                    yield "delta", {"text": text}
            finally:
                fallback.update(output="".join(texts), usage=usage)
                fallback.end()


# Số liệu của mỗi lần gọi tool (từ ToolSession.trace) đưa lên trace — không có nội dung.
_TOOL_STATS = ("n_chunks", "seconds", "error", "n_facilities", "allergy_hits", "partial_sections")
# Tham số tool không phải nội dung câu hỏi, ghi kể cả khi che nội dung.
_SAFE_ARGS = ("disease_group", "specialty", "emergency", "for_child", "document_id", "section")
_TRACE_TEXT_LIMIT = 2000


def _safe_args(args: dict) -> dict:
    return {key: args[key] for key in _SAFE_ARGS if key in args}


def _call_for_trace(call: Any) -> dict:
    return {"name": call.name, "args": dict(call.args)}


def _contents_for_trace(contents: list) -> list[dict]:
    """`contents` gửi cho Gemini → dạng JSON gọn để xem trên Langfuse. Kết quả tool dài (nhiều đoạn
    tài liệu) cắt ở _TRACE_TEXT_LIMIT ký tự."""
    out = []
    for content in contents:
        parts = []
        for part in content["parts"]:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and "function_response" in part:
                response = part["function_response"]
                parts.append({"tool_result": response["name"], "result": str(response["response"]["result"])[:_TRACE_TEXT_LIMIT]})
            elif getattr(getattr(part, "function_call", None), "name", ""):
                parts.append({"tool_call": _call_for_trace(part.function_call)})
            elif getattr(part, "text", ""):
                parts.append(part.text)
        out.append({"role": content["role"], "parts": parts})
    return out


_service: AgentService | None = None


def get_agent_service() -> AgentService:
    global _service
    if _service is None:
        _service = AgentService(GeminiService(), Retriever())
    return _service
