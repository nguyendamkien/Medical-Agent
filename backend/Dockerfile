# Backend ChatBot Y Tế — xem docs/DOCKER.md
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Model rerank (~571 MB) tải về đây lần đầu; docker-compose gắn volume để không phải tải lại
    HF_HOME=/hf-cache

WORKDIR /app

# Cài thư viện trước khi copy code: sửa code không phải cài lại
COPY requirements.txt .
RUN pip install -r requirements.txt

# Chạy bằng user thường. Tạo sẵn thư mục cho volume để volume mới nhận đúng quyền.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /hf-cache /app/app/storage \
    && chown -R app:app /hf-cache /app

COPY --chown=app:app . .

USER app
EXPOSE 8000

# Cập nhật bảng DB rồi chạy API. 1 worker: mỗi worker nạp riêng model rerank vào RAM.
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn main:app --host 0.0.0.0 --port 8000"]
