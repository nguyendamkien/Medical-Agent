"""API chat: bắt buộc đăng nhập, lịch sử hội thoại đọc / ghi trong PostgreSQL.

    POST /api/chat          trả câu trả lời một lần
    POST /api/chat/stream   trả từng phần bằng Server-Sent Events (frontend dùng endpoint này)

Một lượt chat:
1. get_current_user kiểm tra token (sai → 401).
2. _prepare_conversation: chưa có conversation_id → tạo hội thoại mới;
   có → kiểm tra hội thoại thuộc user (không → 404) và đọc lịch sử từ DB.
3. Gọi RAG / agent như trước (service.ask / ask_stream) với lịch sử vừa đọc.
4. _save_turn: trả lời xong mới lưu cả câu hỏi + câu trả lời (lỗi giữa chừng → không lưu gì).
"""

import json
import logging
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models import Conversation, Message, User
from app.routers.conversations import get_owned_conversation
from app.schemas import ChatMessage, ChatRequest, ChatResponse
from app.security import get_current_user
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


def _derive_title(text: str) -> str:
    """Giống deriveTitle ở frontend: 32 ký tự đầu của câu hỏi đầu tiên."""
    text = " ".join(text.split())
    return f"{text[:32]}…" if len(text) > 32 else text


def _prepare_conversation(
    request: ChatRequest, user: User, db: Session
) -> tuple[uuid.UUID, str, list[ChatMessage]]:
    """Lấy (hoặc tạo) hội thoại và đọc lịch sử từ DB. Trả (id, tiêu đề, history)."""
    if request.conversation_id is None:
        # Câu hỏi đầu tiên của cuộc mới: tạo hội thoại ngay để gửi id cho frontend
        # (sự kiện `conversation` ở đầu stream). Lịch sử rỗng.
        conversation = Conversation(user_id=user.id, title=_derive_title(request.message))
        db.add(conversation)
        db.commit()
        return conversation.id, conversation.title, []

    conversation = get_owned_conversation(db, request.conversation_id, user)
    # Chỉ đọc HISTORY_MAX_TURNS lượt gần nhất (mỗi lượt = 1 câu hỏi + 1 câu trả lời):
    # lấy 2*N tin mới nhất (id giảm dần) rồi đảo lại cho đúng thứ tự thời gian.
    max_turns = get_settings().history_max_turns
    rows = db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.id.desc())
        .limit(2 * max_turns)
    ).all()
    history = [ChatMessage(role=m.role, content=m.content) for m in reversed(rows)]
    return conversation.id, conversation.title, history


def _save_turn(conversation_id: uuid.UUID, question: str, reply: str, sources: list[dict]) -> None:
    """Lưu cả câu hỏi và câu trả lời trong MỘT transaction, chỉ khi đã trả lời xong.

    Dùng session riêng (SessionLocal) thay vì session của request, vì với StreamingResponse,
    FastAPI 0.115 đóng session của Depends(get_db) TRƯỚC khi stream chạy xong.
    """
    with SessionLocal() as db:
        db.add(Message(conversation_id=conversation_id, role="user", content=question))
        db.add(Message(conversation_id=conversation_id, role="model", content=reply, sources=sources))
        db.execute(
            update(Conversation).where(Conversation.id == conversation_id).values(updated_at=func.now())
        )
        db.commit()


@router.post("/chat", response_model=ChatResponse)
def chat(
    request: ChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> ChatResponse:
    service = _get_service()
    conversation_id, _, history = _prepare_conversation(request, user, db)
    try:
        reply, sources = service.ask(request.message, history)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Không thể lấy phản hồi từ Gemini: {exc}") from exc

    _save_turn(conversation_id, request.message, reply, [s.model_dump() for s in sources])
    return ChatResponse(reply=reply, sources=sources)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat/stream")
def chat_stream(
    request: ChatRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> StreamingResponse:
    """Sự kiện: conversation → status → sources → delta (nhiều lần) → done.

    `conversation` { id, title } luôn đến đầu tiên, để frontend biết id của hội thoại vừa tạo.
    """
    service = _get_service()
    # Chạy TRƯỚC khi mở stream: token sai / hội thoại không thuộc user → 401 / 404 bình thường.
    conversation_id, title, history = _prepare_conversation(request, user, db)

    def events() -> Iterator[str]:
        yield _sse("conversation", {"id": str(conversation_id), "title": title})
        reply_parts: list[str] = []
        sources: list[dict] = []
        try:
            for event, data in service.ask_stream(request.message, history):
                if event == "delta":
                    reply_parts.append(data["text"])  # chữ đã qua guardrail liều thuốc
                elif event == "sources":
                    sources = data["sources"]  # sự kiện sau chứa danh sách đầy đủ hơn
                yield _sse(event, data)
        except Exception as exc:
            logger.exception("Lỗi khi stream câu trả lời")
            yield _sse("error", {"detail": f"Không thể lấy phản hồi từ Gemini: {exc}"})
            return  # không lưu câu trả lời dở

        try:
            _save_turn(conversation_id, request.message, "".join(reply_parts), sources)
        except Exception:
            logger.exception("Không lưu được lượt chat vào DB")
            yield _sse("error", {"detail": "Đã trả lời nhưng không lưu được vào lịch sử."})
            return
        yield _sse("done", {"disclaimer": ChatResponse.model_fields["disclaimer"].default})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
