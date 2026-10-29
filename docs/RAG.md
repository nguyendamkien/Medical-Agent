# Pipeline RAG — ingest, retrieval và model sử dụng

Tài liệu mô tả pipeline RAG của backend: dữ liệu được đưa vào vector DB như thế nào (ingest), mỗi câu hỏi được tìm tài liệu ra sao (retrieval: hybrid BM25 + dense, rerank cross-encoder) và được trả lời thế nào (generation). Cách đo chất lượng: xem [RAG_EVAL.md](RAG_EVAL.md).

**Cấu hình đang chạy:** embedding `text-embedding-3-small` (OpenAI) · retrieval **hybrid** BM25 + dense gộp bằng RRF · **rerank bật** bằng `bge-reranker-v2-m3` với 10 ứng viên (~11–12 s/câu trên CPU; mặc định bật, tắt bằng `RERANK_ENABLED=false`) · sinh câu trả lời bằng Gemini · dữ liệu 10 nhóm bệnh (100 bài, 505 chunk).

## Model sử dụng

| Vai trò | Model | Nhà cung cấp | Cấu hình (`backend/.env`) |
|---|---|---|---|
| Embedding (tài liệu + câu hỏi) | `text-embedding-3-small` — vector 1536 chiều | OpenAI | `OPENAI_API_KEY`, `EMBEDDING_MODEL` |
| Sinh câu trả lời | Gemini (mặc định `gemini-3.1-flash-lite`, ghi đè bằng `.env`) | Google | `GEMINI_API_KEY`, `GEMINI_MODEL` |
| Vector DB | ChromaDB (lưu trên đĩa), khoảng cách cosine | — | `VECTOR_DB_PATH`, `VECTOR_COLLECTION` |
| Tìm kiếm từ khoá | BM25 tự cài đặt, chỉ mục trong RAM (`app/rag/bm25.py`) | — | `RETRIEVAL_MODE`, `RAG_CANDIDATES` |
| Rerank (mặc định bật) | Cross-encoder `BAAI/bge-reranker-v2-m3` — chạy bản ONNX int8 | BAAI, chạy local bằng ONNX Runtime | `RERANK_ENABLED`, `RERANK_CANDIDATES`, `RERANK_MIN_SCORE` |

**Lưu ý quan trọng:** tài liệu và câu hỏi phải được embed bằng **cùng một model**. Khi đổi `EMBEDDING_MODEL` (hoặc đổi sang nhà cung cấp khác) bắt buộc chạy lại ingest, nếu không Chroma sẽ báo lỗi lệch số chiều vector hoặc trả kết quả sai.

### Vì sao chọn `text-embedding-3-small`

- Hỗ trợ tiếng Việt đủ tốt cho truy vấn y tế phổ thông.
- Rẻ nhất trong các model embedding của OpenAI: **$0.02 / 1M token** ($0.01 nếu dùng Batch API).
- Nếu chất lượng tìm kiếm chưa đạt, có thể chuyển sang `text-embedding-3-large` ($0.13 / 1M token, 3072 chiều, có thể truyền `dimensions=1024` để giảm dung lượng).

### Chi phí embedding (đo bằng tokenizer `cl100k_base`)

| File dữ liệu | Số bài | Số chunk | Token | `3-small` | `3-large` |
|---|---|---|---|---|---|
| `benh_pho_bien_10.jsonl` (đang dùng) | 100 | 505 | ~0,30M | ~$0.006 | ~$0.04 |
| `combined.jsonl` | 2.306 | 12.300 | ~7,1M | ~$0.14 | ~$0.92 |

Chi phí embed câu hỏi khi chat không đáng kể (~30–100 token/câu).

## Dữ liệu đầu vào

File được chọn bởi `DATA_PATH` (mặc định `../data-collection/data/benh_pho_bien_10.jsonl`, đường dẫn tính từ thư mục `backend/`).

Hiện tại dữ liệu được **giới hạn trong 10 nhóm bệnh phổ biến**:

| Nhóm bệnh | Số bài | Nhóm bệnh | Số bài |
|---|---|---|---|
| Tăng huyết áp | 17 | Viêm da cơ địa | 10 |
| Viêm loét dạ dày | 15 | Hen phế quản | 7 |
| Gout | 14 | Viêm khớp | 5 |
| Viêm gan B | 13 | Suy tim | 4 |
| Suy thận | 12 | Sỏi thận | 3 |

Nguồn: suckhoedoisong (54), medlatec (23), vinmec (23).

Mỗi dòng JSONL là một bài viết:

```json
{
  "id": "...", "title": "...", "url": "...", "source": "vinmec",
  "category": "...", "disease_group": "Gout (Gút)", "published_date": "...",
  "full_text": "...",
  "sections": [{"heading": "Triệu chứng bệnh Gout", "content": "..."}]
}
```

Để dùng toàn bộ dữ liệu, đổi `DATA_PATH=../data-collection/data/combined.jsonl` rồi chạy lại ingest.

## Quá trình ingest

Chạy **một lần**, hoặc mỗi khi đổi dữ liệu / đổi model embedding:

```powershell
cd backend
python -m app.scripts.ingest
```

```
 JSONL ──► loader ──► chunker ──► embeddings (OpenAI) ──► vector_store (Chroma)
           đọc bài     chia chunk   batch 32 chunk/request    upsert + metadata
```

### 1. Load — `app/rag/loader.py`

- Đọc file JSONL (UTF-8) từng dòng.
- Bỏ qua dòng trống và bản ghi thiếu `id` hoặc `full_text`.
- Dòng JSON hỏng → dừng và báo số dòng lỗi.

### 2. Chunk — `app/rag/chunker.py`

Chia theo section, rồi cắt tiếp theo câu:

1. Mỗi `section` của bài được xử lý riêng, nên nội dung "Triệu chứng" và "Điều trị" không bị trộn vào một chunk. Bài không có `sections` thì dùng `full_text` với heading "Nội dung".
2. Section **≤ 2400 ký tự** → giữ nguyên thành 1 chunk.
3. Section dài hơn → tách câu theo `. ! ? ; :`, ghép dần tới gần 2400 ký tự thì đóng chunk. Chunk sau lấy **300 ký tự cuối** của chunk trước làm overlap. Không tách được câu thì cắt theo khoảng trắng.
4. Text của chunk: `"{title}\n{heading}\n{nội dung}"` (`chunk_prefix`). Tên bài nằm trong text vì heading thường chung chung ("Tóm tắt", "Điều trị"): thiếu tên bệnh thì chunk của các bệnh gần nhau dễ bị lấy nhầm. Giới hạn 2400 ký tự chỉ tính phần nội dung. ID chunk: `{document_id}-{số thứ tự}`.
5. Metadata đi kèm: `document_id`, `source`, `title`, `category`, `disease_group`, `section`, `url`, `citation`.

Với file 10 bệnh, mỗi chunk trung bình ~625 token; chunk lớn nhất ~1.340 token, dưới giới hạn 8.191 token của OpenAI.

### 3. Embed — `app/rag/embeddings.py`

- `embed_texts(texts)`: mỗi batch 32 chunk được gửi trong **một request** tới `client.embeddings.create`. Kết quả được sắp xếp theo `index` để giữ đúng thứ tự.
- OpenAI client được tạo một lần (`lru_cache`), tự retry tối đa 2 lần khi lỗi mạng hoặc bị rate limit.

### 4. Lưu — `app/rag/vector_store.py`

- Chroma `PersistentClient` lưu tại `VECTOR_DB_PATH` (mặc định `backend/app/storage/chroma`), collection `VECTOR_COLLECTION` với `hnsw:space = cosine`.
- Đầu mỗi lần ingest, `store.reset()` **xoá toàn bộ collection** rồi tạo lại, nên index luôn khớp đúng với file trong `DATA_PATH`.
- Chunk được ghi bằng `upsert` (id, text, embedding, metadata).
- Chỉ mục BM25 không lưu trên đĩa: nó được dựng lại từ các chunk trong Chroma mỗi khi server khởi động. **Sau khi chạy lại ingest, khởi động lại server** để BM25 khớp với index mới.

## Quá trình trả lời câu hỏi (query)

```
POST /api/chat ──► cắt history ──► [viết lại câu hỏi] ──► Retriever.search ──► build_prompt ──► Gemini ──► { reply, sources }
                                   (chỉ khi có history)     (hybrid + [rerank])
```

0. **Lấy lịch sử** (`app/routers/chat.py`): từ 29/10/2026 client không gửi `history` nữa. Router đọc tối đa `2 × HISTORY_MAX_TURNS` tin nhắn gần nhất của hội thoại từ PostgreSQL rồi truyền vào service. Xem [AUTH_CHAT_HISTORY.md](AUTH_CHAT_HISTORY.md).
1. **Cắt lịch sử** (`trim_history` trong `app/services/rag_service.py`): chỉ giữ `HISTORY_MAX_TURNS` lượt hỏi–đáp gần nhất (mặc định 10), bỏ tin nhắn bot đứng đầu (lời chào) để lịch sử luôn bắt đầu bằng câu của người dùng.
2. **Viết lại câu hỏi** (`QUERY_REWRITE_ENABLED`, mặc định bật; `GeminiService.rewrite_query`): khi đã có lịch sử, Gemini (temperature 0, prompt riêng) viết câu hỏi nối tiếp thành câu độc lập — "Vậy điều trị thế nào?" → "Điều trị bệnh gout như thế nào?". Câu viết lại **chỉ dùng để retrieve**; bước sinh câu trả lời vẫn nhận câu gốc + lịch sử. Gọi Gemini lỗi, kết quả rỗng hoặc dài bất thường (> 300 ký tự) → dùng câu gốc. Câu đầu tiên của cuộc trò chuyện không tốn thêm lần gọi nào.
3. **Retrieve** các chunk liên quan bằng hybrid search (chi tiết ở mục dưới) — `app/rag/retriever.py`.
4. **Dựng prompt** (`app/rag/prompt.py`): mỗi chunk thành một khối `[Nguồn i] tiêu đề | Mục: ...` (`i` đánh theo bài, chunk cùng bài chung số), kèm quy tắc chỉ trả lời dựa trên context, không chẩn đoán/kê đơn, khuyên gọi 115 khi cấp cứu. Không có chunk nào → dùng prompt riêng (`build_no_context_prompt`): tối đa 3 câu, nói chưa có thông tin / từ chối câu ngoài lĩnh vực sức khỏe, khuyên đi khám hoặc gọi 115, **cấm nêu** triệu chứng, nguyên nhân, con số, thuốc, điều trị, tiên lượng — với prompt chung, Gemini nói "chưa tìm thấy" nhưng vẫn kể thêm kiến thức của nó.
5. **Sinh câu trả lời** bằng Gemini (`app/services/gemini_service.py`), có kèm lịch sử hội thoại và system instruction về an toàn y tế.
6. **Trả về** câu trả lời và danh sách nguồn (title, url), mỗi bài một nguồn theo đúng thứ tự `[Nguồn i]`.

## Retrieval: hybrid BM25 + dense

```
câu hỏi ─┬─► Dense: embed → Chroma (cosine) ─────► top-20 ─┐
         │                                                  ├─► RRF ─► [rerank cross-encoder] ─► top-5
         └─► BM25: tách token → chỉ mục trong RAM ─► top-20 ┘
```

Hai kiểu tìm kiếm bù điểm yếu cho nhau:

| Nhánh | Cách chấm | Mạnh | Yếu |
|---|---|---|---|
| **Dense** (ngữ nghĩa) | Cosine giữa embedding câu hỏi và chunk (`text-embedding-3-small`, Chroma HNSW) | Hiểu đồng nghĩa, diễn đạt khác: "cao huyết áp" ≈ "tăng huyết áp" | Bỏ lỡ thuật ngữ hiếm, mã xét nghiệm, tên thuốc: "tophi", "HBsAg" |
| **BM25** (từ khoá) | Đếm từ trùng, trọng số theo độ hiếm (IDF), bão hoà tần suất (`k1 = 1.5`), chuẩn hoá độ dài chunk (`b = 0.75`) | Bắt chính xác thuật ngữ hiếm | Không hiểu đồng nghĩa |

### Nhánh BM25 — `app/rag/bm25.py`

- **Tách token tiếng Việt** không cần bộ tách từ: lấy âm tiết thường hoá, bỏ hư từ ("là", "của", "thì", "gì"…), rồi thêm **bigram âm tiết** (`huyết_áp`, `suy_thận`). Bigram giữ được từ ghép, giảm khớp nhầm kiểu "áp" trong "áp xe" với "huyết áp".
- Chỉ mục dựng **trong RAM** từ chính các chunk trong Chroma khi khởi tạo `Retriever` (505 chunk: dưới 1 giây), dùng inverted index nên chỉ duyệt chunk có chứa từ của câu hỏi.
- **Sau khi chạy lại ingest phải khởi động lại server** để dựng lại chỉ mục BM25.

### Gộp kết quả — Reciprocal Rank Fusion (RRF)

Điểm BM25 (vd 12.7) và cosine (vd 0.68) nằm trên hai thang khác nhau nên không cộng thẳng được. RRF chỉ dùng **thứ hạng** của chunk trong từng nhánh:

```
rrf(chunk) = Σ  1 / (RRF_K + hạng của chunk trong nhánh đó)      RRF_K = 60, hạng bắt đầu từ 1
```

- Mỗi nhánh lấy `RAG_CANDIDATES` = 20 ứng viên; chunk vắng mặt ở một nhánh thì nhánh đó đóng góp 0.
- Chunk xếp cao ở **cả hai** nhánh được điểm cao nhất; chunk chỉ có ở một nhánh vẫn có cơ hội lọt top — nhờ vậy thuật ngữ hiếm mà dense bỏ lỡ vẫn được lấy.
- `RRF_K = 60` là giá trị chuẩn: làm phẳng chênh lệch giữa hạng 1 và hạng 2 để một nhánh không lấn át hoàn toàn nhánh kia.
- Ví dụ: chunk hạng 1 ở BM25 và hạng 9 ở dense có `1/61 + 1/69 = 0.0309`, cao hơn chunk hạng 1 ở dense nhưng vắng mặt ở BM25 (`1/61 = 0.0164`).

Kết quả sau khi gộp được sắp theo điểm RRF và lấy `RAG_TOP_K` = 5 chunk (hoặc chuyển sang rerank nếu bật).

### Lọc theo nhóm bệnh và đọc cả bài

Hai khả năng của `Retriever` chưa được pipeline chat dùng tới; chúng là nền cho các tool của agent ([AGENTIC_RAG.md](AGENTIC_RAG.md)):

- `Retriever.search(query, disease_group="Suy thận")` chỉ tìm trong một nhóm bệnh. Nhánh dense lọc bằng `where` của Chroma; nhánh BM25 chấm điểm toàn bộ rồi bỏ chunk khác nhóm, nên cả hai nhánh vẫn lấy đủ `RAG_CANDIDATES` ứng viên trong nhóm. Giá trị phải thuộc `Retriever.disease_groups` (đọc từ metadata của index lúc khởi động, không viết cứng); giá trị khác → `ValueError`.
- `Retriever.get_article(document_id)` ghép lại một bài từ các chunk của nó: metadata của bài kèm `sections: [{heading, content}]` theo đúng thứ tự, đã bỏ phần overlap giữa các chunk và tên bài / heading gắn ở đầu chunk. Không cần embedding hay rerank. Đã đối chiếu với file dữ liệu: 100/100 bài ghép lại khớp nguyên văn (sau khi gộp khoảng trắng).

### Ngưỡng "không có tài liệu phù hợp"

Áp cho **cả câu hỏi**, không cho từng chunk (chunk chỉ đến từ BM25 không có điểm cosine). Trả về rỗng → dùng prompt "không có tài liệu" (`build_no_context_prompt`).

- Luôn kiểm tra: cosine tốt nhất của nhánh dense < `RAG_MIN_SCORE` (0.25).
- Khi bật rerank, thêm: điểm rerank tốt nhất < `RERANK_MIN_SCORE` (0.4). Ngưỡng này hiệu quả hơn hẳn — xem bảng ở mục rerank.

### Rerank bằng cross-encoder — `app/rag/reranker.py`

Bi-encoder và BM25 đều không "đọc" câu hỏi cùng với chunk. Cross-encoder ghép `[câu hỏi] + [chunk]` thành **một** đầu vào cho transformer, nên attention thấy được tương tác giữa từng từ của hai bên và cho điểm liên quan chính xác hơn — đổi lại phải chạy model cho từng cặp, nên chỉ dùng để chấm lại `RERANK_CANDIDATES` ứng viên tốt nhất sau RRF (hiện đặt 10) rồi giữ top-5.

**Model: `BAAI/bge-reranker-v2-m3`** (cross-encoder đa ngôn ngữ, có tiếng Việt, ~568M tham số), chạy bản ONNX `onnx-community/bge-reranker-v2-m3-ONNX` — file `onnx/model_int8.onnx` (lượng tử hoá int8, ~571 MB). Điểm = sigmoid(logit), 0–1. Có thể đổi `RERANK_ONNX_FILE=onnx/model.onnx` để dùng bản fp32 (~2.3 GB) của cùng model.

- Chạy bằng **ONNX Runtime, không dùng PyTorch**; các thư viện cần (`onnxruntime`, `tokenizers`, `huggingface_hub`, `numpy`) đã có sẵn nhờ `chromadb`. Model tự tải lần đầu vào cache Hugging Face.
- **Vì sao không dùng PyTorch:** trên máy phát triển, **Smart App Control** của Windows 11 đang bật (chế độ Enforce) và chặn `torch.dll`. Nhật ký `Microsoft-Windows-CodeIntegrity/Operational` ghi sự kiện 3033/3077: *"did not meet the Enterprise signing level requirements"*, policy `{0283ac0f-fff1-49ae-ada1-8a933130cad6}`. Smart App Control chỉ cho nạp file thực thi/DLL có chữ ký số tin cậy hoặc có uy tín trên dịch vụ đám mây của Microsoft; các DLL trong wheel PyTorch không đáp ứng, còn DLL của `onnxruntime` do Microsoft ký nên chạy bình thường. Máy không có GPU NVIDIA nên PyTorch cũng không nhanh hơn ONNX Runtime ở đây.
- Mỗi chunk trả về có `score` (điểm xếp hạng cuối) và `scores` gồm `dense`, `dense_rank`, `bm25`, `bm25_rank`, `rrf`, `rerank` để debug.
- Mặc định bật (`RERANK_ENABLED=true`, `RERANK_CANDIDATES=10`) — mọi số đo chất lượng đều dùng cấu hình này. Đặt `RERANK_ENABLED=false` nếu cần trả lời nhanh trên CPU yếu.

Điểm rerank tách "có tài liệu / không có tài liệu" tốt hơn cosine nhiều (đo trên golden set):

| | Cosine (dense) | Điểm rerank |
|---|---|---|
| Câu có đáp án đúng ở top-1 — điểm thấp nhất | 0.48 | **0.73** |
| Câu ngoài phạm vi — điểm cao nhất | 0.52 (chồng lên) | **0.17** |

Tốc độ đo trên CPU 8 nhân, không GPU (số luồng ONNX Runtime mặc định đã tối ưu, batch không giúp thêm — ~0.8 s cho mỗi cặp 512 token):

| `RERANK_CANDIDATES` | Độ dài tối đa | Thời gian / câu hỏi |
|---|---|---|
| 20 | 1024 token | ~24 s |
| 10 | 1024 token | ~11.5 s |
| 10 | 512 token | ~8.6 s |

Top-1 giống nhau ở cả 4 cấu hình thử (4 câu hỏi). Đang chọn **10 ứng viên × 1024 token (~11.5 s/câu)** để nằm trong ngân sách ~15 s.

### Hiệu quả đo được

Trên golden set 70 câu ([RAG_EVAL.md](RAG_EVAL.md)), top-5:

| Retrieval | hit@1 | hit@3 | hit@5 | MRR | Câu trượt khỏi top-5 |
|---|---|---|---|---|---|
| Dense | 0.84 | 0.88 | 0.92 | 0.87 | 5 |
| Hybrid (RRF) | 0.84 | 0.95 | 0.95 | 0.90 | 3 |
| Hybrid + rerank (20 ứng viên) | 0.91 | 0.94 | 0.97 | 0.93 | 2 |
| Hybrid + rerank (10 ứng viên) — **đang dùng** | **0.91** | **0.97** | **0.98** | **0.94** | **1** |

Thêm về hybrid + rerank: section_hit 0.97 → 1.00, disease_precision 0.88 → 0.91; factual hit@1 0.91 → 0.97. **10 ứng viên không kém 20 ứng viên** (lệch 1–2 câu, trong mức ngẫu nhiên) mà nhanh gấp đôi. Với `RERANK_MIN_SCORE = 0.4`, cả 5/5 câu ngoài phạm vi bị chặn ngay ở retrieval (không có chunk nào vào prompt), không câu có tài liệu nào bị chặn nhầm.

Đánh giá end-to-end theo 4 tiêu chí RAGAS với cấu hình đang dùng (30/09/2026): context_precision **0.94**, context_recall **0.94**, faithfulness **0.98**, answer_relevancy **0.99** (so với dense: 0.81 / 0.90 / 0.98 / 0.96). Chi tiết: [RAG_EVAL.md](RAG_EVAL.md).

Hybrid cứu được các câu dense bỏ lỡ vì thuật ngữ hiếm (`gout-08` — "nốt tophi") hoặc cần nhiều bài (`multi-03` — viêm da cơ địa và hen suyễn). Các câu còn trượt chủ yếu là **câu hỏi nối tiếp** ("Vậy điều trị thế nào?") — retrieval chỉ nhìn câu hỏi hiện tại, cần viết lại câu hỏi dựa trên lịch sử chat mới sửa được.

**Sau khi đưa tên bài vào text chunk (13/10/2026):** hybrid không rerank tăng hit@1 0.84 → 0.88; hybrid + rerank 10 ứng viên đạt hit@1 0.91, hit@3 0.95, hit@5 0.95, MRR 0.93. Phần giảm so với bảng trên nằm trọn ở 2 câu follow-up retrieve bằng câu nguyên văn (không có tên bệnh); với câu đã viết lại — đường hệ thống thực sự chạy — kết quả vẫn 1.00. Điểm rerank tách câu có đáp án (≥ 0.72) và câu ngoài phạm vi (≤ 0.08) rõ hơn trước. Chi tiết: [RAG_EVAL.md](RAG_EVAL.md).

`RETRIEVAL_MODE=dense` quay về cách cũ (chỉ dense, lọc từng chunk theo `RAG_MIN_SCORE`).

## Cấu hình liên quan (`backend/.env`)

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `OPENAI_API_KEY` | — | Key gọi OpenAI embedding |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Model embedding |
| `GEMINI_API_KEY` | — | Key gọi Gemini |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Model sinh câu trả lời và viết lại câu hỏi |
| `DATA_PATH` | `../data-collection/data/benh_pho_bien_10.jsonl` | File dữ liệu để ingest |
| `VECTOR_DB_PATH` | `app/storage/chroma` | Thư mục lưu Chroma |
| `VECTOR_COLLECTION` | `medical_documents` | Tên collection |
| `RAG_TOP_K` | `5` | Số chunk đưa vào prompt mỗi câu hỏi |
| `RAG_MIN_SCORE` | `0.25` | Cosine tốt nhất tối thiểu; thấp hơn thì coi như không có tài liệu phù hợp |
| `RETRIEVAL_MODE` | `hybrid` | `hybrid` (BM25 + dense, RRF) hoặc `dense` |
| `RAG_CANDIDATES` | `20` | Số ứng viên mỗi nhánh trước khi gộp / rerank |
| `RRF_K` | `60` | Hằng số làm phẳng trong RRF |
| `RERANK_ENABLED` | `true` | Bật rerank cross-encoder |
| `RERANK_ONNX_REPO` / `RERANK_ONNX_FILE` | `onnx-community/bge-reranker-v2-m3-ONNX` / `onnx/model_int8.onnx` | Model ONNX; `onnx/model.onnx` là bản fp32 (~2.3 GB) |
| `RERANK_CANDIDATES` | `10` | Số ứng viên RRF đưa vào cross-encoder |
| `RERANK_MIN_SCORE` | `0.4` | Điểm rerank tốt nhất tối thiểu, thấp hơn thì coi như không có tài liệu |

## Đánh giá

Xem [RAG_EVAL.md](RAG_EVAL.md): bộ golden test 70 câu, script đo retrieval (`eval/eval_retrieval.py`, so được dense / hybrid / rerank) và script chấm end-to-end theo tiêu chí RAG bằng LLM giám khảo (`eval/eval_rag.py`).

## Hạn chế đã biết

- **Khi tắt rerank, `RAG_MIN_SCORE = 0.25` gần như không lọc được gì**: cosine của câu ngoài phạm vi (tới 0.52) chồng lên câu có đáp án (từ 0.48), việc từ chối dựa vào prompt. Bật rerank thì `RERANK_MIN_SCORE` giải quyết được, nhưng rerank chậm trên CPU.
- **Câu hỏi nối tiếp** phụ thuộc vào bước viết lại câu hỏi: tắt `QUERY_REWRITE_ENABLED` thì retrieval chỉ dùng câu hỏi hiện tại và lấy sai tài liệu. Bật thì mỗi câu hỏi nối tiếp tốn thêm 1 lần gọi Gemini.
- **Lịch sử chỉ lưu ở trình duyệt** và bị cắt còn `HISTORY_MAX_TURNS` lượt — chatbot không nhớ thông tin từ các lượt cũ hơn hay từ cuộc trò chuyện khác.
- **Đường dẫn tương đối** (`DATA_PATH`, `VECTOR_DB_PATH`) chỉ đúng khi chạy lệnh từ thư mục `backend/`.
- **`reset()` chạy trước khi embed**: nếu ingest lỗi giữa chừng, index sẽ trống hoặc thiếu cho tới khi chạy lại thành công.
- Bước chunk gộp mọi khoảng trắng/xuống dòng trước khi cắt, nên cấu trúc đoạn văn và danh sách không được giữ.
- **Rerank chậm trên CPU**: với cấu hình đang dùng (10 ứng viên) mỗi câu hỏi mất thêm ~11–12 s, đổi lại chất lượng retrieval tốt hơn rõ (với 20 ứng viên: hit@1 0.84 → 0.91).
