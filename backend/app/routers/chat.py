import json
import logging
from collections.abc import Iterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.config import get_settings
from app.schemas import ChatRequest, ChatResponse
from app.services.agent_service import AgentService, get_agent_service
from app.services.rag_service import RagService, get_rag_service

router = APIRouter(prefix="/api", tags=["chat"])

logger = logging.getLogger(__name__)


def _get_service() -> RagService | AgentService:
    """AGENT_ENABLED chọn agent hoặc pipeline cố định; hai bên có cùng ask / ask_stream."""
    try:
        if get_settings().agent_enabled:
            return get_agent_service()
        return get_rag_service()
    except RuntimeError as exc:  # thiếu API key, chưa cài thư viện...
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    service = _get_service()
    try:
        reply, sources = service.ask(request.message, request.history)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # lỗi gọi Gemini API (mạng, quota, key sai...)
        raise HTTPException(
            status_code=502, detail=f"Không thể lấy phản hồi từ Gemini: {exc}"
        ) from exc

    return ChatResponse(reply=reply, sources=sources)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    """Như POST /api/chat nhưng trả về Server-Sent Events để hiển thị câu trả lời dần dần.

    Sự kiện: status → sources → delta (nhiều lần) → done. Lỗi sau khi stream đã bắt đầu không
    đổi được HTTP status nữa nên được gửi thành sự kiện `error` { detail }.
    """
    # Lỗi cấu hình báo ngay bằng HTTP 500, trước khi mở stream.
    service = _get_service()

    def events() -> Iterator[str]:
        try:
            for event, data in service.ask_stream(request.message, request.history):
                yield _sse(event, data)
        except Exception as exc:
            logger.exception("Lỗi khi stream câu trả lời")
            yield _sse("error", {"detail": f"Không thể lấy phản hồi từ Gemini: {exc}"})
            return
        yield _sse("done", {"disclaimer": ChatResponse.model_fields["disclaimer"].default})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # Tắt cache / buffer của proxy (nginx) để từng sự kiện tới trình duyệt ngay.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
