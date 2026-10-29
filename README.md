# ChatBot Y Tế

Chatbot hỏi đáp thông tin y tế tiếng Việt, dùng **RAG** (Retrieval-Augmented Generation): tìm các đoạn liên quan trong bài viết y khoa thu thập từ Vinmec, Medlatec và Báo Sức khỏe & Đời sống, rồi để Google Gemini sinh câu trả lời dựa trên các đoạn đó, kèm nguồn tham khảo.

> ⚠️ Đây là công cụ tham khảo thông tin, **không thay thế** chẩn đoán hay tư vấn của bác sĩ và chuyên gia y tế.

## Kiến trúc

```
                        (1) Chạy 1 lần / khi cần cập nhật dữ liệu
 data-collection/  ──►  data/benh_pho_bien_10.jsonl  ──►  ingest.py  ──►  Chroma (vector DB)
   crawl 3 nguồn          100 bài, 10 nhóm bệnh          chunk + embedding OpenAI   app/storage/chroma

                        (2) Mỗi câu hỏi của người dùng (đã đăng nhập)
 Frontend (React)  ──►  POST /api/chat/stream  ──►  kiểm tra token, đọc lịch sử hội thoại từ PostgreSQL
   + token JWT          { message,               ──►  agent: Gemini tự gọi tool tìm tài liệu
                          conversation_id }        (hybrid BM25 + dense, gộp RRF, rerank cross-encoder)
                                                                       │
 Frontend hiển thị  ◄──  SSE: sources, từng đoạn trả lời  ◄──  Gemini sinh câu trả lời
 câu trả lời + nguồn                                            từ tài liệu + lịch sử chat
                                                  ──►  trả lời xong: lưu câu hỏi + câu trả lời vào PostgreSQL
```

| Thư mục                           | Vai trò                                       | Công nghệ                                                                             |
| ----------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------- |
| [backend/](backend/)                 | API chat + pipeline RAG, đăng nhập, lưu lịch sử chat | FastAPI, OpenAI embedding, Gemini API, ChromaDB, BM25, ONNX Runtime (rerank), PostgreSQL (SQLAlchemy, Alembic), JWT |
| [frontend/](frontend/)               | Giao diện đăng nhập + chat, danh sách hội thoại | React 19, Vite                                                                          |
| [data-collection/](data-collection/) | Crawler thu thập dữ liệu y tế              | requests, BeautifulSoup                                                                 |
| [docs/](docs/)                       | Tài liệu pipeline RAG, cách đánh giá, đăng nhập + lịch sử chat | —                                                                                      |
| [docker-compose.yml](docker-compose.yml) | PostgreSQL 16 + Adminer (xem dữ liệu)     | Docker                                                                                  |

## Tài liệu

- [docs/RAG.md](docs/RAG.md): pipeline RAG chi tiết, gồm dữ liệu, ingest, chunking, model, hybrid retrieval (BM25 + dense, RRF), rerank cross-encoder, cấu hình, hạn chế đã biết.
- [docs/AGENTIC_RAG.md](docs/AGENTIC_RAG.md): agent tự gọi tool tìm tài liệu (bật mặc định), guardrail liều thuốc.
- [docs/RAG_EVAL.md](docs/RAG_EVAL.md): bộ golden test, script đo retrieval và chấm end-to-end theo tiêu chí RAG, kết quả đo.
- [docs/AUTH_CHAT_HISTORY.md](docs/AUTH_CHAT_HISTORY.md): đăng ký / đăng nhập (JWT) và lưu lịch sử chat bằng PostgreSQL. Ghi lại những gì đã làm, các bước cài đặt, thao tác thường dùng và cách xử lý lỗi.
- [backend/STRUCTURE.md](backend/STRUCTURE.md): cấu trúc code backend và luồng xử lý một request.
- [frontend/STRUCTURE.md](frontend/STRUCTURE.md): cấu trúc code frontend, luồng đăng nhập và gửi tin nhắn.
- [data-collection/README.md](data-collection/README.md): crawler, nguồn dữ liệu, bản quyền.

## Yêu cầu

- **Python 3.11+**
- **Node.js 20.19+ hoặc 22.12+** và npm (yêu cầu của Vite)
- **Docker Desktop**: chạy PostgreSQL lưu tài khoản và lịch sử chat. Không cần cài PostgreSQL lên máy
- **Gemini API key** — sinh câu trả lời (https://aistudio.google.com/apikey)
- **OpenAI API key** — tạo embedding (https://platform.openai.com/api-keys). Chi phí rất nhỏ: embed toàn bộ dữ liệu 10 bệnh ~$0.006, mỗi câu hỏi không đáng kể.

## Cài đặt & chạy

Repo **không chứa** dữ liệu crawl và vector index (đã bỏ qua trong `.gitignore`), nên lần đầu cần làm đủ 4 bước dưới đây theo thứ tự.

### Bước 1 — Thu thập dữ liệu

```bash
cd data-collection
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt

python run_crawl.py --source all --limit 20     # chạy thử nhanh, mỗi nguồn 20 bài
```

Để lấy bộ dữ liệu đầy đủ và các tuỳ chọn khác, xem [data-collection/README.md](data-collection/README.md). Backend mặc định đọc `data-collection/data/benh_pho_bien_10.jsonl` (100 bài của 10 nhóm bệnh phổ biến); đổi `DATA_PATH` để dùng file khác, vd `combined.jsonl` (2.306 bài).

### Bước 2 — Cấu hình & cài backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
python -m pip install -r requirements.txt

copy .env.example .env         # macOS/Linux: cp .env.example .env
# Mở .env và điền GEMINI_API_KEY, OPENAI_API_KEY, JWT_SECRET
python -c "import secrets; print(secrets.token_urlsafe(48))"   # tạo chuỗi ngẫu nhiên cho JWT_SECRET
```

> Trên máy bật Smart App Control (Windows 11), các file `pip.exe`, `uvicorn.exe`, `alembic.exe` trong venv có thể bị chặn. Luôn gọi qua Python: `python -m pip`, `python -m uvicorn`, `python -m alembic`.

### Bước 2b: Database (PostgreSQL)

Mở Docker Desktop, rồi chạy ở thư mục gốc của repo:

```bash
docker compose up -d                 # PostgreSQL ở localhost:5432, Adminer ở http://localhost:8080

cd backend
python -m alembic upgrade head       # tạo các bảng users, conversations, messages
```

`DATABASE_URL` mặc định trong `.env.example` đã khớp với `docker-compose.yml`. Xem dữ liệu bằng Adminer: `http://localhost:8080`, **System = PostgreSQL**, Server `db`, user/mật khẩu `medical`/`medical`, database `medical_agent`.

### Bước 3 — Tạo vector index (chạy 1 lần)

```bash
cd backend
python -m app.scripts.ingest
```

Script chia bài viết thành chunk theo từng mục (Triệu chứng, Điều trị…), tạo embedding bằng OpenAI `text-embedding-3-small` (32 chunk mỗi request) và lưu vào Chroma. Với dữ liệu 10 bệnh: 505 chunk, khoảng 1 phút. Chạy lại script sẽ xoá index cũ và tạo mới — sau đó **khởi động lại backend** để chỉ mục BM25 được dựng lại theo index mới.

### Bước 4 — Chạy backend và frontend

```bash
# Terminal 1 — backend
cd backend
python -m uvicorn main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend
npm install
copy .env.example .env         # tuỳ chọn, chỉ cần khi backend không ở localhost:8000
npm run dev
```

- Frontend: http://localhost:5173. Đăng ký tài khoản rồi chat. Lịch sử được lưu theo tài khoản.
- Backend: http://localhost:8000. Swagger UI tại http://localhost:8000/docs, có nút **Authorize** để dán token khi thử API.
- Nhớ bật Docker Desktop (`docker compose up -d`) trước khi chạy backend.

### Đánh giá chất lượng (tuỳ chọn)

```bash
cd backend
python -m eval.eval_retrieval      # đo retrieval trên golden set, < $0.001
python -m eval.eval_rag            # chấm toàn bộ pipeline bằng LLM giám khảo, ~10 phút
```

Chi tiết và kết quả: [docs/RAG_EVAL.md](docs/RAG_EVAL.md).

## Cấu hình

### Backend — `backend/.env`

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
| `AGENT_ENABLED` | `true` | Dùng agent: Gemini tự gọi tool tìm tài liệu, 0 đến nhiều lần (xem [docs/AGENTIC_RAG.md](docs/AGENTIC_RAG.md)). `false` = pipeline cố định, gọi Gemini ít hơn (hợp khi quota hạn chế) |
| `AGENT_MAX_TOOL_CALLS` | `3` | Số lần tìm tài liệu tối đa cho mỗi câu hỏi (mỗi lần ~12 s vì rerank) |
| `AGENT_MAX_TOTAL_CALLS` | `8` | Số lời gọi tool tối đa, kể cả đọc bài (`get_article`) |
| `AGENT_FOLLOWUP_RERANK_CANDIDATES` | `5` | Số ứng viên rerank từ lần tìm thứ hai trong một câu hỏi (lần đầu dùng `RERANK_CANDIDATES`) |
| `DOSE_GUARDRAIL` | `true` | Quét câu trả lời của agent, che liều thuốc (hàm lượng, số lần/ngày) trước khi gửi tới người dùng |
| `AGENT_NO_CONTEXT_GUARD` | `true` | Agent không lấy được tài liệu nào thì trả lời bằng prompt "không có tài liệu" như pipeline cũ |
| `CORS_ORIGINS`      | `http://localhost:5173,http://127.0.0.1:5173`    | Các origin frontend được phép gọi API                                         |
| `DATABASE_URL`      | `postgresql+pg8000://medical:medical@localhost:5432/medical_agent` | PostgreSQL lưu tài khoản và lịch sử chat. Dùng driver `pg8000` (thuần Python) vì Windows có thể chặn DLL của `psycopg` |
| `JWT_SECRET`        | *(bắt buộc)*                                   | Khóa ký token đăng nhập. Đổi khóa thì mọi người dùng phải đăng nhập lại        |
| `JWT_EXPIRE_DAYS`   | `7`                                              | Số ngày token còn hiệu lực, hết hạn thì phải đăng nhập lại                     |

Các biến còn lại (`RAG_MIN_SCORE`, `RAG_CANDIDATES`, `RRF_K`, `RERANK_*`): xem mục "Cấu hình liên quan" trong [docs/RAG.md](docs/RAG.md).

### Frontend — `frontend/.env`

| Biến                 | Mặc định               | Ý nghĩa          |
| --------------------- | ------------------------- | ------------------ |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Địa chỉ backend |

## API

Mọi API trừ đăng ký / đăng nhập đều **bắt buộc đăng nhập**: gửi header `Authorization: Bearer <access_token>`. Thiếu, sai hoặc hết hạn token thì nhận `401`. `GET /` trả về `{"status": "ok"}` để kiểm tra server còn sống.

### Đăng nhập

| API | Body | Trả về |
|---|---|---|
| `POST /api/auth/register` | `{ email, password (8–128 ký tự), display_name }` | `201` thông tin user. Email đã có thì `409`, sai định dạng thì `422` |
| `POST /api/auth/login` | `{ email, password }` | `{ access_token, token_type: "bearer", user }`. Sai email hoặc mật khẩu thì `401` |
| `GET /api/auth/me` | (không có) | Thông tin user của token: `{ id, email, display_name, created_at }` |

### Hội thoại

| API | Ý nghĩa |
|---|---|
| `GET /api/conversations` | Danh sách hội thoại của user, mới nhất trước: `[{ id, title, created_at, updated_at }]` |
| `GET /api/conversations/{id}/messages` | Tin nhắn theo thứ tự: `[{ id, role: "user"\|"model", content, sources, created_at }]` |
| `PATCH /api/conversations/{id}` | Đổi tên, body `{ title }` |
| `DELETE /api/conversations/{id}` | Xóa hội thoại kèm toàn bộ tin nhắn (`204`) |

Hội thoại của user khác trả `404`. Không có API tạo hội thoại: hội thoại được tạo khi gửi câu hỏi đầu tiên.

### `POST /api/chat`

Request:

```json
{
  "message": "Triệu chứng của cơn gout cấp là gì?",
  "conversation_id": "9f3a…"
}
```

Response:

```json
{
  "reply": "…",
  "sources": [
    { "title": "…", "url": "https://…", "citation": "Theo Báo Sức khỏe & Đời sống - …" }
  ],
  "disclaimer": "Thông tin trên chỉ mang tính tham khảo, không thay thế …"
}
```

- `conversation_id` để trống (hoặc `null`) ở câu hỏi đầu tiên thì server tạo hội thoại mới, tiêu đề lấy từ câu hỏi.
- Server tự đọc lịch sử (`HISTORY_MAX_TURNS` lượt gần nhất) từ DB. Client **không gửi** `history`.
- Trả lời xong, server lưu câu hỏi và câu trả lời (kèm `sources`) vào DB. Lỗi giữa chừng thì không lưu gì.
- Lỗi: `401` khi chưa đăng nhập, `404` khi hội thoại không thuộc user, `500` khi cấu hình sai (vd. thiếu API key), `502` khi gọi Gemini thất bại (mạng, quota, key sai, model không tồn tại).

### `POST /api/chat/stream`

Cùng request với `/api/chat`, nhưng trả về **Server-Sent Events** để hiển thị câu trả lời dần dần (frontend dùng endpoint này). Các sự kiện theo thứ tự:

| Sự kiện | `data` | Ý nghĩa |
|---|---|---|
| `conversation` | `{"id": "…", "title": "…"}` | Luôn đến đầu tiên: id của hội thoại (id mới nếu vừa tạo) |
| `status` | `{"stage": "thinking" \| "retrieving" \| "reading" \| "generating", …}` | Bước đang chạy. Agent gửi kèm `query` khi tìm, `document_id` khi đọc bài |
| `sources` | `{"sources": [{title, url, citation}]}` | Nguồn tham khảo. Agent gửi lại sau mỗi lần gọi tool, mỗi lần là danh sách đầy đủ |
| `delta` | `{"text": "…"}` | Một đoạn câu trả lời. Nối các `delta` lại thành câu trả lời đầy đủ |
| `done` | `{"disclaimer": "…"}` | Kết thúc, lượt chat đã được lưu |
| `error` | `{"detail": "…"}` | Lỗi khi stream đã bắt đầu (HTTP status lúc đó đã là 200). Lượt này không được lưu |

Lỗi `401` / `404` / `500` (chưa đăng nhập, hội thoại không thuộc user, thiếu API key…) vẫn trả về bằng HTTP status trước khi mở stream.

## Cấu trúc thư mục

```
.
├── docker-compose.yml            # PostgreSQL 16 + Adminer
├── backend/
│   ├── main.py                   # Khởi tạo FastAPI + CORS, gắn router auth / conversations / chat
│   ├── requirements.txt
│   ├── .env.example
│   ├── alembic.ini, alembic/     # Migration tạo / sửa bảng PostgreSQL
│   ├── STRUCTURE.md              # Giải thích cấu trúc backend
│   ├── app/
│   │   ├── config.py             # Settings đọc từ .env
│   │   ├── schemas.py            # Pydantic models (request/response)
│   │   ├── db.py                 # Kết nối PostgreSQL (SQLAlchemy engine, session)
│   │   ├── models.py             # Bảng users, conversations, messages
│   │   ├── security.py           # Băm mật khẩu (argon2), JWT, get_current_user
│   │   ├── routers/
│   │   │   ├── auth.py           # Đăng ký, đăng nhập, /me
│   │   │   ├── conversations.py  # Danh sách / tin nhắn / đổi tên / xoá hội thoại
│   │   │   └── chat.py           # POST /api/chat, POST /api/chat/stream (SSE), lưu lịch sử
│   │   ├── services/
│   │   │   ├── rag_service.py    # Điều phối: retrieve → gọi Gemini → gom nguồn
│   │   │   ├── agent_service.py  # Agent: Gemini tự gọi tool tìm tài liệu (mặc định)
│   │   │   └── gemini_service.py # Gọi Gemini, chứa system prompt an toàn y tế
│   │   ├── rag/
│   │   │   ├── loader.py         # Đọc JSONL
│   │   │   ├── chunker.py        # Chia chunk theo section
│   │   │   ├── embeddings.py     # OpenAI embedding
│   │   │   ├── vector_store.py   # Bọc ChromaDB
│   │   │   ├── bm25.py           # Chỉ mục BM25 trong RAM (tìm theo từ khoá)
│   │   │   ├── retriever.py      # Hybrid BM25 + dense, gộp RRF, ngưỡng, gọi rerank
│   │   │   ├── reranker.py       # Cross-encoder bge-reranker-v2-m3 (ONNX)
│   │   │   └── prompt.py         # Ghép context + câu hỏi thành prompt
│   │   └── scripts/ingest.py     # Tạo vector index
│   └── eval/
│       ├── golden_set.jsonl      # 70 câu hỏi chuẩn
│       ├── eval_retrieval.py     # Đo retrieval
│       └── eval_rag.py           # Chấm end-to-end bằng LLM giám khảo
├── docs/                         # RAG.md, AGENTIC_RAG.md, RAG_EVAL.md, AUTH_CHAT_HISTORY.md
├── frontend/
│   └── src/
│       ├── App.jsx               # Đăng nhập, danh sách hội thoại, gửi tin nhắn
│       ├── api/chatApi.js        # Gọi backend (gắn token, xử lý 401)
│       ├── components/           # AuthScreen, Sidebar, MessageBubble, ChatInput
│       └── utils/sessions.js     # Lời chào, đổi tin nhắn từ API sang dạng hiển thị
└── data-collection/
    ├── run_crawl.py              # CLI chạy crawler
    ├── crawlers/                 # vinmec.py, medlatec.py, suckhoedoisong.py
    ├── config.py, utils.py
    └── data/                     # Kết quả crawl (không nằm trong git)
```

## Cách chatbot trả lời an toàn

- Prompt RAG yêu cầu Gemini **chỉ trả lời dựa trên context** tìm được; nếu không đủ thông tin thì nói rõ là chưa tìm thấy, không bịa. Đo trên golden set: 5/5 câu ngoài phạm vi dữ liệu đều được trả lời "chưa tìm thấy thông tin".
- Không chẩn đoán cá nhân, không kê đơn hay liều thuốc; khuyên gọi **115** hoặc đến cơ sở y tế khi có dấu hiệu cấp cứu.
- Mỗi câu trả lời kèm danh sách nguồn (link bài gốc) và câu disclaimer cố định.

## Dữ liệu người dùng

- Lịch sử chat là dữ liệu sức khỏe nhạy cảm, nên được lưu theo tài khoản trong PostgreSQL. Mỗi user chỉ đọc được hội thoại của mình, và có thể xóa từng hội thoại.
- Mật khẩu chỉ lưu dạng băm argon2. Token đăng nhập (JWT) được ký bằng `JWT_SECRET` và chỉ chứa id user.
- `backend/.env` (API key, `JWT_SECRET`) không được commit. Mật khẩu PostgreSQL trong `docker-compose.yml` chỉ dùng khi dev trên máy, phải đổi trước khi deploy.

## Xử lý sự cố

| Triệu chứng                                                                          | Nguyên nhân thường gặp                                                                                                                       |
| -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Thiếu GEMINI_API_KEY` / `Thiếu OPENAI_API_KEY`                                  | Chưa tạo`backend/.env` hoặc chưa điền key                                                                                                 |
| `Extra inputs are not permitted` khi khởi động                                    | `.env` có biến mà `Settings` trong `app/config.py` không khai báo — kiểm tra lại tên biến                                         |
| `Không tìm thấy file dữ liệu` khi build index                                   | Chưa crawl (Bước 1) hoặc sai`DATA_PATH`; lệnh phải chạy từ thư mục `backend/`                                                       |
| Lỗi lệch số chiều vector khi chat                                                  | Index được tạo bằng model embedding khác — chạy lại`python -m app.scripts.ingest`                                                      |
| Bot luôn trả lời "chưa tìm thấy thông tin"                                      | Chưa build index (Bước 3), hoặc ngưỡng`RAG_MIN_SCORE` / `RERANK_MIN_SCORE` quá cao                                                     |
| `Fatal error in launcher: Unable to create process…` khi gọi `pip` / `uvicorn` | Thư mục dự án bị đổi tên/di chuyển sau khi tạo venv. Dùng`python -m pip …`, `python -m uvicorn …`, hoặc xoá và tạo lại venv |
| Trả lời rất chậm                                                                   | Rerank (bật mặc định) mất ~11 s/câu trên CPU; câu hỏi đầu tiên sau khi khởi động còn phải tải/nạp model. Cần nhanh hơn thì đặt `RERANK_ENABLED=false` (chất lượng giảm, xem [docs/RAG.md](docs/RAG.md)) |
| `502` — model not found / quota exceeded                                            | Đổi`GEMINI_MODEL` sang model còn được hỗ trợ trong tài khoản của bạn, hoặc chờ hết giới hạn quota                              |
| Frontend báo lỗi mạng / CORS                                                        | Backend chưa chạy, hoặc`CORS_ORIGINS` chưa gồm origin của frontend                                                                        |
| `Connection refused` / `Can't create a connection to host localhost and port 5432` | Docker Desktop chưa mở, hoặc container `db` đã dừng. Mở Docker Desktop rồi chạy `docker compose up -d` |
| `DLL load failed while importing pq: An Application Control policy has blocked this file` | Windows chặn DLL của `psycopg`. Dùng driver `pg8000` (`postgresql+pg8000://…` trong `DATABASE_URL`) |
| `An Application Control policy has blocked this file` khi chạy `pip` / `uvicorn` / `alembic` | Dùng `python -m pip …`, `python -m uvicorn …`, `python -m alembic …` |
| `RuntimeError: Chưa đặt JWT_SECRET`                                               | Thêm `JWT_SECRET` vào `backend/.env` rồi khởi động lại backend |
| Adminer báo `Connection refused`                                                   | Ô System đang là MySQL. Chọn **PostgreSQL** |
| `relation "users" does not exist`                                                  | Chưa tạo bảng: `python -m alembic upgrade head` trong `backend/` |
| Đang dùng bị đưa về màn đăng nhập "Phiên đăng nhập đã hết hạn"                     | Token quá `JWT_EXPIRE_DAYS` ngày, hoặc `JWT_SECRET` vừa bị đổi. Đăng nhập lại; lịch sử vẫn còn |
