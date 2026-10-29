"""Các bảng PostgreSQL: users, conversations, messages.

Quan hệ:  User 1 ── n Conversation 1 ── n Message
- Mỗi class dưới đây = một bảng. Mỗi thuộc tính `Mapped[...] = mapped_column(...)` = một cột.
- Sửa file này xong phải tạo migration mới để DB đổi theo:
    python -m alembic revision --autogenerate -m "mô tả thay đổi"
    python -m alembic upgrade head
"""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    """Tài khoản người dùng."""

    __tablename__ = "users"

    # UUID ngẫu nhiên (không phải 1, 2, 3...) để không đoán được id của người khác.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # unique=True: không cho hai tài khoản trùng email; index=True: tìm theo email khi đăng nhập nhanh.
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # Chỉ lưu bản băm argon2 (app/security.py), KHÔNG BAO GIỜ lưu mật khẩu gốc.
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100))
    # server_default=func.now(): Postgres tự điền thời điểm tạo, không cần gán trong code.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Không phải cột trong DB: cho phép viết `user.conversations` để lấy danh sách hội thoại.
    # cascade + passive_deletes: xoá user thì Postgres xoá luôn hội thoại (nhờ ondelete="CASCADE").
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class Conversation(Base):
    """Một cuộc trò chuyện (một mục trong sidebar)."""

    __tablename__ = "conversations"
    # Index (user_id, updated_at): tải sidebar "hội thoại của user X, mới nhất trước" nhanh.
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Khoá ngoại tới users.id; ondelete="CASCADE": xoá user → xoá hội thoại của user đó.
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Tự đặt từ câu hỏi đầu tiên (routers/chat.py → _derive_title), người dùng có thể đổi tên.
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Cập nhật mỗi khi có lượt chat mới (routers/chat.py → _save_turn) để sắp xếp sidebar.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="conversations")
    # `conversation.messages` trả tin nhắn theo đúng thứ tự gửi (id tăng dần).
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.id",
    )


class Message(Base):
    """Một tin nhắn trong hội thoại: câu hỏi của user hoặc câu trả lời của bot."""

    __tablename__ = "messages"
    # Index (conversation_id, id): tải tin nhắn của một hội thoại theo thứ tự nhanh.
    __table_args__ = (Index("ix_messages_conversation_id", "conversation_id", "id"),)

    # Số tự tăng (BIGSERIAL): sắp xếp theo id = đúng thứ tự gửi.
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # ondelete="CASCADE": xoá hội thoại → xoá hết tin nhắn của nó.
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(10))  # "user" | "model", giống frontend và Gemini
    content: Mapped[str] = mapped_column(Text)  # Text: không giới hạn độ dài
    # JSONB: lưu nguyên danh sách nguồn trích dẫn RAG [{title, url, citation}, ...] của câu trả lời.
    # None với tin nhắn của user.
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
