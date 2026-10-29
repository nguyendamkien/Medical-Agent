# Đăng nhập + lưu lịch sử chat (PostgreSQL)

Người dùng phải đăng ký / đăng nhập mới chat được. Lịch sử hội thoại lưu theo tài khoản trong PostgreSQL, thay cho `localStorage` của trình duyệt.

## Đã làm

### Hạ tầng
- `docker-compose.yml` ở thư mục gốc chạy **PostgreSQL 16** (`localhost:5432`) và **Adminer** để xem dữ liệu (`http://localhost:8080`).
- Kết nối bằng SQLAlchemy 2 qua driver **`pg8000`** (thuần Python). Không dùng `psycopg` vì Windows Smart App Control chặn DLL của nó.
- Quản lý bảng bằng **Alembic**. Migration đầu tiên tạo 3 bảng.

### Database

| Bảng | Cột chính |
|---|---|
| `users` | `id` UUID, `email` (unique), `password_hash` (argon2), `display_name`, `created_at` |
| `conversations` | `id` UUID, `user_id` → users, `title`, `created_at`, `updated_at` |
| `messages` | `id` tự tăng, `conversation_id` → conversations, `role` (`user` / `model`), `content`, `sources` (JSONB), `created_at` |

Xóa user thì xóa luôn hội thoại; xóa hội thoại thì xóa luôn tin nhắn (`ON DELETE CASCADE`).

### Backend

| File | Nội dung |
|---|---|
| `app/db.py` | engine, `SessionLocal`, `Base`, `get_db()` |
| `app/models.py` | 3 bảng ở trên |
| `app/security.py` | băm mật khẩu argon2, tạo / kiểm tra JWT, `get_current_user` (sai / hết hạn → 401) |
| `app/routers/auth.py` | `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me` |
| `app/routers/conversations.py` | liệt kê, xem tin nhắn, đổi tên, xóa hội thoại. Hội thoại của người khác → 404 |
| `app/routers/chat.py` | bắt buộc đăng nhập; nhận `conversation_id` thay cho `history`; tự đọc lịch sử từ DB; trả lời xong mới lưu câu hỏi + câu trả lời |
| `app/schemas.py` | schema auth và hội thoại; `ChatRequest` dùng `conversation_id` |
| `alembic/`, `alembic.ini` | migration |

Chi tiết API: [README.md](../README.md#api). Chi tiết code: [backend/STRUCTURE.md](../backend/STRUCTURE.md).

**Quyết định thiết kế:**
- Không có API "tạo hội thoại": hội thoại được tạo khi gửi câu hỏi đầu tiên. Stream phát sự kiện `conversation` `{id, title}` đầu tiên để frontend biết id.
- Câu hỏi và câu trả lời được lưu **cùng lúc** khi stream xong. Lỗi giữa chừng thì không lưu gì, nên lịch sử không có câu hỏi thiếu câu trả lời.
- Việc lưu dùng `SessionLocal()` riêng, vì FastAPI 0.115 đóng session của `Depends(get_db)` trước khi `StreamingResponse` chạy xong.
- Bộ eval không bị ảnh hưởng, vì nó gọi thẳng service chứ không qua API.

### Frontend

| File | Nội dung |
|---|---|
| `api/chatApi.js` | lưu token, `apiFetch` gắn header `Authorization` (401 → xóa token, về màn đăng nhập), API auth và hội thoại |
| `components/AuthScreen.jsx` | màn đăng nhập / đăng ký (mới) |
| `App.jsx` | danh sách hội thoại và tin nhắn lấy từ API, gửi `conversation_id` |
| `components/Sidebar.jsx` | đổi tên, xóa hội thoại, tên user, đăng xuất |
| `utils/sessions.js` | lời chào; tự xóa lịch sử chat cũ còn trong `localStorage` |

Chi tiết: [frontend/STRUCTURE.md](../frontend/STRUCTURE.md).

### Đã kiểm tra
- Đăng ký 201, trùng email 409, sai định dạng 422. Đăng nhập sai 401, đúng 200. `/me` có token 200, không có hoặc sai token 401.
- Chat với Gemini thật: câu đầu tạo hội thoại, câu sau hiểu ngữ cảnh, DB lưu đủ tin nhắn kèm `sources`.
- User khác đọc hoặc chat vào hội thoại không phải của mình → 404. Đổi tên, xóa chạy đúng.
- Frontend: lint và build sạch. Đăng ký từ giao diện đã tạo được tài khoản.

## Cài đặt

Chạy các lệnh trong `backend/` với venv đã kích hoạt. Trên Windows bật Smart App Control, gọi `pip`, `uvicorn`, `alembic` qua `python -m`.

1. **Cài thư viện:** `python -m pip install -r requirements.txt`
2. **Cấu hình `backend/.env`** (mẫu trong `.env.example`):
   ```
   DATABASE_URL=postgresql+pg8000://medical:medical@localhost:5432/medical_agent
   JWT_SECRET=<chuỗi ngẫu nhiên>
   JWT_EXPIRE_DAYS=7
   ```
   Tạo `JWT_SECRET` bằng lệnh: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
3. **Bật database:** mở Docker Desktop, rồi chạy `docker compose up -d` ở thư mục gốc của repo.
4. **Tạo bảng:** `python -m alembic upgrade head`
5. **Chạy:**
   - backend: `python -m uvicorn main:app --reload`
   - frontend: `npm run dev` trong `frontend/`
   - mở `http://localhost:5173` và đăng ký tài khoản

## Thao tác thường dùng

| Việc | Cách làm |
|---|---|
| Xem dữ liệu | Adminer `http://localhost:8080`: **System = PostgreSQL**, Server `db`, `medical` / `medical`, DB `medical_agent` |
| Xem bằng dòng lệnh | `docker compose exec db psql -U medical medical_agent` |
| Thử API | `http://localhost:8000/docs`: login, rồi bấm **Authorize** và dán token (không kèm chữ `Bearer`) |
| Đổi cấu trúc bảng | sửa `app/models.py`, chạy `python -m alembic revision --autogenerate -m "mô tả"`, đọc lại file vừa sinh, rồi `python -m alembic upgrade head`. **Không sửa** migration đã chạy |
| Tắt / bật DB | `docker compose stop` / `docker compose start` (dữ liệu vẫn còn) |
| Xóa sạch DB | `docker compose down -v`, `docker compose up -d`, `python -m alembic upgrade head` |

## Xử lý lỗi

| Triệu chứng | Cách sửa |
|---|---|
| `Connection refused`, port 5432 | Mở Docker Desktop, chạy `docker compose up -d` |
| `DLL load failed while importing pq` | Dùng `pg8000`: `DATABASE_URL` bắt đầu bằng `postgresql+pg8000://` |
| `Application Control policy has blocked this file` | Gọi qua `python -m pip / uvicorn / alembic` |
| Adminer báo `Connection refused` | Ô System đang là MySQL, chọn **PostgreSQL** |
| `No module named 'alembic'` / `'app'` | Sai venv (phải là `backend/venv`) hoặc sai thư mục (phải đứng trong `backend/`) |
| Migration sinh ra rỗng | `alembic/env.py` thiếu `import app.models` hoặc `target_metadata` vẫn là `None` |
| `RuntimeError: Chưa đặt JWT_SECRET` | Thêm vào `.env` rồi khởi động lại backend |
| Bị đưa về màn đăng nhập "phiên đã hết hạn" | Token quá `JWT_EXPIRE_DAYS` ngày, hoặc `JWT_SECRET` vừa bị đổi. Đăng nhập lại, lịch sử vẫn còn |

## Giới hạn hiện tại
- Chưa có quên mật khẩu, xác thực email, giới hạn số lần đăng nhập sai.
- JWT không thu hồi được trước hạn. Muốn đăng xuất mọi người thì đổi `JWT_SECRET`.
- Lịch sử chat cũ trong `localStorage` không được chuyển lên DB.
- Mật khẩu Postgres trong `docker-compose.yml` chỉ dùng khi dev, phải đổi trước khi deploy.
