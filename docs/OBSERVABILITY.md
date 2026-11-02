# Xem trace bằng Langfuse

> Cập nhật 02/11/2026. Dùng **Langfuse Cloud** (máy dev không đủ RAM để tự host Langfuse v3, vốn cần thêm ClickHouse, Redis, MinIO và một Postgres riêng). SDK `langfuse==5.1.0`.

Mỗi câu hỏi thành một **trace** trên Langfuse: Gemini được gọi mấy lần, agent gọi tool nào với tham số gì, mỗi bước mất bao lâu, tốn bao nhiêu token. Eval cũng ghi trace, kèm điểm chấm, nên câu nào trượt thì mở đúng trace câu đó để xem agent đã làm gì.

## Cài đặt (một lần)

1. Đăng ký tại **https://cloud.langfuse.com** (vùng EU). Tạo organization, rồi tạo project.
2. Vào **Settings → API Keys → Create new API keys**, chép **Public Key** (`pk-lf-…`) và **Secret Key** (`sk-lf-…`).
3. Thêm vào `backend/.env`:
   ```
   LANGFUSE_ENABLED=true
   LANGFUSE_PUBLIC_KEY=pk-lf-...
   LANGFUSE_SECRET_KEY=sk-lf-...
   LANGFUSE_BASE_URL=https://cloud.langfuse.com   # project ở vùng US: https://us.cloud.langfuse.com
   LANGFUSE_MASK_CONTENT=true
   ```
4. `python -m pip install -r requirements.txt` (thêm thư viện `langfuse`), rồi khởi động lại backend.
5. Chat thử một câu, mở project trên Langfuse → **Tracing**. Trace xuất hiện sau vài giây (SDK gửi nền theo lô).

Không bật (`LANGFUSE_ENABLED=false`, mặc định), thiếu key hoặc chưa cài thư viện thì mọi thứ thành no-op: app và eval chạy như cũ, không gọi ra mạng. Lỗi của Langfuse (mất mạng, key sai) chỉ ghi log cảnh báo, không bao giờ làm hỏng câu trả lời.

## Một trace gồm gì

```
chat                                   user_id = id user (UUID, không dùng email)
│                                      session_id = id hội thoại → gom các lượt của một cuộc trò chuyện
│                                      tags: chat, agent | rag, has_profile
│                                      metadata: pipeline, fallback, n_tool_calls, n_searches,
│                                                n_chunks, n_facilities, n_dose_redactions
├─ gemini.agent_turn       [generation]  model, số token, turn, allow_tools, has_profile
├─ tool.search_medical_docs [tool]       disease_group, n_chunks, seconds, partial_sections, allergy_hits
├─ tool.get_article         [tool]       document_id, section, seconds
├─ tool.find_healthcare_facility [tool]  specialty, emergency, for_child, n_facilities
├─ gemini.agent_turn       [generation]  câu trả lời
├─ gemini.fallback         [generation]  chỉ khi chốt chặn / model không trả lời; metadata reason
└─ guardrail.dose          [guardrail]   chỉ khi guardrail che liều thuốc (level WARNING)
```

Pipeline cũ (`AGENT_ENABLED=false`): `gemini.rewrite_query` → `retrieve` (n_chunks, top_score) → `gemini.answer`.

**Mức cảnh báo (level) để lọc nhanh trên Langfuse:**

| Level | Khi nào |
|---|---|
| `WARNING` | Chốt chặn "không có tài liệu" chặn câu trả lời của model; tool hết lượt; tool trả lỗi (vd `document_id` lạ); guardrail che liều; người dùng ngắt kết nối trước khi trả lời xong |
| `ERROR` | Gọi Gemini thất bại; trả lời xong nhưng không lưu được vào DB |

Ví dụ một câu "gout nên ăn gì và khám ở đâu" (đo khi kiểm tra, 02/11/2026): 2 vòng Gemini (~2,6 s và ~3,0 s; 2.107 và 6.884 token), `search_medical_docs` 31,5 s, `find_healthcare_facility` 2 ms. Trace cho thấy ngay thời gian nằm gần hết ở bước tìm (lần đầu sau khi khởi động phải nạp reranker).

## Che nội dung (`LANGFUSE_MASK_CONTENT`)

Trace gửi lên máy chủ của Langfuse. Câu hỏi, hồ sơ sức khỏe (nằm trong system instruction), đoạn tài liệu và câu trả lời là **dữ liệu sức khỏe**, nên:

| | `true` (mặc định) | `false` |
|---|---|---|
| Gửi lên | Tên bước, tool, thời gian, token, số đếm; tham số không phải nội dung (`disease_group`, `specialty`, `emergency`, `for_child`, `document_id`, `section`); level / lỗi | Thêm input / output đầy đủ: câu hỏi, `query` agent tự viết, đoạn tài liệu trả về, câu trả lời |
| Input / output | `[ẩn — LANGFUSE_MASK_CONTENT=true]` | Nội dung thật (kết quả tool dài cắt ở 2.000 ký tự) |

- **Trace của eval luôn gửi đủ nội dung**, bất kể cài đặt này: câu hỏi trong golden set là câu mẫu, không phải dữ liệu người dùng thật.
- Chỉ đặt `false` khi dev với tài khoản thử và câu hỏi mẫu. **Không bật `false` khi có người dùng thật.**
- `user_id` là UUID, không gửi email hay tên.

## Eval ghi trace

`python -m eval.eval_rag --agent --types facility --workers 1` khi Langfuse đang bật:

- Mỗi câu là một trace tên `eval <id>` (vd `eval safety-04`), tags `eval`, `agent` / `rag`, loại câu.
- Cả lần chạy chung một session: `eval-agent-20261102-0930`.
- Các chỉ số gắn thành **score** của trace: `context_precision`, `context_recall`, `faithfulness`, `answer_relevancy`, `profile_ok`, `facility_ok`, `latency_s`.

Trên Langfuse: lọc tag `eval`, sắp theo score (vd `context_recall` = 0), mở trace để xem truy vấn agent tự viết, tool trả về gì, vòng nào bị chốt chặn.

## Code nằm ở đâu

| File | Vai trò |
|---|---|
| `app/observability.py` | Lớp bọc SDK: `start_trace()`, `Observation.child()` / `update()` / `end()` / `score_trace()`, `flush()`, `NOOP`. Che nội dung, bắt mọi lỗi của SDK |
| `app/routers/chat.py` | Mở trace gốc `chat` cho `/api/chat` và `/api/chat/stream`, đóng trong `finally` (kể cả khi lỗi hay người dùng ngắt) |
| `app/services/agent_service.py` | `gemini.agent_turn`, `tool.*`, `gemini.fallback`, `guardrail.dose`, metadata tóm tắt |
| `app/services/rag_service.py` | `gemini.rewrite_query`, `retrieve`, `gemini.answer` |
| `app/services/gemini_service.py` | `record_usage()`: lấy số token từ `usage_metadata` của Gemini |
| `eval/eval_rag.py` | Trace `eval <id>`, score, `flush()` cuối lần chạy |

**Vì sao tạo observation bằng tay** (`start_observation`) thay cho decorator `@observe` của SDK: câu trả lời được stream qua generator, Starlette chạy từng bước của generator trong threadpool, nên "context hiện tại" của OpenTelemetry không theo kịp. Mỗi `Observation` giữ chính nó và tạo con trực tiếp từ nó. Thuộc tính cấp trace (`user_id`, `session_id`, tags) được gắn lại mỗi lần tạo con bằng `propagate_attributes`, vì SDK v5 chỉ tự gắn cho các bước tạo **bên trong** khối đó. Đã kiểm tra bằng exporter ghi vào bộ nhớ: mọi bước con đều có `user.id`, `session.id`; cập nhật metadata nhiều lần được gộp, không ghi đè.

Thêm một bước mới vào trace:

```python
span = trace.child("ten_buoc", as_type="span", input=noi_dung, metadata={"so_lieu": 3})
try:
    ...
    span.update(output=ket_qua)
finally:
    span.end()
```

Chỉ đưa vào `metadata` những thứ không phải nội dung; `input` / `output` tự bị che khi mask.

## Xử lý sự cố

| Triệu chứng | Cách sửa |
|---|---|
| Không thấy trace nào | `LANGFUSE_ENABLED=true` chưa? Đã khởi động lại backend? Log backend có cảnh báo "thiếu LANGFUSE_PUBLIC_KEY" / "Chưa cài thư viện langfuse" không |
| Log báo lỗi 401 khi gửi trace | Key sai, hoặc project ở vùng US mà `LANGFUSE_BASE_URL` vẫn là vùng EU (hoặc ngược lại) |
| Trace của eval thiếu vài câu cuối | Script thoát trước khi gửi xong; `eval_rag.py` đã gọi `flush()` ở cuối, nếu dừng giữa chừng (Ctrl+C) thì phần chưa gửi sẽ mất |
| Thấy `[ẩn — LANGFUSE_MASK_CONTENT=true]` thay cho câu hỏi | Đúng thiết kế. Cần xem nội dung thì đặt `LANGFUSE_MASK_CONTENT=false` (chỉ khi dev) |

## Chưa làm

- Nút 👍/👎 trên câu trả lời: backend gửi `trace_id` xuống frontend, khi người dùng bấm thì ghi score vào đúng trace.
- Trace chi tiết bên trong retriever (dense / BM25 / rerank riêng từng bước). Hiện chỉ có thời gian của cả lần tìm.
- Lượt chat mà người dùng ngắt **trước khi** stream bắt đầu có thể để lại trace chưa đóng (không được gửi lên).
