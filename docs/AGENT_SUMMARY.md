# Tóm tắt: chuyển chatbot sang Agentic RAG

> Cập nhật 20/10/2026 (tối). Chi tiết, số đo và lý do từng quyết định: [AGENTIC_RAG.md](AGENTIC_RAG.md). Cách đo: [RAG_EVAL.md](RAG_EVAL.md).

**Kết luận:** agent đã chạy được và trả lời câu nhiều ý tốt hơn rõ. Lỗi lộ liều đã được guardrail chặn, thời gian câu nhiều ý (35 s) đã được chấp nhận. **Vẫn tắt mặc định** (`AGENT_ENABLED=false`) cho tới khi chạy lại eval đầy đủ với các sửa đổi mới.

## Đã làm

| Bước | Nội dung | Trạng thái |
|---|---|---|
| 1. Vòng lặp agent | Gemini function calling, tự quyết định tìm 0–3 lần rồi trả lời. 2 tool: `search_medical_docs`, `get_article`. Code giữ đánh số nguồn, bỏ chunk trùng, chặn `document_id` lạ, chốt chặn "không có tài liệu thì không kể thêm". Frontend hiện "Đang tìm: …" | Xong |
| 2. Thời gian | Lần tìm thứ hai loại chunk đã gửi trước khi rerank và chỉ rerank 5 ứng viên: **14,0 → 5,9 s**. Thêm timeout cho Gemini (120 s) và OpenAI (30 s) — trước đó mạng chập làm request treo 10–60 phút | Xong (mục tiêu câu nhiều ý nới lên ≤ 35 s) |
| 3. Metadata + `get_article` | Lọc theo nhóm bệnh, tên bài trong chunk, đọc cả bài / một mục. Đánh dấu "Đoạn k/n của mục" khi mục bị cắt. Tách giới hạn: 3 lần tìm, 8 lời gọi tool | Xong |
| 4. Đánh giá | `eval_rag.py --agent --resume`, chỉ số hành vi agent. Golden set 70 → 88 câu. Đo cả hai pipeline trên 88 câu, đọc tay câu an toàn | Xong |

## Kết quả đo (88 câu, pipeline cũ / agent)

| Loại câu | Chất lượng | Thời gian trung vị |
|---|---|---|
| Một ý (52) | Ngang nhau | 19 / 24 s |
| Nhiều ý (10) | **Agent tốt hơn**: recall 0,94 → 1,00, faithfulness 0,97 → 1,00 | 17 / **35 s** |
| Nối tiếp (8) | Ngang nhau | 29 / 18 s |
| Chào hỏi (3) | Agent không tìm, đúng ý đồ | 13 / 5 s |
| An toàn (7) | **Agent kém hơn**: recall 1,00 → 0,50 | 16 / 45 s |

## Vấn đề còn lại

- **Nêu liều thuốc — đã chặn:** agent từng nêu liều ở 3 câu. Thêm lời nhắc cuối kết quả tool và **guardrail** (`app/rag/guardrail.py`) che liều trong câu trả lời trước khi tới người dùng: kiểm tra 25 lượt, model tự nêu liều 1 lần, guardrail che kịp; 176 câu trả lời cũ không che nhầm chỗ nào.
- **Bỏ qua tìm kiếm ở câu an toàn — sửa một phần:** câu xin thuốc / liều giờ tìm 11/11 lượt. Câu cấp cứu như đau ngực, dấu hiệu đột quỵ: agent trả lời ngay không tìm (0/5), luôn khuyên gọi 115 ở câu đầu — **chấp nhận** (20/10). Prompt "không có tài liệu" đã có nhánh riêng cho câu cấp cứu: chỉ tập trung vào gọi 115, không còn câu "chưa có thông tin trong dữ liệu".
- **Thời gian:** câu nhiều ý 35 s — chấp nhận (mục tiêu nới từ ≤ 25 s lên ≤ 35 s ngày 20/10). Câu một ý vẫn chậm hơn pipeline cũ ~5 s.

## Việc tiếp theo

1. Chạy lại `eval_rag.py --agent` đầy đủ 88 câu với guardrail và quy tắc mới, rồi quyết định bật agent.
2. (Không làm) giảm `MAX_LENGTH` reranker: chunk dài nhất 800 token, giảm xuống 512 cắt 25% chunk mà chỉ nhanh ~1,2–1,5 lần.
