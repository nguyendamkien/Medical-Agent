# Chạy demo bằng Docker

Chạy toàn bộ app (PostgreSQL, backend, frontend) bằng Docker trên một máy, để demo. Bản này **không dành cho deploy lên server**: mật khẩu DB để mặc định, cổng DB và Adminer mở ra ngoài.

Muốn dev (sửa code, tự load lại) thì vẫn chạy backend và frontend trực tiếp như trong [README](../README.md#cài-đặt--chạy).

## Kiến trúc

```
 Trình duyệt ──► http://localhost:3000  (container frontend: nginx)
                   ├── /        → file React đã build sẵn
                   └── /api/... → http://backend:8000  (container backend: FastAPI)
                                    ├── PostgreSQL (container db)
                                    ├── Chroma index  ← thư mục backend/app/storage trên máy
                                    └── model rerank  ← volume hf-cache
```

- Frontend và API chung một địa chỉ (`localhost:3000`) nên không cần CORS. nginx tắt buffering cho `/api` để SSE gửi từng đoạn câu trả lời ngay.
- Backend chạy 1 worker. Khi khởi động, container tự chạy `alembic upgrade head`.
- API key và `JWT_SECRET` đọc từ `backend/.env` lúc chạy, không nằm trong image.

| File | Vai trò |
|---|---|
| [backend/Dockerfile](../backend/Dockerfile) | `python:3.11-slim`, cài `requirements.txt`, chạy bằng user thường |
| [frontend/Dockerfile](../frontend/Dockerfile) | Build Vite bằng Node 22, rồi chạy nginx phục vụ `dist/` |
| [frontend/nginx.conf](../frontend/nginx.conf) | Chuyển tiếp `/api` sang backend, tắt buffering cho SSE, fallback SPA |
| [docker-compose.yml](../docker-compose.yml) | Service `backend`, `frontend` nằm trong profile `app`; `db`, `adminer` chạy mặc định |

Vì backend và frontend nằm trong profile `app`, lệnh `docker compose up -d` vẫn chỉ chạy DB và Adminer như trước. Cách dev cũ không đổi gì.

## Chuẩn bị (một lần)

1. Docker Desktop đang chạy. Nên để Docker được dùng ít nhất 4 GB RAM, vì model rerank chiếm khoảng 1–2 GB.
2. `backend/.env` đã điền `GEMINI_API_KEY`, `OPENAI_API_KEY`, `JWT_SECRET` (xem README, bước 2). Các biến `DATABASE_URL`, `VECTOR_DB_PATH`, `DATA_PATH`, `CORS_ORIGINS` sẽ được docker-compose ghi đè cho đúng với container, không cần sửa `.env`.
3. **Chroma index**: container dùng chung thư mục `backend/app/storage` với lúc chạy trên Windows.
   - Đã từng chạy `python -m app.scripts.ingest` trên máy thì không cần làm gì.
   - Chưa có index thì crawl dữ liệu (README, bước 1) rồi ingest bằng container:
     ```bash
     docker compose --profile app run --rm backend python -m app.scripts.ingest
     ```
4. **Model rerank** (~571 MB): nếu không làm gì, câu hỏi đầu tiên sẽ tự tải model vào volume `hf-cache`. Nếu mạng chậm thì có thể mất nhiều phút, và trong lúc đó câu hỏi đứng ở bước "đang tìm tài liệu". Máy đã từng chạy backend trên Windows thì model đã có trong cache Hugging Face, chỉ cần chép vào volume (khoảng 20 giây):
   ```powershell
   # PowerShell, ở thư mục gốc repo
   docker compose --profile app create backend
   docker run --rm -v "$env:USERPROFILE\.cache\huggingface\hub:/src:ro" -v medical-agent_hf-cache:/hf-cache alpine sh -c "mkdir -p /hf-cache/hub && cp -r /src/models--onnx-community--bge-reranker-v2-m3-ONNX /hf-cache/hub/ && chown -R 1000:1000 /hf-cache"
   ```
   Lệnh `create` chỉ để tạo volume trước. Tên volume là `<tên thư mục repo>_hf-cache`; nếu repo nằm trong thư mục có tên khác thì xem tên đúng bằng `docker volume ls`.

## Chạy

```bash
docker compose --profile app up -d --build
```

- App: http://localhost:3000. Đăng ký tài khoản rồi chat.
- Swagger: http://localhost:8000/docs
- Adminer: http://localhost:8080 (như README)

Lần build đầu mất khoảng 5–10 phút, chủ yếu do cài thư viện Python; image backend khoảng 1 GB. Những lần sau, nếu `requirements.txt` không đổi thì chỉ build lại phần code, nhanh hơn nhiều.

## Lệnh thường dùng

| Việc | Lệnh |
|---|---|
| Xem log backend | `docker compose logs -f backend` |
| Dừng app (giữ dữ liệu) | `docker compose --profile app down` |
| Sửa code xong, chạy lại | `docker compose --profile app up -d --build` |
| Ingest lại dữ liệu | `docker compose --profile app run --rm backend python -m app.scripts.ingest` rồi `docker compose restart backend` |
| Vào shell trong backend | `docker compose exec backend sh` |
| Xoá cả DB và model đã tải | `docker compose --profile app down -v` (mất tài khoản, lịch sử chat) |

## Lưu ý

- **Không chạy backend trên Windows và backend trong Docker cùng lúc.** Hai bên dùng chung Chroma index và cổng 8000.
- Đã từng chạy frontend bằng `npm run dev` vẫn dùng được với backend trong Docker, vì `CORS_ORIGINS` của container có cho phép `localhost:5173`.
- `VITE_API_BASE_URL` được gắn cứng lúc build. Bản Docker để rỗng, nghĩa là gọi API cùng địa chỉ với trang. Muốn trỏ sang backend khác thì build lại với `--build-arg VITE_API_BASE_URL=...`.
- `requirements.txt` không ghim phiên bản các thư viện kéo theo (onnxruntime, huggingface_hub, tokenizers…). Vì vậy image có thể cài bản mới hơn venv trên máy. Nếu backend trong Docker chạy khác trên máy, so phiên bản bằng `pip freeze` ở cả hai bên.

## Xử lý lỗi

| Hiện tượng | Nguyên nhân / cách sửa |
|---|---|
| Câu hỏi đầu tiên đứng rất lâu ở "đang tìm tài liệu" | Đang tải model rerank. Xem dung lượng đã tải: `docker compose exec backend du -sh /hf-cache`. Nhanh hơn thì chép model từ máy (bước 4 ở trên). Docker-compose đã đặt `HF_HUB_DISABLE_XET=1`, vì giao thức Xet có thể treo trong container |
| Trả lời "Không có tài liệu phù hợp" với mọi câu | Chưa có Chroma index: chạy ingest (bước 3) rồi `docker compose restart backend` |
| `medical-backend` báo `unhealthy` hoặc khởi động lại liên tục | Xem `docker compose logs backend`. Thường do thiếu `backend/.env`, hoặc migration lỗi |
| Lỗi cổng `8000` / `3000` đã được dùng | Tắt backend đang chạy trên Windows, hoặc đổi cổng bên trái trong `ports` ở `docker-compose.yml` |
| Câu trả lời hiện ra một lần ở cuối, không stream | Có proxy khác đứng trước nginx đang buffer. Mặc định `nginx.conf` đã tắt buffering cho `/api` |
