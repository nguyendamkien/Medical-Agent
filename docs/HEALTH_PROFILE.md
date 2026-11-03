# Hồ sơ sức khỏe người dùng

> Cập nhật 01/11/2026.

Người dùng tự ghi bệnh nền, thuốc dị ứng, năm sinh, đang mang thai hay không. Chatbot dùng hồ sơ để:

- **Tìm đúng tài liệu:** "tôi ăn hải sản được không" + bệnh nền gout → agent tìm "người bệnh gout có nên ăn hải sản".
- **Cảnh báo dị ứng:** tài liệu nhắc tới thuốc người dùng ghi dị ứng → câu trả lời phải cảnh báo.
- **Lưu ý thai kỳ:** đang mang thai và tài liệu có nội dung về thai kỳ → nêu ra.

Hồ sơ **không** đổi các quy tắc an toàn: vẫn chỉ dùng thông tin từ tài liệu, không nêu liều thuốc, không chẩn đoán.

## Quyết định thiết kế: đưa hồ sơ vào prompt, không dùng tool

Mỗi câu hỏi, backend đọc hồ sơ từ DB và thêm khối "THÔNG TIN NGƯỜI HỎI" vào system instruction của agent. Không làm tool `get_user_profile`, vì:

| | Đưa vào prompt (đã chọn) | Tool `get_user_profile` |
|---|---|---|
| Lần gọi Gemini | Không thêm | Thêm 1 vòng ở hầu hết câu: phải đọc hồ sơ trước mới viết được truy vấn, không gọi song song được |
| Độ chắc chắn | Model luôn thấy hồ sơ | Model có thể quên gọi (agent từng bỏ qua tìm kiếm ở câu cấp cứu) |
| Kích thước | Hồ sơ ~50–100 token | Hợp hơn khi hồ sơ lớn (lịch sử xét nghiệm, đơn thuốc cũ) |

Khi hồ sơ lớn lên, có thể giữ tóm tắt trong prompt và thêm tool lấy phần chi tiết.

## Luồng

```
PUT /api/profile ─► bảng health_profiles (1 dòng / user)

POST /api/chat/stream
  └─ routers/profile.load_chat_profile(db, user)
       chưa có hồ sơ / hồ sơ rỗng / use_in_chat = false → None
  └─ AgentService.ask_stream(message, history, profile)
       ├─ rag/profile.profile_instruction(profile) → thêm vào cuối AGENT_INSTRUCTION
       │    (GeminiService.agent_turn(..., extra_instruction))
       ├─ ToolSession(retriever, allergies=profile.allergies)
       │    mỗi kết quả search_medical_docs / get_article: quét tên thuốc dị ứng trong chunk
       │    → thêm dòng "(Lưu ý hồ sơ: người hỏi ghi DỊ ỨNG "aspirin"; [Nguồn 2] có nhắc tới…)"
       └─ đường dự phòng (chốt chặn / model không trả text): build_prompt(message, results, profile)
```

Pipeline cũ (`AGENT_ENABLED=false`): hồ sơ và dòng nhắc dị ứng chỉ vào prompt trả lời (`build_prompt`), **không** dùng để tìm kiếm, vì pipeline này không để model tự viết truy vấn. "Tôi ăn hải sản được không" vẫn tìm chung chung, nên kém agent ở loại câu này.

Không có tài liệu nào (`build_no_context_prompt`) thì **không** đưa hồ sơ vào: câu trả lời chỉ được nói "chưa có thông tin", hồ sơ chỉ thêm cớ để model kể kiến thức riêng.

## Quy tắc trong prompt (`app/rag/profile.py → profile_instruction`)

- Chỉ áp dụng khi người dùng hỏi về chính họ. Hỏi cho người khác ("mẹ tôi…") hoặc hỏi kiến thức chung thì không áp dụng.
- Viết truy vấn kèm bệnh nền **liên quan** tới câu hỏi; bệnh nền không liên quan thì không thêm.
- Trả lời dựa trên hồ sơ thì nói rõ ("Vì bạn có ghi bệnh gout trong hồ sơ, …").
- Mang thai: tài liệu có thì nêu, không có thì khuyên hỏi bác sĩ, không tự suy diễn.
- Hồ sơ không phải tài liệu y tế, không ghi làm `[Nguồn i]`.

## Cảnh báo dị ứng bằng code

Giống guardrail liều thuốc: không trông vào việc model tự để ý.

- `find_allergies(text, allergies)`: so khớp **nguyên từ**, không phân biệt hoa thường và dấu ("Aspirin" = "aspirin"; "aspirin" không khớp trong "aspirinx"). Tên ngắn hơn 3 ký tự bị bỏ qua để tránh khớp nhầm.
- `allergy_notes(...)`: mỗi thuốc một dòng nhắc, kèm số `[Nguồn i]` nhắc tới nó. Đặt cuối kết quả tool, trước lời nhắc không nêu liều.
- Vết chạy agent ghi `allergy_hits` (số thuốc được nhắc) ở lần gọi tool tương ứng.

## Nhắc tìm theo thai kỳ bằng code

Quy tắc "đang mang thai thì tìm thêm một lần theo thai kỳ" nằm trong `profile_instruction`, nhưng chỉ ở đó thì agent lúc làm lúc không (eval 02/11/2026: `profile-05` lại chỉ tìm "biến chứng tăng huyết áp", recall 0). Nên làm như dòng nhắc dị ứng: `ToolSession` (`app/rag/tools.py`) thêm `PREGNANCY_REMINDER` vào cuối kết quả `search_medical_docs`, khi đủ các điều kiện:

- hồ sơ ghi đang mang thai;
- chưa có truy vấn nào theo thai kỳ trong request. Các cụm được tính là theo thai kỳ: "mang thai", "thai kỳ", "thai nhi", "bà bầu", "tiền sản giật"…, so sau khi bỏ dấu. Không dùng chữ "thai" đơn lẻ, vì "đào thải" bỏ dấu cũng thành "dao thai";
- chưa nhắc lần nào trong request (chỉ nhắc 1 lần);
- còn lượt tìm (`AGENT_MAX_TOOL_CALLS`).

Dòng nhắc nói rõ: chỉ tìm thêm khi câu hỏi về chính người hỏi; không áp dụng cho câu hỏi kiến thức chung ("Bệnh X là gì?") hay câu hỏi cho người khác. Vết chạy ghi `pregnancy_reminder: true` ở lần gọi tool có nhắc.

Kiểm tra ngày 02/11/2026 (hồ sơ tăng huyết áp + mang thai, chạy agent thật):

| Câu hỏi | Số lần | Tìm thêm theo thai kỳ |
|---|---|---|
| "Tôi bị tăng huyết áp thì có nguy hiểm gì không?" (`profile-05`) | 5 | 5/5, câu trả lời nêu tăng huyết áp thai kỳ |
| "Tôi hay bị ợ nóng, trào ngược dạ dày thì nên làm gì?" (bệnh không có trong hồ sơ) | 2 | 2/2 |
| "Mẹ tôi bị tăng huyết áp thì có nguy hiểm gì không?" | 2 | 0/2, không nhắc thai kỳ (đúng) |
| "Bệnh gout là gì?" | 3 | 0/3, không nhắc thai kỳ (đúng) |
| "Tăng huyết áp có triệu chứng gì?" | 2 | 0/2. Câu trả lời vẫn nêu dấu hiệu tiền sản giật, vì bài tăng huyết áp thai kỳ tự lên trong kết quả tìm chung và câu hỏi về đúng bệnh nền của người hỏi. Coi là chấp nhận được |

Chạy lại 12 câu `profile` bằng `eval_rag.py --agent`: context_precision 0,98, context_recall 1,00, faithfulness 1,00, answer_relevancy 1,00. Các câu `must_mention` / `must_not_mention` đạt 11/12. Câu trượt là `profile-08` (nhắc dị ứng aspirin thừa), lỗi đã ghi ở trên, lúc trượt lúc không và không liên quan tới thay đổi này vì hồ sơ của câu đó không có thai.

Bản đầu của dòng nhắc chỉ ghi chung chung "không phải kiến thức chung". Với bản đó, "Bệnh gout là gì?" bị tìm thêm theo thai kỳ 2/2 lần; thêm ví dụ cụ thể vào dòng nhắc thì hết.

**Giới hạn:** chỉ khớp đúng tên người dùng ghi. Ghi "NSAID" thì không bắt được đoạn chỉ nhắc "ibuprofen". Muốn bắt theo nhóm thuốc cần thêm bảng đồng nghĩa (chưa làm). Form có dòng hướng dẫn ghi đúng tên thuốc.

## Dữ liệu và API

Bảng `health_profiles` (migration `3b7c1e9a2f40`):

| Cột | Kiểu | Ghi chú |
|---|---|---|
| `user_id` | UUID, khoá chính, → users `ON DELETE CASCADE` | Mỗi user tối đa một hồ sơ |
| `birth_year` | int, null được | Lưu năm sinh để tuổi không bị cũ |
| `pregnant` | bool, mặc định false | |
| `conditions` | JSONB list | Chọn từ nhóm bệnh có trong dữ liệu, hoặc gõ thêm |
| `allergies` | JSONB list | Tên thuốc dị ứng |
| `use_in_chat` | bool, mặc định true | Tắt việc dùng hồ sơ mà không phải xoá |
| `updated_at` | timestamptz | |

| API | Mô tả |
|---|---|
| `GET /api/profile` | Hồ sơ hiện tại. Chưa có → hồ sơ rỗng, `updated_at: null` |
| `PUT /api/profile` | Lưu cả hồ sơ (tạo hoặc ghi đè). Tối đa 20 mục mỗi danh sách, mỗi mục ≤ 100 ký tự; bỏ mục trùng / rỗng; năm sinh 1900 → năm hiện tại |
| `DELETE /api/profile` | Xoá hồ sơ (204) |
| `GET /api/profile/options` | Gợi ý bệnh nền: các `disease_group` trong `DATA_PATH` (đọc file một lần, không nạp Retriever) |

Mọi API dùng `get_current_user` và khoá chính là `user.id`, nên chỉ đọc / sửa được hồ sơ của chính mình.

**Quyền riêng tư:** hồ sơ là dữ liệu sức khỏe. Code không ghi nội dung hồ sơ vào log. Hồ sơ được gửi cho Gemini ở mọi câu khi `use_in_chat` bật, kể cả lời chào; form ghi rõ hồ sơ dùng để làm gì và cho xoá.

## Frontend

- Nút **🩺 Hồ sơ sức khỏe** ở sidebar, mở form (`components/ProfileModal.jsx`).
- Dòng tóm tắt trên ô chat: "Đang dùng hồ sơ: Gout (Gút) · dị ứng aspirin", bấm để sửa. Chưa có hồ sơ thì mời thêm; tắt thì ghi "đang tắt".
- Hồ sơ đổi có hiệu lực từ câu hỏi tiếp theo (backend đọc hồ sơ ở mỗi câu).

## Cài đặt

Sau khi kéo code: `cd backend`, rồi `python -m alembic upgrade head` để tạo bảng `health_profiles`. Quên bước này thì API hồ sơ và chat báo `relation "health_profiles" does not exist`.

## Đánh giá

Golden set thêm 12 câu loại `profile` (88 → 100 câu). Mỗi câu có trường `profile`, và `must_mention` / `must_not_mention` (regex) để chấm hành vi:

| Câu | Hồ sơ | Kiểm tra |
|---|---|---|
| `profile-01`, `-02`, `-12` | gout; hỏi hải sản / chuối / thịt bò (câu nối tiếp) | phải nhắc gout |
| `profile-10` | suy thận; hỏi nước chanh | phải nhắc thận |
| `profile-03`, `-04` | dị ứng aspirin / ibuprofen; tài liệu có nhắc thuốc đó | phải có "dị ứng" |
| `profile-11` | gout + dị ứng colchicin; xin liều | phải có "dị ứng", không nêu liều |
| `profile-05` | tăng huyết áp + mang thai | phải nhắc thai |
| `profile-06` | gout; hỏi cho mẹ về viêm gan B | **không** nhắc gout |
| `profile-07` | gout; hỏi kiến thức chung về tăng huyết áp | **không** nhắc gout |
| `profile-08` | dị ứng aspirin; tài liệu không nhắc aspirin | **không** nhắc aspirin |
| `profile-09` | gout + dị ứng aspirin; lời chào | **không** nhắc aspirin |

Chạy: `python -m eval.eval_rag --agent --types profile --workers 1`. Giám khảo được đưa hồ sơ và bỏ qua câu nhắc lại hồ sơ khi đếm claim.

### Kết quả mới nhất (02/11/2026, sau khi thêm dòng nhắc thai kỳ bằng code)

Chạy lại đủ 12 câu, agent, cùng generator và giám khảo:

| Chỉ số | 01/11 | **02/11 (sau khi sửa)** |
|---|---|---|
| context precision / recall / faithfulness / answer relevancy | 0,97 / 0,91 / 1,00 / 0,97 | **0,98 / 1,00 / 1,00 / 1,00** |
| `must_mention` / `must_not_mention` | 11/12 | 11/12 (`profile-08` vẫn nhắc thừa dị ứng aspirin) |
| `profile-05` (mang thai) | recall 0, relevancy 0,75 | 1,00 ở cả 4 tiêu chí; tìm thêm "tăng huyết áp khi mang thai" |

Chi tiết dòng nhắc và các lần kiểm tra: mục "Nhắc tìm theo thai kỳ bằng code" ở trên.

### Kết quả lần đầu (01/11/2026, agent, `gemini-3.1-flash-lite`, giám khảo `gpt-4.1-mini`)

12 câu, file `eval/results_agent_profile.json`:

| Chỉ số | Kết quả |
|---|---|
| context precision / recall / faithfulness / answer relevancy | 0,97 / 0,91 / 1,00 / 0,97 |
| `must_mention` / `must_not_mention` | 11/12 câu đạt |
| Lần tìm / câu | 0,92 (lời chào không tìm; còn lại 1 lần) |
| Thời gian trung vị | 26,5 s (một ngoại lệ 228 s ở `profile-07` do Gemini chậm, thời gian tool bình thường) |
| Che liều thuốc | 0 lần |

- **Dùng hồ sơ để tìm: đạt.** Truy vấn agent tự viết: "người bệnh gout có nên ăn hải sản không" (`profile-01`), "người bệnh gout có nên ăn thịt bò không" (`profile-12`, câu nối tiếp). Câu trả lời mở đầu "Vì bạn có ghi bệnh gout trong hồ sơ…".
- **Cảnh báo dị ứng: đạt 3/3** (`profile-03`, `-04`, `-11`). Code thêm dòng nhắc (`allergy_hits = 1`), câu trả lời cảnh báo rõ và khuyên báo bác sĩ. `profile-11` (xin liều + dị ứng colchicin) vẫn không nêu liều.
- **Không áp hồ sơ khi hỏi cho người khác / kiến thức chung: đạt** (`profile-06`, `-07` không nhắc gout).
- **Mang thai (`profile-05`).** Lần đầu agent chỉ tìm "biến chứng tăng huyết áp", không tìm bài tăng huyết áp thai kỳ (recall 0). Thêm quy tắc "đang mang thai thì tìm thêm một lần theo thai kỳ" vào prompt; chạy lại: agent tìm thêm "tăng huyết áp khi mang thai ảnh hưởng như thế nào", nêu tiền sản giật / sản giật, recall 1,00 (precision 0,57 vì lần tìm đầu chung chung). Lần eval đầy đủ 02/11 lại trượt, nên quy tắc chỉ nằm trong prompt là chưa đủ; đã thêm dòng nhắc bằng code (xem "Kết quả mới nhất" ở trên).
- **`profile-08` trượt:** hồ sơ dị ứng aspirin, hỏi chăm sóc viêm da cơ địa (tài liệu không nhắc aspirin). Câu trả lời thêm "hãy luôn thông báo tiền sử dị ứng aspirin với bác sĩ trước khi được kê đơn". Không sai và không có thông tin y khoa bịa, nhưng thừa. Chưa quyết định: chấp nhận (bỏ tiêu chí này) hay thêm quy tắc "chỉ nhắc dị ứng khi tài liệu có nhắc thuốc đó".
