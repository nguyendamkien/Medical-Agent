"""Kết nối PostgreSQL (user + lịch sử chat) bằng SQLAlchemy 2.0.

- engine: giữ pool kết nối tới Postgres, tạo một lần cho cả app.
- SessionLocal: mỗi request mở một Session từ đây.
- get_db(): dependency cho FastAPI — mở session khi request tới, đóng khi xong.
- Base: lớp cha của các model (bảng) sẽ thêm ở bước sau.
"""

from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

engine = create_engine(
    get_settings().database_url,
    pool_pre_ping=True,  # kiểm tra kết nối trước khi dùng, tránh lỗi khi Postgres vừa khởi động lại
    pool_size=5,
    max_overflow=10,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """Dùng trong router: `db: Session = Depends(get_db)`."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_connection() -> None:
    """Chạy SELECT 1; ném lỗi nếu không kết nối được (Docker chưa bật, sai mật khẩu...)."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
