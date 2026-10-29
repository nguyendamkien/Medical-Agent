"""API quản lý hội thoại của user đang đăng nhập (dùng cho sidebar ở frontend).

    GET    /api/conversations                 danh sách hội thoại, mới nhất trước
    GET    /api/conversations/{id}/messages   toàn bộ tin nhắn của một hội thoại
    PATCH  /api/conversations/{id}            đổi tên
    DELETE /api/conversations/{id}            xoá (kèm toàn bộ tin nhắn)

Không có API "tạo hội thoại": hội thoại được tạo khi gửi câu hỏi đầu tiên (routers/chat.py),
nên bấm "Cuộc trò chuyện mới" mà không chat thì DB không có bản ghi rỗng.

Mọi API đều cần đăng nhập (Depends(get_current_user)) và chỉ đụng tới hội thoại của chính user đó.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Conversation, Message, User
from app.schemas import ConversationOut, ConversationUpdate, MessageOut
from app.security import get_current_user

router = APIRouter(prefix="/api/conversations", tags=["conversations"])


def get_owned_conversation(db: Session, conversation_id: uuid.UUID, user: User) -> Conversation:
    """Lấy hội thoại của chính user này. Hội thoại của người khác cũng trả 404 (không phải 403),
    để không lộ là id đó có tồn tại.

    Dùng chung cho mọi chỗ cần đọc / sửa một hội thoại (kể cả routers/chat.py) — đây là chốt chặn
    để user A không đọc được lịch sử của user B.
    """
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Không tìm thấy cuộc trò chuyện.")
    return conversation


@router.get("", response_model=list[ConversationOut])
def list_conversations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Danh sách hội thoại của user, hội thoại vừa chat gần nhất lên đầu."""
    return db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)  # chỉ lấy của user đang đăng nhập
        .order_by(Conversation.updated_at.desc())
    ).all()


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
def list_messages(
    conversation_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Toàn bộ tin nhắn của một hội thoại theo thứ tự gửi (kèm `sources` của câu trả lời)."""
    conversation = get_owned_conversation(db, conversation_id, user)
    return db.scalars(
        select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id)
    ).all()


@router.patch("/{conversation_id}", response_model=ConversationOut)
def rename_conversation(
    conversation_id: uuid.UUID,
    body: ConversationUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Đổi tiêu đề hội thoại."""
    conversation = get_owned_conversation(db, conversation_id, user)
    conversation.title = body.title.strip()
    db.commit()
    db.refresh(conversation)  # đọc lại từ DB để trả về giá trị mới nhất
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    """Xoá hội thoại; tin nhắn bị xoá theo. 204 = thành công, không có nội dung trả về."""
    conversation = get_owned_conversation(db, conversation_id, user)
    db.delete(conversation)  # tin nhắn bị xoá theo (cascade)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
