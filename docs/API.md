# API backend

Swagger UI khi backend đang chạy: http://localhost:8000/docs (nút **Authorize** để dán token).

Mọi API trừ đăng ký / đăng nhập đều **bắt buộc đăng nhập**: gửi header `Authorization: Bearer <access_token>`. Thiếu, sai hoặc hết hạn token thì nhận `401`. `GET /` trả về `{"status": "ok"}` để kiểm tra server còn sống.

## Đăng nhập

| API | Body | Trả về |
|---|---|---|
| `POST /api/auth/register` | `{ email, password (8–128 ký tự), display_name }` | `201` thông tin user. Email đã có thì `409`, sai định dạng thì `422` |
| `POST /api/auth/login` | `{ email, password }` | `{ access_token, token_type: "bearer", user }`. Sai email hoặc mật khẩu thì `401` |
| `GET /api/auth/me` | (không có) | Thông tin user của token: `{ id, email, display_name, created_at }` |

## Hội thoại

| API | Ý nghĩa |
|---|---|
| `GET /api/conversations` | Danh sách hội thoại của user, mới nhất trước: `[{ id, title, created_at, updated_at }]` |
| `GET /api/conversations/{id}/messages` | Tin nhắn theo thứ tự: `[{ id, role: "user"\|"model", content, sources, facilities, created_at }]` |
| `PATCH /api/conversations/{id}` | Đổi tên, body `{ title }` |
| `DELETE /api/conversations/{id}` | Xóa hội thoại kèm toàn bộ tin nhắn (`204`) |

Hội thoại của user khác trả `404`. Không có API tạo hội thoại: hội thoại được tạo khi gửi câu hỏi đầu tiên.

## Hồ sơ sức khỏe

| API | Ý nghĩa |
|---|---|
| `GET /api/profile` | Hồ sơ của user: `{ birth_year, pregnant, conditions, allergies, use_in_chat, updated_at }`. Chưa có thì hồ sơ rỗng, `updated_at: null` |
| `PUT /api/profile` | Lưu cả hồ sơ (tạo hoặc ghi đè), body như trên trừ `updated_at` |
| `DELETE /api/profile` | Xóa hồ sơ (`204`) |
| `GET /api/profile/options` | `{ conditions }`: các nhóm bệnh có trong dữ liệu, để gợi ý ở form |

Chi tiết: [HEALTH_PROFILE.md](HEALTH_PROFILE.md).

## Gợi ý nơi khám

| API | Ý nghĩa |
|---|---|
| `GET /api/facilities?specialty=&emergency=&for_child=&limit=` | Bệnh viện ở Hà Nội theo chuyên khoa, cùng logic với tool của agent |
| `GET /api/facilities/specialties` | Mã chuyên khoa → tên, nhóm bệnh → chuyên khoa |

Chi tiết: [FACILITIES.md](FACILITIES.md).

## `POST /api/chat`

Request:

```json
{
  "message": "Triệu chứng của cơn gout cấp là gì?",
  "conversation_id": "9f3a…"
}
```

Response:

```json
{
  "reply": "…",
  "sources": [
    { "title": "…", "url": "https://…", "citation": "Theo Báo Sức khỏe & Đời sống - …" }
  ],
  "facilities": [],
  "disclaimer": "Thông tin trên chỉ mang tính tham khảo, không thay thế …"
}
```

- `conversation_id` để trống (hoặc `null`) ở câu hỏi đầu tiên thì server tạo hội thoại mới, tiêu đề lấy từ câu hỏi.
- Server tự đọc lịch sử (`HISTORY_MAX_TURNS` lượt gần nhất) và hồ sơ sức khỏe (nếu user bật "dùng trong chat") từ DB. Client **không gửi** `history`.
- `facilities`: thẻ bệnh viện agent gợi ý khi người dùng hỏi nơi khám, rỗng ở các câu khác.
- Trả lời xong, server lưu câu hỏi và câu trả lời (kèm `sources`, `facilities`) vào DB. Lỗi giữa chừng thì không lưu gì.
- Lỗi: `401` khi chưa đăng nhập, `404` khi hội thoại không thuộc user, `500` khi cấu hình sai (vd. thiếu API key), `502` khi gọi Gemini thất bại (mạng, quota, key sai, model không tồn tại).

## `POST /api/chat/stream`

Cùng request với `/api/chat`, nhưng trả về **Server-Sent Events** để hiển thị câu trả lời dần dần (frontend dùng endpoint này). Các sự kiện theo thứ tự:

| Sự kiện | `data` | Ý nghĩa |
|---|---|---|
| `conversation` | `{"id": "…", "title": "…"}` | Luôn đến đầu tiên: id của hội thoại (id mới nếu vừa tạo) |
| `status` | `{"stage": "thinking" \| "retrieving" \| "reading" \| "finding_facility" \| "generating", …}` | Bước đang chạy. Agent gửi kèm `query` khi tìm, `document_id` khi đọc bài |
| `sources` | `{"sources": [{title, url, citation}]}` | Nguồn tham khảo. Agent gửi lại sau mỗi lần gọi tool, mỗi lần là danh sách đầy đủ |
| `facilities` | `{"facilities": [{name, address, phones, maps_url, …}]}` | Chỉ khi agent gợi ý nơi khám: thẻ bệnh viện, danh sách đầy đủ tới lúc đó |
| `delta` | `{"text": "…"}` | Một đoạn câu trả lời. Nối các `delta` lại thành câu trả lời đầy đủ |
| `done` | `{"disclaimer": "…"}` | Kết thúc, lượt chat đã được lưu |
| `error` | `{"detail": "…"}` | Lỗi khi stream đã bắt đầu (HTTP status lúc đó đã là 200). Lượt này không được lưu |

Lỗi `401` / `404` / `500` (chưa đăng nhập, hội thoại không thuộc user, thiếu API key…) vẫn trả về bằng HTTP status trước khi mở stream.
