# ChatBot Y Tế

Chatbot hỏi đáp thông tin y tế tiếng Việt, dùng **RAG** (Retrieval-Augmented Generation): tìm các đoạn liên quan trong bài viết y khoa thu thập từ Vinmec, Medlatec và Báo Sức khỏe & Đời sống, rồi để Google Gemini sinh câu trả lời dựa trên các đoạn đó, kèm nguồn tham khảo.

> ⚠️ Đây là công cụ tham khảo thông tin, **không thay thế** chẩn đoán hay tư vấn của bác sĩ và chuyên gia y tế.

## Kiến trúc

```
                        (1) Chạy 1 lần / khi cần cập nhật dữ liệu
 data-collection/  ──►  data/benh_pho_bien_10.jsonl  ──►  ingest.py  ──►  Chroma (vector DB)
   crawl 3 nguồn          100 bài, 10 nhóm bệnh          chunk + embedding OpenAI   app/storage/chroma

                        (2) Mỗi câu hỏi của người dùng
 Frontend (React)  ──►  POST /api/chat/stream  ──►  viết lại câu hỏi nối tiếp (nếu có lịch sử)
                                                  ──►  hybrid retrieval: BM25 + dense (Chroma), gộp RRF
                                                  ──►  rerank cross-encoder  ──►  top-5 chunk
                                                                       │
 Frontend hiển thị  ◄──  SSE: sources, từng đoạn trả lời  ◄──  Gemini sinh câu trả lời
 câu trả lời + nguồn                                            từ context + lịch sử chat
```

| Thư mục                           | Vai trò                                       | Công nghệ                                                                             |
| ----------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------- |
| [backend/](backend/)                 | API chat + pipeline RAG                        | FastAPI, OpenAI embedding, Gemini API, ChromaDB, BM25, ONNX Runtime (rerank)         |
| [frontend/](frontend/)               | Giao diện chat, lưu nhiều cuộc hội thoại | React 19, Vite                                                                          |
| [data-collection/](data-collection/) | Crawler thu thập dữ liệu y tế              | requests, BeautifulSoup                                                                 |
| [docs/](docs/)                       | Tài liệu pipeline RAG và cách đánh giá  | —                                                                                      |

## Tài liệu

- [docs/RAG.md](docs/RAG.md) — pipeline RAG chi tiết: dữ liệu, ingest, chunking, model, hybrid retrieval (BM25 + dense, RRF), rerank cross-encoder, cấu hình, hạn chế đã biết.
- [docs/RAG_EVAL.md](docs/RAG_EVAL.md) — bộ golden test 70 câu, script đo retrieval và chấm end-to-end theo tiêu chí RAG, kết quả đo.
- [backend/STRUCTURE.md](backend/STRUCTURE.md) — cấu trúc code backend và luồng xử lý một request.
- [data-collection/README.md](data-collection/README.md) — crawler, nguồn dữ liệu, bản quyền.

## Yêu cầu

- **Python 3.11+**
- **Node.js 20.19+ hoặc 22.12+** và npm (yêu cầu của Vite)
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
# Mở .env và điền GEMINI_API_KEY, OPENAI_API_KEY
```

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

- Frontend: http://localhost:5173
- Backend: http://localhost:8000 — Swagger UI tại http://localhost:8000/docs

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

Các biến còn lại (`RAG_MIN_SCORE`, `RAG_CANDIDATES`, `RRF_K`, `RERANK_*`): xem mục "Cấu hình liên quan" trong [docs/RAG.md](docs/RAG.md).

### Frontend — `frontend/.env`

| Biến                 | Mặc định               | Ý nghĩa          |
| --------------------- | ------------------------- | ------------------ |
| `VITE_API_BASE_URL` | `http://localhost:8000` | Địa chỉ backend |

## API

### `POST /api/chat`

Request:

```json
{
  "message": "Triệu chứng của cơn gout cấp là gì?",
  "history": [
    { "role": "user", "content": "..." },
    { "role": "model", "content": "..." }
  ]
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

- `history` do frontend gửi kèm mỗi lần, server **không lưu** hội thoại. Frontend lưu các cuộc trò chuyện trong `localStorage` của trình duyệt.
- `role` chỉ nhận `user` hoặc `model`.
- Lỗi: `500` khi cấu hình sai (vd. thiếu API key), `502` khi gọi Gemini thất bại (mạng, quota, key sai, model không tồn tại).
- `GET /` trả về `{"status": "ok"}` để kiểm tra server còn sống.

### `POST /api/chat/stream`

Cùng request với `/api/chat`, nhưng trả về **Server-Sent Events** để hiển thị câu trả lời dần dần (frontend dùng endpoint này). Các sự kiện theo thứ tự:

| Sự kiện | `data` | Ý nghĩa |
|---|---|---|
| `status` | `{"stage": "rewriting" \| "retrieving" \| "generating"}` | Bước đang chạy (`rewriting` chỉ có khi có `history`) |
| `sources` | `{"sources": [{title, url, citation}]}` | Nguồn tham khảo, gửi ngay sau khi tìm tài liệu xong |
| `delta` | `{"text": "…"}` | Một đoạn câu trả lời — nối các `delta` lại thành câu trả lời đầy đủ |
| `done` | `{"disclaimer": "…"}` | Kết thúc |
| `error` | `{"detail": "…"}` | Lỗi khi stream đã bắt đầu (HTTP status lúc đó đã là 200) |

Lỗi cấu hình (thiếu API key…) vẫn trả về HTTP `500` trước khi mở stream.

## Cấu trúc thư mục

```
.
├── backend/
│   ├── main.py                   # Khởi tạo FastAPI + CORS
│   ├── requirements.txt
│   ├── .env.example
│   ├── STRUCTURE.md              # Giải thích cấu trúc backend
│   ├── app/
│   │   ├── config.py             # Settings đọc từ .env
│   │   ├── schemas.py            # Pydantic models (request/response)
│   │   ├── routers/chat.py       # POST /api/chat, POST /api/chat/stream (SSE)
│   │   ├── services/
│   │   │   ├── rag_service.py    # Điều phối: retrieve → gọi Gemini → gom nguồn
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
├── docs/                         # RAG.md, RAG_EVAL.md
├── frontend/
│   └── src/
│       ├── App.jsx               # Giao diện chat chính
│       ├── api/chatApi.js        # Gọi backend
│       ├── components/           # Sidebar, MessageBubble, ChatInput
│       └── utils/sessions.js     # Lưu các cuộc trò chuyện vào localStorage
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

## Bản quyền dữ liệu

Nội dung crawl thuộc về Vinmec, MEDLATEC và Báo Sức khỏe & Đời sống; dự án chỉ dùng cho mục đích học tập/thử nghiệm cá nhân. Dữ liệu từ suckhoedoisong.vn yêu cầu trích dẫn nguồn khi dùng cho AI/RAG (trường `citation` đã có sẵn trong dữ liệu). Chi tiết xem [data-collection/README.md](data-collection/README.md).
