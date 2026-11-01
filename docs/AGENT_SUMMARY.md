# Tóm tắt: chuyển chatbot sang Agentic RAG

> Cập nhật 21/10/2026; thêm mục "Sau khi bật" ngày 01/11/2026. Chi tiết, số đo và lý do từng quyết định: [AGENTIC_RAG.md](AGENTIC_RAG.md). Cách đo: [RAG_EVAL.md](RAG_EVAL.md).

**Kết luận: agent bật mặc định từ 21/10/2026** (`AGENT_ENABLED=true`). Đo lại 88 câu sau các sửa đổi: chất lượng tổng bằng pipeline cũ (0,98 / 0,98 / 1,00 / 1,00), câu nhiều ý tốt hơn, 0 liều thuốc trên 88 câu trả lời, câu nhiều ý 24 s, câu một ý ngang pipeline cũ. Pipeline cố định vẫn chọn được bằng `AGENT_ENABLED=false` (gọi Gemini ít hơn, hợp khi quota hạn chế).

## Đã làm

| Bước | Nội dung | Trạng thái |
|---|---|---|
| 1. Vòng lặp agent | Gemini function calling, tự quyết định tìm 0–3 lần rồi trả lời. 2 tool: `search_medical_docs`, `get_article`. Code giữ đánh số nguồn, bỏ chunk trùng, chặn `document_id` lạ, chốt chặn "không có tài liệu thì không kể thêm". Frontend hiện "Đang tìm: …" | Xong |
| 2. Thời gian | Lần tìm thứ hai loại chunk đã gửi trước khi rerank và chỉ rerank 5 ứng viên: **14,0 → 5,9 s**. Thêm timeout cho Gemini (120 s) và OpenAI (30 s) — trước đó mạng chập làm request treo 10–60 phút | Xong (mục tiêu câu nhiều ý nới lên ≤ 35 s) |
| 3. Metadata + `get_article` | Lọc theo nhóm bệnh, tên bài trong chunk, đọc cả bài / một mục. Đánh dấu "Đoạn k/n của mục" khi mục bị cắt. Tách giới hạn: 3 lần tìm, 8 lời gọi tool | Xong |
| 4. Đánh giá | `eval_rag.py --agent --resume`, chỉ số hành vi agent. Golden set 70 → 88 câu. Đo cả hai pipeline trên 88 câu, đọc tay câu an toàn | Xong |

## Kết quả đo (88 câu, pipeline cũ / agent 21/10)

| Loại câu | Chất lượng | Thời gian trung vị |
|---|---|---|
| Tất cả (88) | Bằng nhau: 0,98 / 0,98 / 1,00 / 1,00 | 19 / 19 s |
| Một ý (52) | Ngang nhau | 19 / 19 s |
| Nhiều ý (10) | **Agent tốt hơn**: recall 0,94 → 1,00, faithfulness 0,97 → 1,00 | 17 / 24 s |
| Nối tiếp (8) | Ngang nhau | 29 / 20 s |
| An toàn (7) | Cả hai không nêu liều, có 115 ở câu đầu câu cấp cứu; agent recall 0,88 (pipeline cũ 1,00) | 16 / 21 s |

Số đo ngày 20/10, trước guardrail và quy tắc an toàn: agent chậm hơn (câu nhiều ý 35 s), recall an toàn 0,50, nêu liều ở 3 câu — xem [AGENTIC_RAG.md](AGENTIC_RAG.md).

## Các vấn đề đã xử lý

- **Nêu liều thuốc — đã chặn:** agent từng nêu liều ở 3 câu. Thêm lời nhắc cuối kết quả tool và **guardrail** (`app/rag/guardrail.py`) che liều trong câu trả lời trước khi tới người dùng: kiểm tra 25 lượt, model tự nêu liều 1 lần, guardrail che kịp; 176 câu trả lời cũ không che nhầm chỗ nào.
- **Bỏ qua tìm kiếm ở câu an toàn — đã sửa:** câu xin thuốc / liều giờ tìm 11/11 lượt. Câu cấp cứu như đau ngực, dấu hiệu đột quỵ: agent trả lời ngay không tìm (0/5), luôn khuyên gọi 115 ở câu đầu — **chấp nhận** (20/10). Prompt "không có tài liệu" đã có nhánh riêng cho câu cấp cứu: chỉ tập trung vào gọi 115, không còn câu "chưa có thông tin trong dữ liệu".
- **Thời gian:** mục tiêu câu nhiều ý nới từ ≤ 25 s lên ≤ 35 s (20/10). Đo 21/10: câu nhiều ý 24 s, câu một ý ngang pipeline cũ.

## Sau khi bật (01/11/2026)

| Tính năng | Thay đổi ở agent | Kết quả đo |
|---|---|---|
| Hồ sơ sức khỏe ([HEALTH_PROFILE.md](HEALTH_PROFILE.md)) | Hồ sơ đưa vào cuối system instruction ở mọi vòng (không làm tool riêng: thêm 1 vòng Gemini mỗi câu và model có thể quên gọi). Code quét thuốc dị ứng trong kết quả tool, thêm dòng nhắc cảnh báo | 12 câu `profile`: 11/12 đạt, chất lượng 0,97 / 0,91 / 1,00 / 0,97 |
| Gợi ý nơi khám ([FACILITIES.md](FACILITIES.md)) | Tool thứ 3 `find_healthcare_facility`: danh sách bệnh viện đi thẳng xuống giao diện thành thẻ, model chỉ nhận số lượng. Chốt chặn "không có tài liệu" dùng `session.has_content`; tool này không tính vào lượt tìm | 8 câu `facility`: 8/8 gọi tool đúng, không số điện thoại tự bịa |

Agent giờ có **3 tool**: `search_medical_docs`, `get_article`, `find_healthcare_facility`. Chưa chạy lại 88 câu cũ sau hai thay đổi này.

## Việc tiếp theo

1. ~~Chạy lại eval đầy đủ rồi quyết định bật agent~~ — xong 21/10, đã bật.
2. Theo dõi quota Gemini: agent gọi 2–3 lần / câu, gói miễn phí 500 request/ngày ≈ 170–250 câu hỏi.
3. (Không làm) giảm `MAX_LENGTH` reranker: chunk dài nhất 800 token, giảm xuống 512 cắt 25% chunk mà chỉ nhanh ~1,2–1,5 lần.
4. Chạy lại đủ 108 câu (`eval_rag.py --agent`) để chắc hồ sơ và tool mới không làm giảm chất lượng các câu cũ.
5. Kiểm tra tay 7 bệnh viện chưa xác minh trong `backend/app/data/facilities_hanoi.jsonl`.
