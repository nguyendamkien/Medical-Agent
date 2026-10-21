# Cấu trúc Backend (FastAPI + RAG)

Chi tiết về pipeline RAG (chunking, hybrid retrieval, rerank, số liệu đo): xem [../docs/RAG.md](../docs/RAG.md). File này tập trung vào **code nằm ở đâu và gọi nhau thế nào**.

## Cây thư mục

```
backend/
├── main.py                        # Điểm khởi động app: tạo FastAPI instance, gắn CORS, gắn router
├── requirements.txt               # Danh sách thư viện Python cần cài
├── .env.example                   # Mẫu file cấu hình (copy thành .env rồi điền key thật)
├── app/
│   ├── config.py                  # Đọc cấu hình từ .env (Settings)
│   ├── schemas.py                 # Định nghĩa request/response body (Pydantic models)
│   ├── routers/
│   │   └── chat.py                # POST /api/chat (trả một lần) và POST /api/chat/stream (SSE)
│   ├── services/
│   │   ├── rag_service.py         # Điều phối một câu hỏi: retrieve → prompt → Gemini → nguồn
│   │   ├── agent_service.py       # Agent (AGENT_ENABLED=true): Gemini tự gọi tool tìm tài liệu
│   │   └── gemini_service.py      # Gọi Google Gemini, chứa system instruction an toàn y tế
│   ├── rag/
│   │   ├── loader.py              # Đọc bài viết từ file JSONL
│   │   ├── chunker.py             # Chia bài thành chunk theo section
│   │   ├── embeddings.py          # Tạo embedding bằng OpenAI
│   │   ├── vector_store.py        # Bọc ChromaDB: lưu / tìm / đọc toàn bộ chunk
│   │   ├── bm25.py                # Chỉ mục BM25 trong RAM (tìm theo từ khoá)
│   │   ├── retriever.py           # Hybrid BM25 + dense, gộp RRF, ngưỡng, gọi rerank
│   │   ├── reranker.py            # Cross-encoder bge-reranker-v2-m3 (mặc định bật)
│   │   ├── prompt.py              # Ghép các chunk + câu hỏi thành prompt
│   │   ├── tools.py               # Tool của agent: search_medical_docs, get_article
│   │   └── guardrail.py           # Quét câu trả lời, che liều thuốc trước khi gửi (kể cả khi stream)
│   ├── scripts/
│   │   └── ingest.py              # Build vector index từ file dữ liệu
│   └── storage/chroma/            # Dữ liệu Chroma trên đĩa (không nằm trong git)
└── eval/
    ├── golden_set.jsonl           # 70 câu hỏi chuẩn để đánh giá
    ├── eval_retrieval.py          # Đo retrieval (hit@k, MRR…)
    └── eval_rag.py                # Chấm end-to-end theo tiêu chí RAG bằng LLM giám khảo
```

## Hai luồng chính

### 1. Ingest — chạy 1 lần: `python -m app.scripts.ingest`

```
scripts/ingest.py
    │  đọc DATA_PATH
    ▼
rag/loader.py          — đọc từng dòng JSONL, bỏ bản ghi thiếu id / full_text
    ▼
rag/chunker.py         — mỗi section → 1 chunk (≤ 2400 ký tự), section dài thì cắt theo câu, overlap 300
    ▼
rag/embeddings.py      — embed 32 chunk / request bằng OpenAI text-embedding-3-small
    ▼
rag/vector_store.py    — reset collection, upsert (id, text, embedding, metadata) vào Chroma
```

### 2. Một request chat: `POST /api/chat`

```
Frontend (React)
    │  POST /api/chat  { message, history }
    ▼
main.py                    — app FastAPI nhận request, CORS middleware kiểm tra origin
    ▼
routers/chat.py            — validate body theo ChatRequest, gọi RagService.ask
    ▼
services/rag_service.py
    │
    ├─► rag/retriever.py   — tìm top-5 chunk liên quan
    │       ├─ embeddings.embed_query → vector_store.search      (dense: top-20 theo cosine)
    │       ├─ bm25.BM25Index.search                             (BM25:  top-20 theo từ khoá)
    │       ├─ reciprocal_rank_fusion                            (gộp 2 danh sách theo thứ hạng)
    │       └─ [reranker.rerank]                                 (nếu RERANK_ENABLED=true)
    │
    ├─► rag/prompt.py      — ghép chunk thành "[Nguồn i] tiêu đề | Mục: …" + quy tắc trả lời
    │
    └─► services/gemini_service.py
            — gửi prompt + history + SYSTEM_INSTRUCTION tới Gemini, nhận câu trả lời
    ▼
routers/chat.py            — bọc vào ChatResponse { reply, sources, disclaimer }
    ▼
Frontend hiển thị câu trả lời + danh sách nguồn
```

Khi `AGENT_ENABLED=true`, `routers/chat.py` gọi `AgentService` thay cho `RagService`: Gemini tự quyết định gọi `search_medical_docs` / `get_article` (0 đến `AGENT_MAX_TOOL_CALLS` lần) rồi mới trả lời; không còn bước viết lại câu hỏi. Chi tiết: [../docs/AGENTIC_RAG.md](../docs/AGENTIC_RAG.md).

## Giải thích từng file

### `main.py`
- Tạo instance `FastAPI()` — app chính, được `uvicorn` chạy (`python -m uvicorn main:app`).
- Gắn `CORSMiddleware` để frontend (origin khác, vd `localhost:5173`) gọi được API — danh sách origin đọc từ `Settings.cors_origins_list`.
- `app.include_router(chat.router)` gắn các endpoint trong `app/routers/chat.py`.
- `GET /` là health-check, trả về `{"status": "ok", ...}`.

### `app/config.py`
- Class `Settings` kế thừa `pydantic_settings.BaseSettings` — đọc biến môi trường **hoặc** file `.env`, không phân biệt hoa thường tên biến.
- Biến `.env` không khai báo trong `Settings` sẽ gây lỗi `Extra inputs are not permitted` khi khởi động.
- Các nhóm cấu hình: API key và model (Gemini, OpenAI embedding), Chroma, dữ liệu, retrieval (`RAG_TOP_K`, `RAG_MIN_SCORE`, `RETRIEVAL_MODE`, `RAG_CANDIDATES`, `RRF_K`), rerank (`RERANK_*`), CORS. Bảng đầy đủ: [../docs/RAG.md](../docs/RAG.md).
- `get_settings()` được bọc `@lru_cache` — chỉ đọc `.env` **một lần** cho cả vòng đời app. Đổi `.env` thì phải khởi động lại server.

### `app/schemas.py`
"Hợp đồng dữ liệu" giữa frontend và backend — FastAPI tự validate theo đây và sinh docs tại `/docs`.

- `ChatMessage`: 1 tin nhắn trong lịch sử — `role` chỉ nhận `"user"` hoặc `"model"` (convention của Gemini SDK), `content` là nội dung.
- `ChatRequest`: body của `POST /api/chat` — `message` (câu hỏi mới, không rỗng) và `history` (list `ChatMessage`, mặc định rỗng).
- `Source`: một nguồn tham khảo — `title`, `url`, `citation` (trích dẫn bắt buộc với bài của Sức khỏe & Đời sống).
- `ChatResponse`: `reply`, `sources` và `disclaimer` (câu cảnh báo y tế cố định).

### `app/routers/chat.py`
- `APIRouter(prefix="/api")` — mọi route trong file có tiền tố `/api`.
- `_get_service()`: `AGENT_ENABLED=true` → `get_agent_service()`, ngược lại `get_rag_service()`. Hai service có cùng `ask` / `ask_stream`.
- `POST /api/chat`: gọi `service.ask(message, history)`, bọc kết quả vào `ChatResponse`. Frontend không dùng nữa; giữ cho script đánh giá và gọi API trực tiếp.
- `POST /api/chat/stream` (frontend dùng): cùng body, trả `text/event-stream` từ `RagService.ask_stream`. Thứ tự sự kiện:
  - `status` `{stage}` — `rewriting` (chỉ khi có history) → `retrieving` → `generating`. Với agent: `thinking` → mỗi lần gọi tool một `retrieving` `{stage, query}` hoặc `reading` `{stage, document_id}` → `generating`
  - `sources` `{sources}` — ngay sau khi retrieve xong; với agent, phát lại sau mỗi vòng gọi tool, mỗi lần là danh sách đầy đủ tới lúc đó
  - `delta` `{text}` — từng đoạn câu trả lời từ Gemini (`GeminiService.ask_stream`, `stream=True`)
  - `done` `{disclaimer}` — kết thúc; hoặc `error` `{detail}` nếu lỗi giữa chừng (HTTP status lúc đó đã là 200, không đổi được nữa)
- Header `X-Accel-Buffering: no` để nginx (nếu deploy sau proxy) không gom sự kiện lại.
- Map lỗi sang HTTP status:
  - `RuntimeError` (vd thiếu API key) → **500** — lỗi cấu hình phía server.
  - Exception khác (mạng, quota, key sai…) → **502** — lỗi từ dịch vụ bên ngoài.

### `app/services/rag_service.py`
- `RagService.ask(message, history)`: cắt history còn `HISTORY_MAX_TURNS` lượt (`trim_history`) → viết lại câu hỏi nối tiếp thành câu độc lập để retrieve (`build_search_query`, khi `QUERY_REWRITE_ENABLED`) → retrieve → dựng prompt → gọi Gemini → `collect_sources` gom `sources` từ metadata, **mỗi bài một nguồn** (nhiều chunk cùng bài chỉ tính một lần). Trả về `(reply, sources)`.
- `RagService.ask_with_context(...)`: như trên nhưng trả thêm danh sách chunk đã retrieve và câu đã dùng để retrieve — dùng cho `eval/eval_rag.py`.
- `get_rag_service()`: singleton — `GeminiService` và `Retriever` (kèm việc dựng chỉ mục BM25) chỉ được tạo ở request đầu tiên rồi tái sử dụng.

### `app/services/agent_service.py`
- `AgentService._run`: vòng lặp viết tay. Mỗi vòng gọi `GeminiService.agent_turn`; nhận lời gọi tool thì chạy bằng `ToolSession`, gửi kết quả lại rồi lặp; nhận text thì stream về. Hai giới hạn: `AGENT_MAX_TOOL_CALLS` đếm lần tìm (hết thì lần tìm tiếp nhận "hết lượt tìm", `get_article` vẫn chạy); `AGENT_MAX_TOTAL_CALLS` đếm mọi lời gọi tool (đủ thì vòng kế tiếp cấm gọi tool).
- Chốt chặn (`AGENT_NO_CONTEXT_GUARD`, mặc định bật): model định trả lời khi request chưa lấy được chunk nào → bỏ câu đó, trả lời bằng `build_no_context_prompt` như pipeline cũ. Model không sinh text → trả lời bằng `build_prompt` trên các chunk đã lấy.
- `ask_with_trace(...)`: trả `reply`, `sources`, `results` (mọi chunk đã lấy), `trace` (mỗi lần gọi tool: tên, tham số, số chunk mới, thời gian, lỗi) và `fallback`. `ask_with_context(...)` cùng dạng với `RagService` để dùng trong `eval_rag.py`.
- `get_agent_service()`: singleton, như `get_rag_service()`.

### `app/services/gemini_service.py`
- `AGENT_INSTRUCTION`: system instruction của agent — khi nào phải / không được gọi tool, cách viết `query`, và các quy tắc an toàn chuyển từ `build_rag_prompt` / `build_no_context_prompt` sang (không nêu liều, 115 ở câu đầu, "không có tài liệu" thì tối đa 3 câu).
- `GeminiService.agent_turn(contents, tools, allow_tools)`: một lần gọi Gemini có khai báo tool, trả từng `Part` (lời gọi tool hoặc text) khi stream về. `allow_tools=False` đặt function calling mode `NONE`.
- `REQUEST_OPTIONS`: mọi lần gọi Gemini có timeout 120 s và chỉ thử lại lỗi tạm thời trong tổng 90 s. Mặc định của SDK là thử lại tới 600 s, nên khi mạng chập một request treo tới 10 phút.
- `SYSTEM_INSTRUCTION`: prompt hệ thống cố định — chỉ dùng thông tin trong tài liệu được đưa kèm, không chẩn đoán cá nhân, không kê đơn, khuyến cáo đi khám / gọi 115 khi có dấu hiệu nguy hiểm. Sửa "cách cư xử" của chatbot ở đây; sửa quy tắc bám context ở `rag/prompt.py`.
- `GeminiService.rewrite_query(message, history)`: gọi Gemini với prompt riêng (`REWRITE_INSTRUCTION`, temperature 0) để viết câu hỏi nối tiếp thành câu độc lập; câu trả lời cũ của bot chỉ lấy 500 ký tự đầu.
- `GeminiService.ask(message, history)`: chuyển `history` sang format Gemini SDK (`{"role", "parts"}`), mở phiên chat với lịch sử đó và gửi prompt RAG làm tin nhắn mới nhất.

### `app/rag/loader.py`
- `load_documents(path)`: đọc JSONL UTF-8 từng dòng, bỏ dòng trống và bản ghi thiếu `id` / `full_text`; dòng JSON hỏng → dừng và báo số dòng.

### `app/rag/chunker.py`
- `chunk_documents(documents)`: mỗi `section` của bài xử lý riêng; section ≤ 2400 ký tự thành 1 chunk, dài hơn thì tách câu và ghép lại, overlap 300 ký tự.
- Text chunk là `"{title}\n{heading}\n{nội dung}"` (`chunk_prefix`); metadata gồm `document_id`, `source`, `title`, `category`, `disease_group`, `section`, `url`, `citation`.

### `app/rag/embeddings.py`
- `embed_texts(texts)`: gửi cả batch trong một request tới OpenAI, trả vector theo đúng thứ tự đầu vào.
- `embed_query(text)`: embed một câu hỏi — phải cùng model với lúc ingest.

### `app/rag/vector_store.py`
- `ChromaVectorStore`: `PersistentClient` tại `VECTOR_DB_PATH`, collection dùng khoảng cách cosine.
- `reset()` xoá và tạo lại collection; `add()` upsert chunk; `search()` trả top-k kèm `id` và `score = 1 − cosine distance`, nhận thêm `where` để lọc theo metadata; `get_all()` đọc toàn bộ chunk để dựng BM25.

### `app/rag/bm25.py`
- `tokenize(text)`: âm tiết thường hoá, bỏ hư từ, thêm bigram âm tiết (`huyết_áp`) để giữ từ ghép tiếng Việt.
- `BM25Index`: inverted index trong RAM, chấm điểm BM25 (`k1 = 1.5`, `b = 0.75`); `search(query, top_n)` trả `[(chunk_id, score)]`.

### `app/rag/retriever.py`
- `Retriever.search(query)`: theo `RETRIEVAL_MODE`:
  - `hybrid` (mặc định): dense top-20 + BM25 top-20 → `reciprocal_rank_fusion` → top-5, hoặc chuyển sang rerank nếu bật.
  - `dense`: chỉ tìm theo vector, lọc từng chunk theo `RAG_MIN_SCORE`.
- Ngưỡng "không có tài liệu phù hợp" áp cho cả câu hỏi (cosine tốt nhất, và điểm rerank tốt nhất khi bật rerank) → trả về rỗng.
- Mỗi chunk trả về có `score` và `scores` (dense, bm25, thứ hạng từng nhánh, rrf, rerank) để debug.
- `Retriever.search(query, disease_group=...)`: chỉ tìm trong một nhóm bệnh (một giá trị của `Retriever.disease_groups`, đọc từ metadata của index).
- `Retriever.search(..., exclude_ids=..., rerank_candidates=...)` (agent dùng): bỏ các chunk đã gửi **trước** khi rerank, và đổi số ứng viên rerank cho lần tìm này. Không truyền thì chạy như cũ.
- `Retriever.section_chunk_ids(document_id, section)`: id các chunk của một mục theo thứ tự — mục dài bị chunker cắt thì có nhiều phần tử. Tool tìm kiếm của agent dùng để ghi "Đoạn k/n của mục".
- `Retriever.get_article(document_id)`: ghép lại cả bài từ các chunk — metadata kèm `sections: [{heading, content}]`, đã bỏ overlap.
- Toàn bộ chunk và chỉ mục BM25 được nạp khi khởi tạo `Retriever` → **chạy lại ingest thì phải khởi động lại server**.

### `app/rag/reranker.py`
- `CrossEncoderReranker.rerank(query, candidates, top_k)`: chấm lại từng cặp (câu hỏi, chunk) rồi giữ top-k.
- `OnnxCrossEncoder`: chạy model bằng ONNX Runtime + `tokenizers` (không dùng PyTorch), tính sigmoid(logit) cho từng cặp.
- `get_reranker()`: nạp model một lần cho cả tiến trình; lần đầu tự tải model (~571 MB) về cache Hugging Face.

### `app/rag/prompt.py`
- `build_context(results)`: mỗi chunk thành khối `[Nguồn i] tiêu đề | Mục: …` kèm nội dung và URL. `i` đánh theo **bài**, không theo chunk (chunk cùng bài chung số), cùng thứ tự với `sources` trả về frontend — nên "[Nguồn i]" trong câu trả lời khớp với nguồn thứ i hiển thị; không có chunk thì ghi "Không tìm thấy tài liệu phù hợp…".
- `build_rag_prompt(question, context)`: quy tắc chỉ trả lời dựa trên context, không bịa, không kê đơn, không nêu liều thuốc (hàm lượng, số lần/ngày, thời gian dùng) kể cả khi context có ghi, gặp dấu hiệu cấp cứu thì khuyên gọi 115.
- `build_no_context_prompt(question)`: prompt khi retrieve không ra chunk nào — tối đa 3 câu, nói chưa có thông tin (hoặc từ chối câu ngoài lĩnh vực sức khỏe, đáp lời chào), khuyên đi khám, cấm nêu triệu chứng, con số, thuốc, điều trị, tiên lượng. Câu cấp cứu có nhánh riêng: câu đầu khuyên gọi 115, không nói "chưa có thông tin trong dữ liệu".
- `build_prompt(question, results)`: chọn một trong hai prompt trên theo `results`; `RagService` gọi hàm này.
- `format_chunk(result, index, with_document_id)`: một khối chunk; tool tìm kiếm của agent dùng bản kèm `document_id`.

### `app/rag/guardrail.py`
- `StreamRedactor`: `feed(đoạn)` trả phần đã an toàn để gửi, `flush()` phần còn lại; giữ lại 80 ký tự cuối chưa gửi nên mọi cụm liều đều bị che trước khi tới frontend. Cụm khớp nằm trong 12 ký tự cuối chưa che khi đang stream ("6 mg" có thể là "6 mg/dL").
- Che: hàm lượng (số + mg / mcg / IU / viên / nhát / giọt…, không theo sau bởi "/"), số lần dùng thuốc ("2 lần mỗi ngày") khi gần đó có từ chỉ thuốc. Không che: g / ml, chỉ số xét nghiệm, khẩu phần.
- `AgentService._run` bọc mọi `delta` qua guardrail khi `DOSE_GUARDRAIL=true`; cụm bị che ghi vào `dose_redactions`.

### `app/rag/tools.py`
- `tool_declarations(disease_groups)`: khai báo `search_medical_docs(query, disease_group?)` và `get_article(document_id, section?)` cho Gemini; `enum` nhóm bệnh lấy từ `Retriever.disease_groups`.
- `ToolSession`: trạng thái tool của một request — đánh số `[Nguồn i]` xuyên suốt request (bài đã gặp dùng lại số cũ), không trả lại chunk đã trả, `get_article` chỉ nhận `document_id` đã xuất hiện trong kết quả tìm kiếm, cắt bài ở 8.000 ký tự. Chunk thuộc mục bị cắt được ghi "Đoạn k/n của mục"; mục chưa có đủ đoạn thì kết quả kèm gợi ý gọi `get_article` (vết chạy ghi `partial_sections`). Tham số sai trả về thành text để model tự sửa. Cuối mọi kết quả `search_medical_docs` / `get_article` có `DOSE_REMINDER` (nhắc không nêu liều thuốc), vì chỉ để quy tắc trong system instruction thì agent vẫn nêu liều khi tài liệu có ghi. `results` giữ mọi chunk đã trả theo thứ tự đánh số, nên `collect_sources(results)` khớp với số trong câu trả lời.

### `app/scripts/ingest.py`
- Load → chunk → embed theo batch 32 → reset collection và lưu vào Chroma, in tiến độ từng batch.

### `eval/`
- Bộ golden test và hai script đánh giá — cách chạy và kết quả: [../docs/RAG_EVAL.md](../docs/RAG_EVAL.md).
- `eval_rag.py --agent`: chạy `AgentService`, lưu vết chạy và chỉ số hành vi gọi tool; `--resume` chạy tiếp từ `<out>.partial.jsonl` khi bị ngắt.

## Vì sao tách theo layer như vậy?

- `schemas.py` là nơi duy nhất định nghĩa "hình dạng dữ liệu" — router và service cùng import, tránh lệch nhau.
- `routers/` chỉ lo HTTP (nhận request, validate, trả response, map lỗi → status code); logic nghiệp vụ nằm trong `services/`.
- `services/rag_service.py` chỉ điều phối; từng bước RAG nằm trong `rag/` và thay được độc lập — vd đổi model embedding chỉ sửa `embeddings.py`, đổi cách tìm kiếm chỉ sửa `retriever.py`, đổi LLM chỉ sửa `gemini_service.py`.
