# Tóm tắt: chatbot dùng Agentic RAG
 Gemini tự quyết định gọi 3 tool: `search_medical_docs`, `get_article`, `find_healthcare_facility`.

**Kết quả cuối (108 câu, 02/11/2026):** context_precision 0,97, context_recall 0,97, faithfulness 0,99, answer_relevancy 1,00; 0 lần nêu liều thuốc; gợi ý nơi khám 8/8 gọi đúng tool.

## Đã làm

| Phần | Nội dung |
|---|---|
| Vòng lặp agent | Gemini function calling, tự quyết định tìm 0–3 lần rồi trả lời. Code giữ đánh số nguồn, bỏ chunk trùng, chặn `document_id` lạ, chốt chặn "không có tài liệu thì không kể thêm". Frontend hiện "Đang tìm: …" |
| Thời gian | Lần tìm thứ hai loại chunk đã gửi trước khi rerank và chỉ rerank 5 ứng viên: **14,0 → 5,9 s**. Timeout cho Gemini (120 s) và OpenAI (30 s) |
| Metadata + `get_article` | Lọc theo nhóm bệnh, tên bài trong chunk, đọc cả bài / một mục, đánh dấu "Đoạn k/n của mục". Hai giới hạn: 3 lần tìm, 8 lời gọi tool |
| An toàn | Dòng nhắc "không nêu liều" cuối kết quả tool + guardrail che liều trong lúc stream; câu xin thuốc / liều phải tìm tài liệu; prompt cấp cứu riêng (115 ở câu đầu) |
| Hồ sơ sức khỏe ([HEALTH_PROFILE.md](HEALTH_PROFILE.md)) | Hồ sơ vào system instruction (không làm tool: thêm 1 vòng Gemini và model có thể quên gọi). Code nhắc cảnh báo thuốc dị ứng và nhắc tìm theo thai kỳ |
| Gợi ý nơi khám ([FACILITIES.md](FACILITIES.md)) | Tool `find_healthcare_facility`: danh sách bệnh viện đi thẳng xuống giao diện thành thẻ, model chỉ nhận số lượng; không tính vào lượt tìm |
| Đánh giá | `eval_rag.py --agent`, chỉ số hành vi agent; golden set 70 → 108 câu |

## Agent so với pipeline cố định (88 câu, 21/10/2026)

| Loại câu | Chất lượng | Thời gian trung vị (cố định / agent) |
|---|---|---|
| Tất cả (88) | Bằng nhau: 0,98 / 0,98 / 1,00 / 1,00 | 19 / 19 s |
| Một ý (52) | Ngang nhau | 19 / 19 s |
| Nhiều ý (10) | **Agent tốt hơn**: recall 0,94 → 1,00, faithfulness 0,97 → 1,00 | 17 / 24 s |
| Nối tiếp (8) | Ngang nhau | 29 / 20 s |
| An toàn (7) | Cả hai không nêu liều, có 115 ở câu đầu câu cấp cứu; agent recall 0,88 (cố định 1,00) | 16 / 21 s |

## Các vấn đề đã xử lý

| Vấn đề | Cách sửa |
|---|---|
| Agent nêu liều thuốc ở 3 câu (20/10) | Dòng nhắc cuối kết quả tool + guardrail bằng code. Sau đó 0 liều trên 88 rồi 108 câu |
| Agent bỏ qua tìm kiếm ở câu xin thuốc / liều (20/10) | Quy tắc buộc tìm: 11/11 lượt tìm. Câu cấp cứu thuần (đau ngực, đột quỵ) vẫn trả lời ngay không tìm, luôn có 115 ở câu đầu: **chấp nhận** |
| Hồ sơ mang thai: agent không tìm theo thai kỳ (`profile-05`, 02/11) | Code nhắc tìm thêm theo thai kỳ: 5/5 lần tìm đúng, recall nhóm `profile` 0,91 → 1,00 |
| Guardrail che nhầm "2.000mg natri/ngày" (`htn-04`, 02/11) | Không che lượng chất dinh dưỡng khi trong câu không có từ chỉ thuốc |

## Vấn đề còn mở

| # | Vấn đề | Thấy ở | Hướng sửa |
|---|---|---|---|
| 1 | Lần tìm đầu không có kết quả thì agent bỏ cuộc, trả "chưa có thông tin" dù dữ liệu có bài phù hợp, dù còn lượt tìm | `safety-04` ("huyết áp 150/95, kê thuốc gì", hỏng 2/4 lần); "vợ tôi mang thai nên ăn gì để phòng gout" (3/3 lần, truy vấn quá hẹp) | Khi kết quả rỗng mà còn lượt, nhắc tìm lại một lần bằng truy vấn rộng hơn (chỉ tên bệnh + chủ đề, bỏ đối tượng / hoàn cảnh, bỏ `disease_group`); nếu chỉ có thông tin chung thì trả lời bằng đó và nói rõ |
| 2 | Hỏi tiếp về thẻ bệnh viện ("bệnh viện đầu tiên ở đâu?") → "chưa có thông tin về danh sách bệnh viện". Lịch sử gửi cho model chỉ có chữ, model không biết lượt trước có thẻ | Thử trực tiếp 2/2 | Khi đọc lịch sử, gắn vào câu trả lời cũ một dòng ngắn (thẻ đã hiện, bài nguồn, `document_id`) chỉ để gửi cho model |
| 3 | Câu "bài đó nói gì…" bịa `document_id`; code chặn được, agent tự tìm lại nhưng tốn thêm một vòng. Cùng nguyên nhân với #2 | `follow-08` | Như #2 |
| 4 | `profile-08`: nhắc thừa dị ứng aspirin khi tài liệu không nhắc tới; lúc trượt lúc không | eval 01–02/11 | Chưa quyết định |

## Việc tiếp theo

1. Sửa các vấn đề còn mở ở trên; sửa xong chạy lại `safety-04` vài lần, nhóm `follow_up`, `facility` và đủ 108 câu.
2. Theo dõi quota và độ trễ của Gemini: agent gọi 2–3 lần / câu, gói miễn phí 500 request / ngày ≈ 170–250 câu hỏi. Khi Gemini quá tải (lỗi 503 / 504), một câu có thể mất vài phút; lần chạy lại 108 câu ngày 03/11 bị huỷ vì lý do này.
3. Kiểm tra tay 7 bệnh viện chưa xác minh trong `backend/app/data/facilities_hanoi.jsonl`.
