import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class ChatMessage(BaseModel):
    role: Literal["user", "model"]
    content: str


class ChatRequest(BaseModel):
    """Body của POST /api/chat và /api/chat/stream.

    Không còn `history`: backend tự đọc lịch sử từ DB theo `conversation_id`.
    """

    message: str = Field(..., min_length=1, max_length=4000, description="Câu hỏi y tế của người dùng")
    conversation_id: uuid.UUID | None = Field(
        default=None, description="Để trống ở câu hỏi đầu tiên → backend tạo hội thoại mới"
    )


class Source(BaseModel):
    title: str
    url: str
    citation: str | None = None


class ChatResponse(BaseModel):
    reply: str
    sources: list[Source] = Field(default_factory=list)
    disclaimer: str = (
        "Thông tin trên chỉ mang tính tham khảo, không thay thế cho chẩn đoán "
        "và tư vấn của bác sĩ hoặc chuyên gia y tế."
    )


# ---------- Đăng nhập (routers/auth.py) ----------
# *Request: dữ liệu frontend gửi lên (Pydantic tự kiểm tra, sai → lỗi 422).
# *Out / *Response: dữ liệu backend trả về — không bao giờ có password_hash.


class RegisterRequest(BaseModel):
    """Body của POST /api/auth/register."""

    email: EmailStr  # EmailStr (cần thư viện email-validator) từ chối email sai định dạng
    password: str = Field(..., min_length=8, max_length=128)
    display_name: str = Field(..., min_length=1, max_length=100)


class LoginRequest(BaseModel):
    """Body của POST /api/auth/login."""

    email: EmailStr
    password: str


class UserOut(BaseModel):
    """Thông tin user trả về cho frontend (register, login, /me)."""

    model_config = ConfigDict(from_attributes=True)  # cho phép trả thẳng object SQLAlchemy

    id: uuid.UUID
    email: str
    display_name: str
    created_at: datetime


class TokenResponse(BaseModel):
    """Kết quả đăng nhập: token để gửi kèm các request sau + thông tin user."""

    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ---------- Hội thoại (routers/conversations.py) ----------


class ConversationOut(BaseModel):
    """Một mục trong sidebar."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationUpdate(BaseModel):
    """Body của PATCH /api/conversations/{id} (đổi tên)."""

    title: str = Field(..., min_length=1, max_length=200)


class MessageOut(BaseModel):
    """Một tin nhắn khi tải lại lịch sử. `sources` chỉ có ở tin của bot (role="model")."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Literal["user", "model"]
    content: str
    sources: list[Source] | None = None
    created_at: datetime


# ---------- Hồ sơ sức khoẻ (routers/profile.py) ----------

MAX_PROFILE_ITEMS = 20
MAX_PROFILE_ITEM_CHARS = 100


def _clean_items(values: list[str]) -> list[str]:
    """Bỏ khoảng trắng thừa, mục rỗng, mục trùng (không phân biệt hoa thường); giữ thứ tự."""
    seen: set[str] = set()
    items = []
    for value in values:
        item = " ".join(str(value).split())
        if item and item.casefold() not in seen:
            seen.add(item.casefold())
            items.append(item)
    return items


class HealthProfileIn(BaseModel):
    """Body của PUT /api/profile. Gửi đủ mọi trường (ghi đè cả hồ sơ)."""

    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    pregnant: bool = False
    conditions: list[str] = Field(default_factory=list, max_length=MAX_PROFILE_ITEMS)
    allergies: list[str] = Field(default_factory=list, max_length=MAX_PROFILE_ITEMS)
    use_in_chat: bool = True

    @field_validator("conditions", "allergies")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        items = _clean_items(values)
        if any(len(item) > MAX_PROFILE_ITEM_CHARS for item in items):
            raise ValueError(f"Mỗi mục tối đa {MAX_PROFILE_ITEM_CHARS} ký tự.")
        return items

    @field_validator("birth_year")
    @classmethod
    def _not_future(cls, value: int | None) -> int | None:
        if value is not None and value > date.today().year:
            raise ValueError("Năm sinh không được ở tương lai.")
        return value


class HealthProfileOut(HealthProfileIn):
    """Hồ sơ trả về. `updated_at` = None: người dùng chưa lưu hồ sơ lần nào."""

    model_config = ConfigDict(from_attributes=True)

    updated_at: datetime | None = None


class ProfileOptions(BaseModel):
    """Gợi ý cho form hồ sơ: các nhóm bệnh có trong dữ liệu của chatbot."""

    conditions: list[str]
