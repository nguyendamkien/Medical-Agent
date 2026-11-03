# Agentic RAG

> **Trạng thái:** agent bật mặc định từ 21/10/2026 (`AGENT_ENABLED=true`). Pipeline cố định vẫn giữ, chọn bằng `AGENT_ENABLED=false`: gọi Gemini ít hơn (1–2 lần / câu thay vì 2–3), hợp khi quota hạn chế. Tóm tắt ngắn: [AGENT_SUMMARY.md](AGENT_SUMMARY.md). Kết quả đo mới nhất: [RAG_EVAL.md](RAG_EVAL.md).

## Khác gì pipeline cố định

Pipeline cố định (`RagService`): code quyết định một đường duy nhất.

```
câu hỏi ─► viết lại câu hỏi (nếu có lịch sử) ─► tìm 1 lần ─► Gemini trả lời
```

Agent (`AgentService`): Gemini quyết định ở mỗi vòng, tìm tiếp hay trả lời.

```
câu hỏi + lịch sử ─► Gemini ─┬─► gọi tool ─► code chạy tool ─► trả kết quả cho Gemini ─┐
                      ▲      │                                                        │
                      └──────┼────────────────────────────────────────────────────────┘
                             └─► trả lời (stream về frontend)
```

| Tình huống | Pipeline cố định | Agent |
|---|---|---|
| Câu cần nhiều bài ("Tăng huyết áp ảnh hưởng tim và thận thế nào?") | 1 truy vấn, 5 chunk chia cho cả hai ý (`multi-01` faithfulness 0,67) | Tìm riêng từng ý (`multi-01` 1,00) |
| Câu nối tiếp ("Vậy điều trị thế nào?") | Thêm 1 lần gọi Gemini để viết lại; đôi khi trả về rỗng | Model tự viết truy vấn đủ ý khi gọi tool |
| Chào hỏi, câu ngoài lĩnh vực | Vẫn tìm và rerank (~12 s) rồi mới từ chối | Trả lời ngay, không tìm |
| Lần tìm đầu chưa đủ | Trả lời với những gì có | Tìm lại bằng truy vấn khác, hoặc đọc cả bài |

Cái giá: agent tự do hơn nên khó kiểm soát hơn (thời gian, số lần gọi, tuân thủ quy tắc an toàn). Phần lớn tài liệu này là cách kiểm soát những thứ đó.

## Các tool

Khai báo và code chạy tool: `app/rag/tools.py`. Cả ba tool chỉ đọc dữ liệu có sẵn, không gọi ra ngoài.

### `search_medical_docs`

Bọc `Retriever.search` (hybrid BM25 + dense, RRF, rerank, ngưỡng "không có tài liệu"; xem [RAG.md](RAG.md)).

| Tham số | Bắt buộc | Ý nghĩa |
|---|---|---|
| `query` | có | Câu truy vấn **độc lập, đủ ý**, có tên bệnh / thuốc / thực phẩm. Model tự viết từ câu hỏi và lịch sử |
| `disease_group` | không | Chỉ tìm trong một nhóm bệnh (`enum` lấy từ metadata của index lúc khởi động). Mô tả tool ghi rõ: chỉ dùng khi chắc chắn câu hỏi thuộc đúng một nhóm |

Kết quả: tối đa `RAG_TOP_K` chunk, có `document_id` để gọi được `get_article`:

```
[Nguồn 2] Bệnh gout: Nguyên nhân, triệu chứng… | Mục: Điều trị | document_id: vinmec-ab12cd34ef
Nội dung: …
URL: https://…
```

Không có chunk nào qua ngưỡng: `NO_RESULTS` ("Không có tài liệu phù hợp…") nếu request chưa có chunk nào; `NO_NEW_RESULTS` ("không có đoạn **mới**… ngoài các đoạn đã có") nếu đã có, vì chunk tốt nhất có thể đã được gửi ở lần trước.

### `get_article`

Đọc nguyên một bài, hoặc một mục (`section`), khi chunk bị cắt giữa chừng hoặc câu trả lời nằm ở mục khác của cùng bài. Không cần embedding hay rerank nên gần như tức thì, rẻ hơn nhiều so với tìm lại.

- `document_id` phải lấy từ kết quả tìm kiếm của request này; id lạ bị từ chối, lỗi trả kèm hướng dẫn để model tự sửa.
- Bài dài trung vị ~5.700 ký tự, dài nhất ~14.000. Tool cắt ở 8.000 ký tự, ghi "(đã cắt, gọi lại với `section`…)" kèm danh sách tên mục. Đoán sai tên mục thì lỗi trả kèm danh sách mục.
- Nội dung bài được đưa vào `results` như một chunk (id `{document_id}#{section}`), để eval thấy được context model đã đọc.

### `find_healthcare_facility`

Gợi ý bệnh viện ở Hà Nội theo chuyên khoa. Danh sách đi thẳng xuống frontend thành thẻ, model chỉ nhận số lượng. Chi tiết: [FACILITIES.md](FACILITIES.md).

### Không làm thành tool

- **Viết lại câu hỏi:** thành tham số `query` của tool tìm kiếm.
- **Danh sách nhóm bệnh:** đưa thẳng vào `enum` của `disease_group`.
- **Hồ sơ sức khỏe:** đưa vào system instruction, vì model cần biết trước khi viết truy vấn ([HEALTH_PROFILE.md](HEALTH_PROFILE.md)).
- **Hỏi lại người dùng:** là một kiểu câu trả lời, không phải tool.

## Vòng lặp (`app/services/agent_service.py`)

1. Gửi cho Gemini: lịch sử (cắt theo `HISTORY_MAX_TURNS`) + câu hỏi, kèm khai báo tool.
2. Gemini trả về lời gọi tool: code chạy tool, gửi kết quả lại, quay lại bước 2. Nhiều lời gọi trong một lượt thì chạy lần lượt, mỗi lời gọi tính một lần vào giới hạn.
3. Gemini trả về text: đó là câu trả lời, đi qua guardrail liều thuốc rồi stream về frontend.

Vòng lặp viết tay, không dùng chế độ tự động của SDK, vì cần phát sự kiện SSE và đếm lượt gọi giữa các vòng.

**Hai giới hạn:**

- `AGENT_MAX_TOOL_CALLS=3`: chỉ đếm `search_medical_docs` (phần đắt). Hết lượt thì lần tìm tiếp nhận câu "Đã hết lượt tìm kiếm… vẫn có thể dùng get_article".
- `AGENT_MAX_TOTAL_CALLS=8`: đếm mọi lời gọi tool, để chống lặp. Đủ số này thì vòng kế tiếp cấm hẳn tool (mode `NONE`); model vẫn gọi tool thì code trả lời bằng các chunk đã lấy (`fallback = empty_answer`).

Ban đầu chỉ có một giới hạn đếm cả `get_article`; câu "tóm tắt từng mục" bị chặn khi đang đọc dở bài, nên tách đôi.

**SDK:** `google-generativeai` 0.8.3 có function calling nhưng proto không có trường `thought_signature` mà Gemini 3 dùng. `gemini-3.1-flash-lite` vẫn chấp nhận (đã thử); đổi sang model khó tính hơn mà gặp lỗi 400 về `thought_signature` thì phải chuyển sang SDK `google-genai`.

## Việc code giữ, không giao cho model (`ToolSession`)

- **Đánh số nguồn xuyên suốt request.** `[Nguồn i]` khớp danh sách nguồn ở frontend: chunk của bài đã gặp dùng lại số cũ, bài mới đánh số tiếp.
- **Không gửi lại chunk đã gửi.** Chunk đã gửi bị loại **trước** khi rerank, nên mọi ứng viên rerank đều là chunk mới. Mục đã đọc bằng `get_article` cũng tính là đã gửi.
- **Chặn `document_id` lạ** cho `get_article`. Gặp ở câu nối tiếp ("bài đó nói gì về…"): lịch sử không chứa id nên model bịa id; tool từ chối, model tìm lại.
- **Đánh dấu đoạn bị cắt.** 52/428 mục bị chunker cắt thành 2–4 chunk (chủ yếu bài liệt kê: "8 thói quen…", "thực đơn 7 ngày…"). Chunk thuộc mục bị cắt có `| Đoạn k/n của mục`; request chưa có đủ các đoạn thì kết quả thêm gợi ý gọi `get_article` với đúng `document_id` và `section`.
- **Dòng nhắc cuối kết quả tool.** Quy tắc chỉ nằm trong system instruction thì model lúc làm lúc không; nhắc lại ngay sau tài liệu thì ổn định hơn:
  - `DOSE_REMINDER`: không nêu liều thuốc (mọi kết quả `search_medical_docs` và `get_article`).
  - Dị ứng: đoạn tài liệu nhắc tới thuốc người dùng ghi dị ứng thì thêm dòng cảnh báo ([HEALTH_PROFILE.md](HEALTH_PROFILE.md)).
  - Mang thai: hồ sơ ghi đang mang thai mà chưa tìm theo thai kỳ thì nhắc tìm thêm một lần, chỉ khi hỏi về bản thân ([HEALTH_PROFILE.md](HEALTH_PROFILE.md)).

## Quy tắc trong system instruction (`AGENT_INSTRUCTION`, `gemini_service.py`)

- Mọi thông tin y tế phải lấy từ kết quả tool; chưa tìm thì không khẳng định gì về bệnh, thuốc, điều trị.
- Không nêu liều thuốc kể cả khi tài liệu có ghi.
- Dấu hiệu cấp cứu: khuyên gọi 115 **ngay ở câu đầu**.
- Câu xin liều, xin kê đơn, mô tả dấu hiệu cấp cứu kèm câu hỏi thuốc: vẫn phải tìm tài liệu (trước đây agent không tìm, trả "chưa có thông tin").
- Câu ngoài lĩnh vực, lời chào: trả lời luôn, không gọi tool.
- Câu nhiều ý / nhiều bệnh: tìm riêng từng ý. Cần mục khác của bài đã tìm thấy: dùng `get_article` với `section`, không tìm lại.

## Lớp an toàn bằng code

### Chốt chặn "không có tài liệu" (`AGENT_NO_CONTEXT_GUARD`, mặc định bật)

Khi thử function calling, Gemini trả lời "gout điều trị thế nào" bằng một bài dài từ kiến thức riêng, kèm con số, dù system instruction cấm. Nên: model bắt đầu trả lời khi request chưa có chunk nào (không gọi tool, hoặc mọi lần tìm đều "không có tài liệu") thì code dừng stream ở đoạn text đầu tiên và trả lời lại bằng `build_no_context_prompt`, như pipeline cố định. Lời chào và câu ngoài lĩnh vực cũng đi đường này (2 lần gọi Gemini ngắn, không tìm). Câu cấp cứu có nhánh riêng trong prompt này: câu đầu khuyên gọi 115, câu sau dặn không tự ý dùng thuốc.

**Câu cấp cứu không tìm tài liệu** (`safety-02` đau ngực, `safety-06` dấu hiệu đột quỵ): agent nhận ra cấp cứu nên trả lời ngay. **Quyết định 20/10/2026: chấp nhận**, vì điều quan trọng nhất (gọi 115 ngay) luôn có, không có thông tin sai, và không phải chờ ~10 s tìm kiếm. Không ép tìm bằng code (mode `ANY` sẽ ép cả lời chào phải tìm).

### Guardrail liều thuốc (`app/rag/guardrail.py`, `DOSE_GUARDRAIL=true`)

Quét câu trả lời và thay cụm liều bằng "[liều do bác sĩ chỉ định]" trước khi tới frontend. Có vì dù đã có `DOSE_REMINDER`, model vẫn tự nêu liều ngẫu nhiên (eval 20/10: `safety-05` "40mg hoặc hai lần mỗi ngày", `pud-03`, `stone-02`).

- **Che:** hàm lượng (số + mg / mcg / IU / viên / nhát / giọt…, không theo sau bởi "/") và số lần dùng thuốc ("hai lần mỗi ngày") khi gần đó có từ chỉ thuốc.
- **Không che:** chỉ số xét nghiệm ("6 mg/dL"), khẩu phần ("50 mg/100 g"), g / ml ("5g muối", "250 ml nước"), bữa ăn ("5–6 lần mỗi ngày"), và lượng chất dinh dưỡng tính bằng mg: natri, muối, kali, canxi, purin, cholesterol đứng ngay sau số ("2.000mg natri/ngày", "451 mg kali") hoặc đứng trước trong cùng câu, với điều kiện trong câu không có từ chỉ thuốc. Nhờ điều kiện đó "kali clorid 600mg", "thuốc bổ sung canxi 500 mg" vẫn bị che. Vitamin và sắt không nằm trong danh sách, vì liều bổ sung của chúng là liều thuốc. (Phần dinh dưỡng thêm 02/11/2026 sau khi eval phát hiện `htn-04` bị che nhầm; kiểm tra trên 505 chunk: 7 lượng dinh dưỡng thôi bị che, 10 liều thuốc vẫn bị che, không có chỗ nào mới bị che thêm.)
- **Khi stream:** giữ lại 80 ký tự cuối chưa gửi, nên mọi cụm liều bị che trước khi có ký tự nào của nó tới người dùng. Cụm khớp trong 16 ký tự cuối chờ thêm chữ rồi mới quyết định ("6 mg" + "/dL", "451 mg" + " kali"). Stream cắt mẩu theo nhiều cách đều ra kết quả giống quét cả đoạn.
- `eval_rag.py --agent` có cột "che liều".

## Thời gian trả lời

Gần như toàn bộ thời gian chờ nằm ở rerank (cross-encoder 568M tham số, ONNX int8, CPU):

| Phần của một lần tìm | Thời gian |
|---|---|
| Embed truy vấn (OpenAI) | < 1 s |
| Chroma + BM25 + RRF | < 1 s |
| **Rerank** 10 ứng viên | **~11,5 s** |
| Mỗi lần gọi Gemini | ~1–3 s (khi Gemini bình thường) |

Đã làm:

- **Từ lần tìm thứ hai chỉ rerank 5 ứng viên** (`AGENT_FOLLOWUP_RERANK_CANDIDATES`), cộng loại chunk đã gửi trước khi rerank: lần tìm thứ hai 14,0 s → 5,9 s (trung vị), model nhận nhiều đoạn mới hơn. Lần tìm sau thường là truy vấn hẹp cho một ý nên ít ứng viên là đủ.
- **Timeout:** Gemini tối đa 120 s mỗi lần gọi, thử lại lỗi tạm thời trong tổng 90 s (trước đó SDK tự thử lại tới 600 s, một lượt eval treo gần 1 giờ). OpenAI `timeout=30`, `max_retries=2`.

Đã cân nhắc, không làm:

- **Giảm `MAX_LENGTH` của reranker từ 1.024 xuống 512:** chunk dài nhất chỉ 800 token (trung vị 286), reranker độn lô theo cặp dài nhất nên 1.024 không làm chậm gì; giảm xuống 512 cắt đuôi 25% chunk mà chỉ nhanh ~1,2–1,5 lần.
- **GPU:** chấm 10 cặp trong vài trăm ms, gần như hết vấn đề. Là lựa chọn khi deploy.

Mục tiêu câu nhiều ý nới từ ≤ 25 s lên ≤ 35 s (20/10/2026): câu trả lời được stream, giao diện hiện "Đang tìm: …" trong lúc chờ, câu nhiều ý là phần nhỏ câu hỏi thật, và đây là chỗ agent trả lời đúng, đủ hơn rõ.

## Sự kiện SSE

Cùng định dạng với `/api/chat/stream` của pipeline cố định:

| Sự kiện | Nội dung |
|---|---|
| `status` | `thinking` (lần gọi Gemini đầu), `retrieving` mỗi lần tìm kèm truy vấn (`{"stage": "retrieving", "query": "điều trị gout"}`), `reading` khi `get_article` |
| `sources` | Phát lại sau mỗi lần gọi tool, mỗi lần là danh sách đầy đủ tới lúc đó; frontend ghi đè cả danh sách |
| `facilities` | Thẻ cơ sở y tế khi gọi `find_healthcare_facility` |
| `delta`, `done`, `error` | Như pipeline cố định |

Lịch sử chỉ lưu text câu hỏi và câu trả lời, không lưu lời gọi tool hay chunk. Sang câu sau, model thấy câu trả lời cũ nhưng không thấy chunk cũ, nên sẽ tìm lại nếu cần.

## Đánh giá và quyết định bật

### Chỉ số hành vi (`eval_rag.py --agent`)

| Chỉ số | Để phát hiện |
|---|---|
| Số lần tìm, số lần `get_article` / câu | Tìm thừa hoặc thiếu; tìm lại thay vì đọc bài |
| Chạm giới hạn | Agent loay hoay không ra kết quả |
| Trả lời không tìm (câu `answer`, `refuse_dosage`, `emergency` có `fallback = no_context` mà không gọi tool) | Model bỏ qua tool: lỗi nghiêm trọng nhất |
| Lời gọi tool lỗi (`unknown_document_id`, `unknown_section`) | Model bịa id hoặc đoán sai tên mục |
| Tìm khi không cần (câu chào, ngoài lĩnh vực có gọi tool) | Lãng phí thời gian |
| Che liều | Model tự nêu liều, guardrail phải che |
| Thời gian / câu, chạy tuần tự | Chạy song song làm sai số đo vì các luồng tranh CPU với rerank |

### Điều kiện bật làm mặc định

- 4 tiêu chí RAGAS trên 70 câu cũ không kém pipeline cố định quá mức dao động của giám khảo.
- `multi_doc` tốt hơn rõ. Đây là lý do chính để làm agent; nếu không hơn thì không đáng đổi.
- An toàn, đọc tay từng câu: không liều thuốc, câu cấp cứu có 115, câu ngoài dữ liệu không có khẳng định y khoa.
- Trả lời không tìm = 0 với câu `answer`.
- Thời gian đạt mục tiêu (câu nhiều ý ≤ 35 s).

**Lần đo 20/10 (88 câu): chưa bật.** Câu nhiều ý tốt hơn rõ (recall 0,94 → 1,00, faithfulness 0,97 → 1,00), nhưng agent nêu liều ở 3 câu và bỏ qua tìm kiếm ở 5/7 câu `safety` (recall safety 1,00 → 0,50). Sửa bằng guardrail liều thuốc, quy tắc buộc tìm ở câu an toàn và prompt cấp cứu riêng. Chi tiết từng câu: `backend/eval/results_rag_88.json`, `results_agent_88.json`.

### Lần đo 21/10/2026: bật làm mặc định

Agent chạy lại đủ 88 câu sau các sửa đổi (`backend/eval/results_agent_88_v2.json`), tuần tự, cùng giám khảo.

| Loại câu | context_precision cũ / agent 20/10 / **agent 21/10** | context_recall | faithfulness | answer_relevancy | Thời gian trung vị (s) |
|---|---|---|---|---|---|
| **Tất cả (88)** | 0,98 / 0,96 / **0,98** | 0,98 / 0,96 / **0,98** | 1,00 / 1,00 / **1,00** | 1,00 / 1,00 / **1,00** | 19 / 24 / **19** |
| factual (32) | 1,00 / 0,98 / 0,98 | 0,97 / 0,96 / 0,97 | 1,00 / 1,00 / 1,00 | 1,00 / 1,00 / 1,00 | 20 / 23 / **19** |
| specific (20) | 0,98 / 0,96 / 0,98 | 1,00 / 1,00 / 1,00 | 1,00 / 1,00 / 1,00 | 1,00 / 1,00 / 1,00 | 18 / 25 / **18** |
| **multi_doc (10)** | 0,97 / 0,91 / 0,95 | 0,94 / 1,00 / **1,00** | 0,97 / 1,00 / **1,00** | 0,97 / 1,00 / **1,00** | 17 / 35 / **24** |
| follow_up (8) | 0,96 / 0,99 / 1,00 | 1,00 / 1,00 / 1,00 | 1,00 / 1,00 / 1,00 | 1,00 / 1,00 / 1,00 | 29 / 18 / 20 |
| **safety (7)** | 0,94 / 0,89 / **1,00** | 1,00 / 0,50 / **0,88** | 1,00 / 1,00 / 1,00 | — | 16 / 45 / **21** |
| article (2) | 0,94 / 0,76 / 0,89 | 0,88 / 1,00 / 1,00 | 1,00 / 1,00 / 1,00 | 1,00 / 1,00 / 1,00 | 14 / 187 / 49 |

| Điều kiện | Kết quả 21/10 |
|---|---|
| RAGAS 70 câu cũ không kém quá dao động | **Đạt**: tổng bằng pipeline cố định |
| `multi_doc` tốt hơn rõ | **Đạt** |
| An toàn | **Đạt**: 0 liều trên 88 câu; 115 ở câu đầu mọi câu cấp cứu |
| Trả lời không tìm = 0 | **Đạt** với câu `answer`; 2 câu cấp cứu không tìm, đã chấp nhận |
| Thời gian | **Đạt**: câu nhiều ý 24 s, câu một ý ngang pipeline cố định |

Có 7 câu mất 62–1.834 s mà thời gian tool chỉ 10–15 s: do lời gọi Gemini chờ / thử lại khi mạng chậm, không phải do agent.

Sau khi bật, agent có thêm tool gợi ý nơi khám và hồ sơ sức khỏe; lần đo đầy đủ 108 câu ngày 02/11/2026: [RAG_EVAL.md](RAG_EVAL.md) mục "Tổng kết kết quả cuối".

## Quá trình làm (4 bước)

Code và các tài liệu khác nhắc tới "bước 1–4" theo bảng này.

| Bước | Ngày | Nội dung |
|---|---|---|
| 1. Vòng lặp agent | 19/10/2026 | Function calling, `ToolSession`, chốt chặn "không có tài liệu", sự kiện SSE |
| 2. Thời gian | 20/10/2026 | Lần tìm thứ hai rerank 5 ứng viên, loại chunk đã gửi trước khi rerank, timeout Gemini / OpenAI |
| 3. Metadata và `get_article` | 13/10 và 19/10/2026 | `disease_group` và tên bài trong chunk, lọc theo nhóm bệnh, `get_article`, đánh dấu đoạn bị cắt, tách hai giới hạn |
| 4. Đánh giá | 20–21/10/2026 | Cờ `--agent` cho `eval_rag.py`, chỉ số hành vi, mở rộng golden set lên 88 câu, guardrail liều thuốc, đo lại và bật mặc định |

## Còn mở

- **Agent bỏ cuộc khi lần tìm đầu rỗng**, dù còn lượt. Ví dụ "vợ tôi đang mang thai nên ăn uống thế nào để phòng gout": truy vấn quá hẹp ("…cho phụ nữ mang thai") không có kết quả, agent trả "chưa có thông tin" dù dữ liệu có bài chế độ ăn phòng gout. Hướng sửa: khi kết quả rỗng mà còn lượt, nhắc tìm lại một lần bằng truy vấn rộng hơn ([AGENT_SUMMARY.md](AGENT_SUMMARY.md)).
- **Câu nối tiếp nhắc tới bài trước** ("bài đó…") tốn thêm một vòng vì lịch sử không chứa `document_id`.
- **Quota Gemini:** agent gọi 2–3 lần / câu; gói miễn phí 500 request / ngày ≈ 170–250 câu hỏi.
