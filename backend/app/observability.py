"""Trace lên Langfuse Cloud (docs/OBSERVABILITY.md). Lớp bọc mỏng quanh SDK, để code agent không phụ
thuộc trực tiếp vào Langfuse:

- Tắt (LANGFUSE_ENABLED=false / thiếu key / chưa cài SDK) → mọi hàm thành no-op (`NOOP`), app và eval
  chạy như cũ.
- Mọi lời gọi SDK nằm trong try: lỗi của Langfuse (mạng, key sai…) chỉ ghi log, không làm hỏng câu trả lời.
- LANGFUSE_MASK_CONTENT=true (mặc định): input / output thay bằng MASKED. Ai gọi `child()` / `update()`
  chỉ được đưa vào `metadata` những thứ không phải nội dung (tên tool, số chunk, thời gian…).

Vì sao tạo observation bằng tay (start_observation) thay cho decorator / context manager của SDK:
câu trả lời được stream qua generator, Starlette chạy từng bước của generator trong threadpool, nên
"context hiện tại" của OpenTelemetry không theo kịp. Ở đây mỗi Observation giữ chính nó và tạo con trực
tiếp từ nó; thuộc tính cấp trace (user_id, session_id, tags) được gắn lại mỗi lần tạo con bằng
propagate_attributes, vì SDK chỉ tự gắn cho các bước tạo BÊN TRONG khối đó.
"""

import logging
from contextlib import nullcontext
from functools import lru_cache
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

MASKED = "[ẩn — LANGFUSE_MASK_CONTENT=true]"


class Observation:
    """Một bước trong trace (trace gốc, lời gọi Gemini, lời gọi tool…). `_obs` None = tắt."""

    def __init__(self, obs: Any = None, attrs: dict | None = None, mask: bool = True) -> None:
        self._obs = obs
        self._attrs = attrs or {}
        self.masked = mask

    @property
    def enabled(self) -> bool:
        return self._obs is not None

    @property
    def trace_id(self) -> str | None:
        return getattr(self._obs, "trace_id", None)

    def content(self, value: Any) -> Any:
        """Nội dung (câu hỏi, tài liệu, câu trả lời): che khi mask."""
        return MASKED if self.masked and value is not None else value

    def child(
        self,
        name: str,
        as_type: str = "span",
        *,
        input: Any = None,
        metadata: dict | None = None,
        model: str | None = None,
    ) -> "Observation":
        if self._obs is None:
            return NOOP
        try:
            with _propagate(self._attrs):
                obs = self._obs.start_observation(
                    name=name, as_type=as_type, input=self.content(input), metadata=metadata, model=model
                )
            return Observation(obs, self._attrs, self.masked)
        except Exception:
            logger.warning("Langfuse: không tạo được observation %s", name, exc_info=True)
            return NOOP

    def update(
        self,
        *,
        input: Any = None,
        output: Any = None,
        metadata: dict | None = None,
        usage: dict | None = None,
        level: str | None = None,
        status_message: str | None = None,
    ) -> None:
        if self._obs is None:
            return
        kwargs: dict[str, Any] = {}
        if input is not None:
            kwargs["input"] = self.content(input)
        if output is not None:
            kwargs["output"] = self.content(output)
        if metadata:
            kwargs["metadata"] = metadata
        if usage:
            kwargs["usage_details"] = usage
        if level:
            kwargs["level"] = level
        if status_message:
            kwargs["status_message"] = status_message
        try:
            self._obs.update(**kwargs)
        except Exception:
            logger.warning("Langfuse: không cập nhật được observation", exc_info=True)

    def end(self) -> None:
        if self._obs is None:
            return
        try:
            self._obs.end()
        except Exception:
            logger.warning("Langfuse: không đóng được observation", exc_info=True)

    def score_trace(self, name: str, value: float, comment: str | None = None) -> None:
        """Gắn điểm (vd chỉ số eval) vào cả trace."""
        if self._obs is None or value is None:
            return
        try:
            self._obs.score_trace(name=name, value=float(value), comment=comment)
        except Exception:
            logger.warning("Langfuse: không ghi được điểm %s", name, exc_info=True)


NOOP = Observation()


@lru_cache
def _client() -> Any:
    """Client Langfuse dùng chung cho cả tiến trình; None khi tắt."""
    settings = get_settings()
    if not settings.langfuse_enabled:
        return None
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        logger.warning("LANGFUSE_ENABLED=true nhưng thiếu LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY → tắt trace")
        return None
    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning("Chưa cài thư viện langfuse (pip install -r requirements.txt) → tắt trace")
        return None
    return Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_base_url,
        environment=settings.langfuse_environment,
    )


def _propagate(attrs: dict) -> Any:
    if not attrs:
        return nullcontext()
    from langfuse import propagate_attributes

    return propagate_attributes(**attrs)


def start_trace(
    name: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    tags: list[str] | None = None,
    input: Any = None,
    metadata: dict | None = None,
    mask: bool | None = None,
) -> Observation:
    """Mở trace gốc. Nhớ gọi `.end()` (đặt trong finally). `mask=None` → theo LANGFUSE_MASK_CONTENT;
    eval truyền `mask=False` vì câu hỏi là câu mẫu."""
    client = _client()
    if client is None:
        return NOOP
    if mask is None:
        mask = get_settings().langfuse_mask_content
    attrs = {
        key: value
        for key, value in {"user_id": user_id, "session_id": session_id, "tags": tags, "trace_name": name}.items()
        if value
    }
    try:
        with _propagate(attrs):
            root = client.start_observation(
                name=name, as_type="agent", input=MASKED if mask and input is not None else input, metadata=metadata
            )
        return Observation(root, attrs, mask)
    except Exception:
        logger.warning("Langfuse: không mở được trace %s", name, exc_info=True)
        return NOOP


def flush() -> None:
    """Gửi ngay các trace đang chờ (dùng cuối script; backend tự gửi nền định kỳ)."""
    client = _client()
    if client is not None:
        try:
            client.flush()
        except Exception:
            logger.warning("Langfuse: flush thất bại", exc_info=True)
