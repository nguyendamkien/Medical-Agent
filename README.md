# ChatBot Y Tế

Chatbot hỏi đáp thông tin y tế tiếng Việt, dùng **Agentic RAG**: Gemini tự quyết định tìm tài liệu trong các bài viết y khoa thu thập từ Vinmec, Medlatec và Báo Sức khỏe & Đời sống, rồi trả lời dựa trên các đoạn tìm được, kèm nguồn tham khảo.

> ⚠️ Đây là công cụ tham khảo thông tin, **không thay thế** chẩn đoán hay tư vấn của bác sĩ và chuyên gia y tế.

## Ứng dụng làm được gì

- **Hỏi đáp có trích nguồn** về 10 nhóm bệnh phổ biến (100 bài viết): triệu chứng, nguyên nhân, điều trị, chế độ ăn… Mỗi câu trả lời ghi `[Nguồn i]` và link bài gốc.
- **Agent tự tìm tài liệu**: câu nhiều ý thì tìm riêng từng ý, câu nối tiếp ("Vậy điều trị thế nào?") hiểu theo lịch sử chat, lời chào thì trả lời ngay không tìm.
- **Hồ sơ sức khỏe**: người dùng khai bệnh nền, dị ứng thuốc, năm sinh, mang thai; agent dùng để tìm đúng tài liệu và cảnh báo khi tài liệu nhắc tới thuốc dị ứng.
- **Gợi ý nơi khám**: bệnh viện ở Hà Nội theo chuyên khoa, hiện thành thẻ có địa chỉ, số điện thoại đã kiểm tra trên trang chính thức.
- **An toàn**: không bịa khi không có tài liệu ("chưa có thông tin"), không nêu liều thuốc (có guardrail che bằng code), khuyên gọi **115** ngay khi có dấu hiệu cấp cứu.
- **Tài khoản và lịch sử chat** lưu trong PostgreSQL; câu trả lời hiện dần theo thời gian thực (SSE).

Dữ liệu crawl thuộc về các trang nguồn, dự án chỉ dùng cho mục đích học tập / thử nghiệm ([data-collection/README.md](data-collection/README.md)).

## Kiến trúc

```
                        (1) Chạy 1 lần / khi cần cập nhật dữ liệu
 data-collection/  ──►  data/benh_pho_bien_10.jsonl  ──►  ingest.py  ──►  Chroma (vector DB)
   crawl 3 nguồn          100 bài, 10 nhóm bệnh          chunk + embedding OpenAI   app/storage/chroma

                        (2) Mỗi câu hỏi của người dùng (đã đăng nhập)
 Frontend (React)  ──►  POST /api/chat/stream  ──►  kiểm tra token, đọc lịch sử hội thoại + hồ sơ sức khỏe từ PostgreSQL
   + token JWT          { message,               ──►  agent: Gemini tự gọi tool
                          conversation_id }          · tìm tài liệu (hybrid BM25 + dense, gộp RRF, rerank cross-encoder)
                                                     · đọc cả bài / một mục
                                                     · gợi ý bệnh viện ở Hà Nội (khi người dùng hỏi nơi khám)
                                                                       │
 Frontend hiển thị  ◄──  SSE: sources, facilities,  ◄──  Gemini sinh câu trả lời ──► guardrail che liều thuốc
 câu trả lời, nguồn,      từng đoạn trả lời              từ tài liệu + lịch sử chat + hồ sơ
 thẻ nơi khám
                                                  ──►  trả lời xong: lưu câu hỏi + câu trả lời vào PostgreSQL
```

| Thành phần | Công nghệ |
|---|---|
| LLM | Gemini `gemini-3.1-flash-lite` (function calling) |
| Embedding | OpenAI `text-embedding-3-small` |
| Retrieval | ChromaDB + BM25, gộp RRF, rerank `bge-reranker-v2-m3` (ONNX Runtime, CPU) |
| Backend | FastAPI, SQLAlchemy, Alembic, PostgreSQL 16, JWT (argon2) |
| Frontend | React 19, Vite |
| Crawler | requests, BeautifulSoup |
| Vận hành | Docker Compose, nginx, Langfuse (trace) |

## Cài đặt & chạy

**Cần có:** Python 3.11+, Node.js 20.19+ hoặc 22.12+, Docker Desktop, [Gemini API key](https://aistudio.google.com/apikey), [OpenAI API key](https://platform.openai.com/api-keys) (embed toàn bộ dữ liệu ~$0,006).

Repo **không chứa** dữ liệu crawl và vector index, nên lần đầu cần làm đủ các bước dưới đây theo thứ tự.

### 1. Thu thập dữ liệu

```bash
cd data-collection
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python run_crawl.py --source all --limit 20     # chạy thử nhanh; bộ đầy đủ: data-collection/README.md
```

Backend mặc định đọc `data-collection/data/benh_pho_bien_10.jsonl` (đổi bằng `DATA_PATH`).

### 2. Cài backend và cấu hình

```bash
cd backend
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
copy .env.example .env         # macOS/Linux: cp .env.example .env
# Điền GEMINI_API_KEY, OPENAI_API_KEY, JWT_SECRET vào .env
python -c "import secrets; print(secrets.token_urlsafe(48))"   # tạo chuỗi cho JWT_SECRET
```

> Windows bật Smart App Control có thể chặn `pip.exe`, `uvicorn.exe`, `alembic.exe` trong venv. Luôn gọi qua Python: `python -m pip`, `python -m uvicorn`, `python -m alembic`.

### 3. Database và vector index

```bash
docker compose up -d                 # ở thư mục gốc: PostgreSQL + Adminer (http://localhost:8080)
cd backend
python -m alembic upgrade head       # tạo / cập nhật bảng; chạy lại mỗi lần kéo code mới
python -m app.scripts.ingest         # tạo vector index, ~1 phút
```

### 4. Chạy

**Cách A: chạy trực tiếp (để phát triển)**

```bash
cd backend && python -m uvicorn main:app --reload --port 8000     # terminal 1
cd frontend && npm install && npm run dev                         # terminal 2
```

Mở http://localhost:5173, đăng ký tài khoản rồi chat. Swagger: http://localhost:8000/docs.

**Cách B: Docker (để demo)**, sau khi đã có `backend/.env` và vector index:

```bash
docker compose --profile app up -d --build
```

Mở http://localhost:3000. Chi tiết và cách chép sẵn model rerank: [docs/DOCKER.md](docs/DOCKER.md).

Bảng biến cấu hình và cách xử lý lỗi thường gặp: [docs/CONFIG.md](docs/CONFIG.md). Danh sách API: [docs/API.md](docs/API.md).

## Cấu trúc thư mục

| Thư mục | Nội dung |
|---|---|
| [backend/](backend/) | FastAPI: `app/routers` (API), `app/services` (agent, RAG, Gemini, gợi ý nơi khám), `app/rag` (chunk, embedding, BM25, retriever, rerank, prompt, tool, guardrail), `alembic/` (migration), `eval/` (golden set + script đo). Chi tiết: [backend/STRUCTURE.md](backend/STRUCTURE.md) |
| [frontend/](frontend/) | React + Vite: màn đăng nhập, chat, danh sách hội thoại, hồ sơ sức khỏe, thẻ nơi khám. Chi tiết: [frontend/STRUCTURE.md](frontend/STRUCTURE.md) |
| [data-collection/](data-collection/) | Crawler 3 nguồn; dữ liệu crawl không nằm trong git |
| [docs/](docs/) | Tài liệu thiết kế (RAG, agent, hồ sơ sức khỏe, gợi ý nơi khám), đánh giá, cấu hình, API, Docker, trace |
| `docker-compose.yml` | PostgreSQL + Adminer; profile `app` thêm backend + frontend |

## Đánh giá

### Tập đánh giá

Golden set **108 câu** ([backend/eval/golden_set.jsonl](backend/eval/golden_set.jsonl)), đáp án tham chiếu trích từ chính các bài trong dữ liệu:

| Loại câu | Số câu | Kiểm tra |
|---|---|---|
| `factual`, `specific` | 32 + 20 | Kiến thức nền và chi tiết trong một bài |
| `multi_doc`, `article` | 10 + 2 | Cần ghép nhiều bài / nhiều mục |
| `follow_up` | 8 | Câu nối tiếp, có lịch sử chat |
| `profile` | 12 | Dùng hồ sơ sức khỏe đúng lúc, cảnh báo dị ứng, thai kỳ |
| `facility` | 8 | Gợi ý nơi khám đúng chuyên khoa, không bịa số điện thoại |
| `safety` | 7 | Từ chối kê liều, khuyên gọi 115 khi cấp cứu |
| `out_of_kb`, `off_topic`, `no_search`, `disambiguation` | 3 + 2 + 3 + 1 | Ngoài dữ liệu, ngoài lĩnh vực, lời chào, phân biệt bệnh gần nhau |

Hai script:

```bash
cd backend
python -m eval.eval_retrieval --rerank          # bước tìm tài liệu: hit@k, MRR (chấm theo nhãn, ~30 phút trên CPU)
python -m eval.eval_rag --agent --workers 1     # cả hệ thống: 4 tiêu chí RAGAS, giám khảo gpt-4.1-mini (~45 phút)
```

### Kết quả (02/11/2026)

| Chỉ số (108 câu) | Điểm |
|---|---|
| context_precision | **0,97** |
| context_recall | **0,97** |
| faithfulness | **0,99** |
| answer_relevancy | **1,00** |
| Retrieval, 70 câu gốc: hit@1 / hit@5 / MRR | 0,91 / 0,95 / 0,93 |

- **An toàn:** 0 lần nêu liều thuốc trên 108 câu; câu cấp cứu luôn khuyên gọi 115 ở câu đầu; 5/5 câu ngoài phạm vi bị chặn ngay ở bước tìm tài liệu.
- **Hành vi agent:** gợi ý nơi khám 8/8 gọi đúng tool; các quy tắc bắt buộc nhắc / cấm nhắc 19/19; không có lần tìm thừa.
- **Thời gian trả lời** trung vị ~20 s mỗi câu, phần lớn do rerank trên CPU.
- So với bản đầu (dense retrieval, 27/09): context_precision 0,81 → 0,97; câu nối tiếp hit@1 0,40 → 1,00.

Bộ test là bộ nội bộ, chưa có chuyên gia y tế duyệt; dùng để so các phiên bản của chatbot này, không phải benchmark. Chi tiết từng loại câu, lỗi đã sửa và vấn đề còn mở: [docs/RAG_EVAL.md](docs/RAG_EVAL.md).
