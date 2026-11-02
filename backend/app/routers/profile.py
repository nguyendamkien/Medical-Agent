"""API hồ sơ sức khoẻ của user đang đăng nhập (docs/HEALTH_PROFILE.md).

    GET    /api/profile           hồ sơ hiện tại (chưa có → hồ sơ rỗng, updated_at = null)
    PUT    /api/profile           lưu cả hồ sơ (tạo mới hoặc ghi đè)
    DELETE /api/profile           xoá hồ sơ
    GET    /api/profile/options   gợi ý bệnh nền: các nhóm bệnh có trong dữ liệu của chatbot

Mọi API chỉ đụng tới hồ sơ của chính user (khoá chính = user.id), không nhận id từ client.
Hồ sơ là dữ liệu sức khoẻ: không ghi nội dung hồ sơ vào log.

load_chat_profile() dùng ở routers/chat.py: đọc hồ sơ để đưa vào prompt.
"""

import logging
from functools import lru_cache

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import HealthProfile, User
from app.rag.loader import load_documents
from app.rag.profile import UserProfile
from app.schemas import HealthProfileIn, HealthProfileOut, ProfileOptions
from app.security import get_current_user

router = APIRouter(prefix="/api/profile", tags=["profile"])

logger = logging.getLogger(__name__)


@router.get("", response_model=HealthProfileOut)
def get_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.get(HealthProfile, user.id)
    return profile if profile is not None else HealthProfileOut()


@router.put("", response_model=HealthProfileOut)
def save_profile(
    body: HealthProfileIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    profile = db.get(HealthProfile, user.id)
    if profile is None:
        profile = HealthProfile(user_id=user.id)
        db.add(profile)
    for field, value in body.model_dump().items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)  # lấy updated_at do Postgres điền
    return profile


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    profile = db.get(HealthProfile, user.id)
    if profile is not None:
        db.delete(profile)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/options", response_model=ProfileOptions)
def profile_options(_: User = Depends(get_current_user)) -> ProfileOptions:
    return ProfileOptions(conditions=_disease_groups())


@lru_cache
def _disease_groups() -> list[str]:
    """Nhóm bệnh trong file dữ liệu (DATA_PATH, ~100 bài, đọc một lần). Không đọc từ Retriever vì
    tạo Retriever phải nạp Chroma + BM25 + reranker — quá nặng cho một form."""
    try:
        groups = {doc.get("disease_group") for doc in load_documents(get_settings().data_path)}
    except OSError:
        logger.warning("Không đọc được DATA_PATH, form hồ sơ không có gợi ý bệnh nền")
        return []
    return sorted(group for group in groups if group)


def load_chat_profile(db: Session, user: User) -> UserProfile | None:
    """Hồ sơ đưa vào prompt. None khi chưa có hồ sơ, hồ sơ rỗng, hoặc người dùng tắt use_in_chat."""
    row = db.get(HealthProfile, user.id)
    if row is None or not row.use_in_chat:
        return None
    profile = UserProfile(
        birth_year=row.birth_year,
        pregnant=row.pregnant,
        conditions=list(row.conditions or []),
        allergies=list(row.allergies or []),
    )
    return None if profile.is_empty else profile
