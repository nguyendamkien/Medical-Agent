"""Bảo mật đăng nhập: băm mật khẩu (argon2), tạo / kiểm tra JWT, và dependency get_current_user.

Luồng:
1. Đăng ký  → hash_password() → lưu password_hash vào bảng users.
2. Đăng nhập → verify_password() đúng → create_access_token() → trả token cho frontend.
3. Mỗi request sau đó gửi header `Authorization: Bearer <token>`
   → get_current_user() kiểm tra token → trả về object User (hoặc lỗi 401).
"""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User

# HS256: ký token bằng một khoá bí mật dùng chung (JWT_SECRET) — đủ cho một backend duy nhất.
ALGORITHM = "HS256"
# argon2: thuật toán băm mật khẩu được khuyến nghị hiện nay, cố tình chậm để khó dò mật khẩu.
_password_hash = PasswordHash.recommended()
# Đọc header "Authorization: Bearer <token>".
# auto_error=False: tự trả 401 thay vì 403 mặc định khi thiếu header.
# HTTPBearer làm trang /docs có nút "Authorize" để dán token.
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    """Mật khẩu gốc → chuỗi băm dạng "$argon2id$...". Mỗi lần băm ra chuỗi khác nhau (có salt)."""
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """So mật khẩu người dùng nhập với chuỗi băm trong DB."""
    return _password_hash.verify(password, password_hash)


def _secret() -> str:
    """Khoá ký JWT lấy từ .env. Thiếu thì báo lỗi rõ ràng thay vì ký bằng chuỗi rỗng (không an toàn)."""
    secret = get_settings().jwt_secret
    if not secret:
        raise RuntimeError("Chưa đặt JWT_SECRET trong backend/.env")
    return secret


def create_access_token(user_id: uuid.UUID) -> str:
    """Tạo token chứa id user (`sub`) và hạn dùng (`exp`, sau JWT_EXPIRE_DAYS ngày).

    Token chỉ được KÝ, không được mã hoá: ai có token đều đọc được nội dung,
    nên chỉ để id user, không để thông tin nhạy cảm.
    """
    expire = datetime.now(timezone.utc) + timedelta(days=get_settings().jwt_expire_days)
    # "sub" phải là chuỗi (PyJWT kiểm tra điều này).
    return jwt.encode({"sub": str(user_id), "exp": expire}, _secret(), algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Dùng trong router: `user: User = Depends(get_current_user)`. Token sai / hết hạn → 401.

    Gắn dependency này vào API nào thì API đó bắt buộc đăng nhập.
    """
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn, vui lòng đăng nhập lại.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:  # không gửi header Authorization
        raise unauthorized
    try:
        # decode() kiểm tra chữ ký (token có bị sửa không) và `exp` (đã hết hạn chưa).
        payload = jwt.decode(credentials.credentials, _secret(), algorithms=[ALGORITHM])
        user_id = uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        # InvalidTokenError: sai chữ ký / hết hạn / sai định dạng; KeyError: thiếu "sub";
        # ValueError: "sub" không phải UUID.
        raise unauthorized from None

    user = db.get(User, user_id)
    if user is None:  # token hợp lệ nhưng user đã bị xoá
        raise unauthorized
    return user
