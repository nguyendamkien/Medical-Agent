# Gợi ý bệnh viện, phòng khám (Hà Nội)

> Cập nhật 01/11/2026. Đi qua code từng bước: [FACILITIES_WALKTHROUGH.md](FACILITIES_WALKTHROUGH.md).

Người dùng hỏi "nên khám ở đâu", agent gọi tool `find_healthcare_facility`. Code chọn cơ sở theo chuyên khoa từ danh sách đã kiểm tra, rồi gửi thẳng xuống giao diện thành **thẻ**: tên, địa chỉ, số điện thoại, nút chỉ đường. Model chỉ nhận **tên** các cơ sở để viết 1–3 câu dẫn; địa chỉ và số điện thoại không đi qua model, nên model không chép sai hay bịa được.

## Dữ liệu: `backend/app/data/facilities_hanoi.jsonl`

24 cơ sở ở Hà Nội: 19 công lập (10 tuyến trung ương, 9 của thành phố), 5 tư nhân. Danh sách được chọn để phủ chuyên khoa của cả 10 nhóm bệnh trong dữ liệu bài viết, thêm sản khoa (thai kỳ) và nhi.

**Quy tắc:**
- **Số điện thoại chỉ lấy từ trang chính thức** của bệnh viện. Lý do: các trang tổng hợp (Medpro, Mytour, HelloBacsi…) ghi số khác nhau, và Bệnh viện Đại học Y Hà Nội từng công khai cảnh báo số điện thoại giả mạo. Sai số điện thoại trong app y tế có thể đưa người bệnh tới kẻ lừa đảo.
- Mỗi số ghi rõ loại (đặt lịch, cấp cứu, CSKH…). Trang chính thức không ghi số cấp cứu thì không suy ra.
- Chuyên khoa chỉ gán khi trang chính thức có khoa đó, hoặc suy ra từ tên bệnh viện chuyên khoa (Bệnh viện Tim, Bệnh viện Thận…). Bệnh viện đa khoa chưa xác nhận được khoa nào thì chỉ có nhãn `da_khoa`.
- Mỗi cơ sở có `verified`, `source_url`, `checked_at`, `note`.

| Trường | Ý nghĩa |
|---|---|
| `ownership` | `cong` / `tu` |
| `level` | `trung_uong` / `thanh_pho` / null (tư nhân) |
| `address` | Theo trang chính thức. Từ 1/7/2025 Hà Nội không còn cấp quận, nên địa chỉ ghi theo phường mới |
| `phones` | `[{label, number}]`, rỗng nếu chưa xác minh |
| `emergency` | `true` khi trang chính thức ghi có cấp cứu; `null` khi trang không ghi |
| `children_only` | Bệnh viện chỉ khám trẻ em (Nhi Trung ương) |
| `specialties` | Mã chuyên khoa, xem `SPECIALTIES` trong `facility_service.py` |

**7 cơ sở chưa xác minh** (`verified: false`, không có số điện thoại, giao diện hiện cảnh báo): Bệnh viện E, Đại học Y Hà Nội, Bệnh Nhiệt đới Trung ương, Phụ sản Trung ương, Phổi Hà Nội, Đức Giang, Thanh Nhàn. Lý do: không truy cập tự động được trang chính thức (lỗi chứng chỉ, không tìm ra tên miền). **Cần kiểm tra tay** rồi bổ sung.

**Cập nhật dữ liệu:** sửa file JSONL (một cơ sở một dòng), đổi `checked_at`, khởi động lại backend. Mã chuyên khoa lạ hoặc `id` trùng thì backend báo lỗi ngay khi nạp.

## Tìm kiếm: `app/services/facility_service.py`

Thuần code, không gọi Gemini. Dùng chung cho tool của agent và API.

`search_facilities(specialty, emergency=False, for_child=False, limit=5)`:
- Lấy cơ sở đúng chuyên khoa, cộng bệnh viện đa khoa làm dự phòng.
- `emergency`: chỉ lấy nơi có `emergency: true`. Bệnh viện ngoại khoa (Việt Đức) vẫn được tính.
- `for_child`: ưu tiên nơi có chuyên khoa nhi. Không phải trẻ em thì bỏ bệnh viện chỉ khám trẻ em.

**Thứ tự, trung lập, không xếp hạng "uy tín":**
1. Đúng chuyên khoa trước, đa khoa sau.
2. Trẻ em: nơi có chuyên khoa nhi trước.
3. Đã xác minh trước, chưa xác minh sau.
4. Còn lại theo thứ tự trong file (công trước, tư sau).

Vinmec và MEDLATEC vừa là nguồn bài viết vừa là bệnh viện tư; không được ưu tiên vì lý do đó.

Nhóm bệnh → chuyên khoa viết sẵn trong `DISEASE_GROUP_SPECIALTY`. Mỗi nhóm có ít nhất 3 cơ sở đúng chuyên khoa:

| Chuyên khoa | Nhóm bệnh | Số cơ sở đúng chuyên khoa |
|---|---|---|
| `tim_mach` | Tăng huyết áp, Suy tim | 7 |
| `tieu_hoa` | Viêm loét dạ dày | 6 |
| `co_xuong_khop` | Gout, Viêm khớp | 3 |
| `gan_truyen_nhiem` | Viêm gan B | 3 |
| `than_tiet_nieu` | Suy thận, Sỏi thận | 5 |
| `da_lieu` | Viêm da cơ địa | 6 |
| `ho_hap` | Hen phế quản | 7 |

**Lưu ý:** cơ xương khớp chỉ có 3 cơ sở đúng chuyên khoa (108, Hồng Ngọc, Thu Cúc), trong đó 2 là tư nhân. Nguyên nhân là chưa xác minh được khoa cơ xương khớp trên trang của các bệnh viện công khác (Bạch Mai có trung tâm cơ xương khớp nhưng trang chủ không liệt kê). Kiểm tra tay sẽ cân bằng lại.

## Agent

Tool `find_healthcare_facility(specialty, emergency?, for_child?)`, khai báo trong `app/rag/tools.py`.

```
"Tôi bị gout, nên khám ở đâu?"
  ▼
Gemini ─► find_healthcare_facility(specialty="co_xuong_khop")
  ▼
ToolSession._find_facility (code)
  ├─► session.facilities  ──► SSE "facilities" ──► thẻ trên giao diện, lưu vào messages.facilities
  └─► trả cho Gemini: chỉ TÊN các cơ sở + lời nhắc (không viết địa chỉ / số điện thoại,
      không nói nơi nào tốt nhất, nhắc gọi trước, cấp cứu thì 115 trước)
  ▼
Gemini: 1–3 câu dẫn
```

**Quy tắc trong system instruction:**
- Chỉ gọi khi người dùng hỏi nơi khám.
- Câu vừa hỏi bệnh vừa hỏi nơi khám thì gọi cả `search_medical_docs`.
- Người dùng ở tỉnh khác thì không gọi, nói rõ chỉ có dữ liệu Hà Nội.
- Cấp cứu: câu đầu là gọi 115, chỉ thêm `emergency=true` khi người dùng hỏi nơi cấp cứu.
- Không bao giờ tự nêu tên, địa chỉ, số điện thoại cơ sở y tế từ kiến thức riêng.

**Các chỗ đã sửa trong vòng lặp agent:**
- Chốt chặn "không có tài liệu" dùng `session.has_content` (có chunk **hoặc** có danh sách cơ sở). Trước đó câu dẫn sau khi gợi ý nơi khám sẽ bị chặn nhầm.
- `find_healthcare_facility` chỉ tính vào `AGENT_MAX_TOTAL_CALLS`, không tính vào 3 lần tìm tài liệu. Trước đó mọi tool khác `get_article` đều bị coi là lần tìm.
- Gọi nhiều lần (ví dụ hai chuyên khoa) thì gộp danh sách, không lặp cơ sở.
- Model không trả text mà đã có danh sách cơ sở thì dùng câu dẫn cố định, không rơi vào prompt "chưa có thông tin".

## API

| API | Mô tả |
|---|---|
| `GET /api/facilities?specialty=&emergency=&for_child=&limit=` | Danh sách thẻ, cùng logic với tool (cần đăng nhập) |
| `GET /api/facilities/specialties` | Mã chuyên khoa → tên, nhóm bệnh → chuyên khoa |

Bảng `messages` có thêm cột `facilities` (JSONB, migration `5d2a8c4e1b70`), lưu nguyên danh sách đã gợi ý để mở lại hội thoại vẫn thấy thẻ. `GET /api/conversations/{id}/messages` trả kèm trường này.

## Frontend

`components/FacilityCards.jsx` nằm trong bong bóng trả lời, trên phần nguồn tham khảo.
- Mỗi thẻ: tên, nhãn công/tư, cấp cứu, chỉ khám trẻ em; chuyên khoa; địa chỉ; số điện thoại (bấm để gọi); cảnh báo nếu chưa xác minh; nút **Chỉ đường** (Google Maps theo tên + địa chỉ, không cần API key) và **Website**.
- Cuối danh sách: "Danh sách tham khảo, không xếp hạng (kiểm tra ngày …). Gọi trước để xác nhận lịch khám. Cấp cứu: gọi 115."

## Chưa làm

- Lọc theo vị trí. Phạm vi chỉ có Hà Nội và 24 cơ sở nên lọc theo chuyên khoa là đủ. Muốn "gần nhất" thì cần toạ độ và vị trí trình duyệt.
- Nút "Tìm nơi khám" dưới mỗi câu trả lời (gợi ý chuyên khoa từ nguồn của câu trả lời).
- Kiểm tra tay 7 cơ sở chưa xác minh.

## Cài đặt

`cd backend`, rồi `python -m alembic upgrade head` (thêm cột `messages.facilities`). Quên bước này thì mọi request đọc tin nhắn báo `column messages.facilities does not exist`; chạy lệnh trên là hết, không cần khởi động lại backend.

## Đánh giá

Golden set thêm 8 câu loại `facility` (100 → 108 câu). Trường `expect_facility` chấm lời gọi tool: `null` nghĩa là không được gọi; dict nghĩa là phải có lời gọi khớp. Mọi câu còn cấm chuỗi giống số điện thoại trong câu trả lời (regex 8–11 chữ số, `115` không bị tính).

| Câu | Kiểm tra |
|---|---|
| `facility-01` | gout → `co_xuong_khop` |
| `facility-02` | câu nối tiếp về hen → `ho_hap` (lấy bệnh từ lịch sử) |
| `facility-03` | trẻ em bị hen → `ho_hap`, `for_child=true` |
| `facility-04` | người ở Đà Nẵng → **không** gọi, phải nhắc "Hà Nội" |
| `facility-05` | đau ngực, hỏi nơi cấp cứu → `emergency=true`, phải có "115" |
| `facility-06` | hỏi về bệnh, không hỏi nơi khám → **không** gọi |
| `facility-07` | vừa hỏi ăn nhạt vừa hỏi nơi khám → `tim_mach` (và tìm tài liệu) |
| `facility-08` | hồ sơ suy thận, "tôi nên khám ở đâu" → `than_tiet_nieu` |

Chạy: `python -m eval.eval_rag --agent --types facility --workers 1`.

### Kết quả (01/11/2026, agent, `gemini-3.1-flash-lite`)

| Lần | Gọi tool đúng | Không có số điện thoại tự bịa | Ghi chú |
|---|---|---|---|
| 1 | 7/8 | 7/7 | `facility-08` không gọi tool; 4/8 câu chép lại tên bệnh viện thành danh sách |
| 2 (sau khi sửa) | **8/8** | 7/7 | Không còn liệt kê lại tên; không câu nào rơi vào đường dự phòng |

Hai chỗ đã sửa sau lần 1:
- **Hỏi nơi khám mà không nêu bệnh** (`facility-08`: hồ sơ suy thận, "Tôi nên đi khám định kỳ ở bệnh viện nào?"). Agent không gọi tool, chốt chặn trả "chưa có thông tin về địa chỉ khám bệnh". Đã thêm quy tắc: lấy bệnh nền trong hồ sơ, không có thì `noi_tong_quat`, và vẫn phải gọi tool. Lần 2 agent gọi `than_tiet_nieu`, câu dẫn "Vì bạn có ghi bệnh nền suy thận trong hồ sơ…".
- **Model chép tên bệnh viện thành danh sách** trùng với thẻ, và giám khảo chấm danh sách đó là claim không có nguồn. Đã đổi kết quả tool: chỉ báo số lượng ("Đã tìm được 5 cơ sở ở Hà Nội (3 cơ sở có chuyên khoa Cơ xương khớp, 2 bệnh viện đa khoa)"), không gửi tên.

Hành vi đúng ở các câu khó:
- Cấp cứu (`facility-05`): câu đầu "Hãy gọi cấp cứu 115…", rồi mới tới danh sách nơi có cấp cứu.
- Người ở Đà Nẵng (`facility-04`): không gọi tool, nói rõ chỉ có dữ liệu Hà Nội.
- Hỏi về bệnh (`facility-06`): không gọi tool gợi ý nơi khám.

**Lưu ý về chỉ số:** faithfulness của nhóm này không có nhiều ý nghĩa, vì câu dẫn không dựa trên tài liệu bài viết. Ví dụ `facility-01` bị chấm 0 vì câu "nên khám chuyên khoa cơ xương khớp" không nằm trong context. Hai chỉ số đáng xem là `facility_ok` và việc không có số điện thoại trong câu trả lời.
