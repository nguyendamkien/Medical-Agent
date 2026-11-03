# Cấu hình và xử lý sự cố

Mẫu cấu hình có chú thích: `backend/.env.example`, `frontend/.env.example`.

## Backend — `backend/.env`

| Biến                 | Mặc định                                        | Ý nghĩa                                                                           |
| --------------------- | -------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `GEMINI_API_KEY`    | *(bắt buộc)*                                   | API key của Gemini                                                                 |
| `GEMINI_MODEL`      | `gemini-3.1-flash-lite`                          | Model sinh câu trả lời và viết lại câu hỏi                                       |
| `OPENAI_API_KEY`    | *(bắt buộc)*                                   | API key của OpenAI, dùng cho embedding                                            |
| `EMBEDDING_MODEL`   | `text-embedding-3-small`                         | Model embedding (đổi thì phải chạy lại ingest)                                |
| `DATA_PATH`         | `../data-collection/data/benh_pho_bien_10.jsonl` | File dữ liệu dùng để build index                                               |
| `VECTOR_DB_PATH`    | `app/storage/chroma`                             | Nơi lưu Chroma                                                                    |
| `VECTOR_COLLECTION` | `medical_documents`                              | Tên collection trong Chroma                                                        |
| `RAG_TOP_K`         | `5`                                              | Số chunk đưa vào prompt cho mỗi câu hỏi                                      |
| `RETRIEVAL_MODE`    | `hybrid`                                         | `hybrid` (BM25 + dense, gộp RRF) hoặc `dense`                                 |
| `RERANK_ENABLED`    | `true`                                           | Rerank cross-encoder (~11 s/câu trên CPU, lần đầu tải model ~571 MB). Mọi số đo chất lượng đều dùng rerank; tắt thì nhanh hơn nhưng câu ngoài phạm vi không bị chặn ở retrieval |
| `QUERY_REWRITE_ENABLED` | `true`                                       | Viết lại câu hỏi nối tiếp ("Vậy điều trị thế nào?") thành câu độc lập trước khi tìm tài liệu — thêm 1 lần gọi Gemini (~1 s) khi đã có lịch sử |
| `HISTORY_MAX_TURNS` | `10`                                             | Số lượt hỏi–đáp gần nhất gửi kèm (1 lượt = 1 câu hỏi + 1 câu trả lời)         |
| `AGENT_ENABLED` | `true` | Dùng agent: Gemini tự gọi tool tìm tài liệu, 0 đến nhiều lần (xem [AGENTIC_RAG.md](AGENTIC_RAG.md)). `false` = pipeline cố định, gọi Gemini ít hơn (hợp khi quota hạn chế) |
| `AGENT_MAX_TOOL_CALLS` | `3` | Số lần tìm tài liệu tối đa cho mỗi câu hỏi (mỗi lần ~12 s vì rerank) |
| `AGENT_MAX_TOTAL_CALLS` | `8` | Số lời gọi tool tối đa, kể cả đọc bài (`get_article`) |
| `AGENT_FOLLOWUP_RERANK_CANDIDATES` | `5` | Số ứng viên rerank từ lần tìm thứ hai trong một câu hỏi (lần đầu dùng `RERANK_CANDIDATES`) |
| `DOSE_GUARDRAIL` | `true` | Quét câu trả lời của agent, che liều thuốc (hàm lượng, số lần/ngày) trước khi gửi tới người dùng |
| `AGENT_NO_CONTEXT_GUARD` | `true` | Agent không lấy được tài liệu nào thì trả lời bằng prompt "không có tài liệu" như pipeline cũ |
| `CORS_ORIGINS`      | `http://localhost:5173,http://127.0.0.1:5173`    | Các origin frontend được phép gọi API                                         |
| `DATABASE_URL`      | `postgresql+pg8000://medical:medical@localhost:5432/medical_agent` | PostgreSQL lưu tài khoản và lịch sử chat. Dùng driver `pg8000` (thuần Python) vì Windows có thể chặn DLL của `psycopg` |
| `JWT_SECRET`        | *(bắt buộc)*                                   | Khóa ký token đăng nhập. Đổi khóa thì mọi người dùng phải đăng nhập lại        |
| `JWT_EXPIRE_DAYS`   | `7`                                              | Số ngày token còn hiệu lực, hết hạn thì phải đăng nhập lại                     |
| `LANGFUSE_ENABLED` | `false` | Gửi trace lên Langfuse Cloud ([OBSERVABILITY.md](OBSERVABILITY.md)). Cần thêm `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` | Vùng EU; project ở vùng US thì dùng `https://us.cloud.langfuse.com`. Tên cũ `LANGFUSE_HOST` vẫn đọc được |
| `LANGFUSE_MASK_CONTENT` | `true` | Che câu hỏi, hồ sơ, tài liệu, câu trả lời trong trace (chỉ gửi tên bước, thời gian, token). Trace của eval luôn gửi đủ nội dung |

Các biến còn lại (`RAG_MIN_SCORE`, `RAG_CANDIDATES`, `RRF_K`, `RERANK_*`): xem mục "Cấu hình liên quan" trong [RAG.md](RAG.md).

## Frontend — `frontend/.env`

| Biến                 | Mặc định               | Ý nghĩa          |
| --------------------- | ------------------------- | ------------------ |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Địa chỉ backend |

## Xử lý sự cố

| Triệu chứng                                                                          | Nguyên nhân thường gặp                                                                                                                       |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Thiếu GEMINI_API_KEY` / `Thiếu OPENAI_API_KEY`                                  | Chưa tạo`backend/.env` hoặc chưa điền key                                                                                                 |
| `Extra inputs are not permitted` khi khởi động                                    | `.env` có biến mà `Settings` trong `app/config.py` không khai báo — kiểm tra lại tên biến                                         |
| `Không tìm thấy file dữ liệu` khi build index                                   | Chưa crawl (README, mục "Cài đặt & chạy") hoặc sai`DATA_PATH`; lệnh phải chạy từ thư mục `backend/`                                                       |
| Lỗi lệch số chiều vector khi chat                                                  | Index được tạo bằng model embedding khác — chạy lại`python -m app.scripts.ingest`                                                      |
| Bot luôn trả lời "chưa tìm thấy thông tin"                                      | Chưa build index (README, mục "Cài đặt & chạy"), hoặc ngưỡng`RAG_MIN_SCORE` / `RERANK_MIN_SCORE` quá cao                                                     |
| `Fatal error in launcher: Unable to create process…` khi gọi `pip` / `uvicorn` | Thư mục dự án bị đổi tên/di chuyển sau khi tạo venv. Dùng`python -m pip …`, `python -m uvicorn …`, hoặc xoá và tạo lại venv |
| Trả lời rất chậm                                                                   | Rerank (bật mặc định) mất ~11 s/câu trên CPU; câu hỏi đầu tiên sau khi khởi động còn phải tải/nạp model. Cần nhanh hơn thì đặt `RERANK_ENABLED=false` (chất lượng giảm, xem [RAG.md](RAG.md)) |
| `502` — model not found / quota exceeded                                            | Đổi`GEMINI_MODEL` sang model còn được hỗ trợ trong tài khoản của bạn, hoặc chờ hết giới hạn quota                              |
| Frontend báo lỗi mạng / CORS                                                        | Backend chưa chạy, hoặc`CORS_ORIGINS` chưa gồm origin của frontend                                                                        |
| `Connection refused` / `Can't create a connection to host localhost and port 5432` | Docker Desktop chưa mở, hoặc container `db` đã dừng. Mở Docker Desktop rồi chạy `docker compose up -d` |
| `DLL load failed while importing pq: An Application Control policy has blocked this file` | Windows chặn DLL của `psycopg`. Dùng driver `pg8000` (`postgresql+pg8000://…` trong `DATABASE_URL`) |
| `An Application Control policy has blocked this file` khi chạy `pip` / `uvicorn` / `alembic` | Dùng `python -m pip …`, `python -m uvicorn …`, `python -m alembic …` |
| `RuntimeError: Chưa đặt JWT_SECRET`                                               | Thêm `JWT_SECRET` vào `backend/.env` rồi khởi động lại backend |
| Adminer báo `Connection refused`                                                   | Ô System đang là MySQL. Chọn **PostgreSQL** |
| `relation "users" does not exist`                                                  | Chưa tạo bảng: `python -m alembic upgrade head` trong `backend/` |
| `column messages.facilities does not exist` / `relation "health_profiles" does not exist` | Code mới hơn DB, còn migration chưa chạy: `python -m alembic upgrade head` trong `backend/` (chỉ thêm bảng / cột, không mất dữ liệu) |
| Đang dùng bị đưa về màn đăng nhập "Phiên đăng nhập đã hết hạn"                     | Token quá `JWT_EXPIRE_DAYS` ngày, hoặc `JWT_SECRET` vừa bị đổi. Đăng nhập lại; lịch sử vẫn còn |
