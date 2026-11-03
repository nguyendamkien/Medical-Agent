# Frontend — ChatBot Y Tế

Giao diện chat viết bằng React 19 + Vite. Gọi các API `/api/auth/*`, `/api/conversations*`, `/api/profile*` và `POST /api/chat/stream` của backend (xem [docs/API.md](../docs/API.md)). Chi tiết code: [STRUCTURE.md](STRUCTURE.md).

## Tính năng

- Đăng ký / đăng nhập. Phải đăng nhập mới chat được. Token lưu trong `localStorage`; hết hạn thì tự về màn đăng nhập.
- Chat hỏi đáp y tế, câu trả lời hiện dần (stream), kèm danh sách nguồn tham khảo (link bài gốc).
- Lịch sử hội thoại lưu ở server (PostgreSQL) theo tài khoản: sidebar liệt kê các cuộc trò chuyện, có đổi tên và xóa. Đăng nhập ở trình duyệt khác vẫn thấy đủ.
- Chatbot nhớ ngữ cảnh trong một cuộc trò chuyện: server tự đọc lịch sử từ DB, frontend chỉ gửi `conversation_id`.
- Hồ sơ sức khỏe: nút "Hồ sơ sức khỏe" ở sidebar mở form (năm sinh, bệnh nền, dị ứng thuốc, mang thai, bật / tắt dùng trong chat). Dòng tóm tắt phía trên ô chat cho biết hồ sơ đang được dùng ([docs/HEALTH_PROFILE.md](../docs/HEALTH_PROFILE.md)).
- Gợi ý nơi khám: hỏi "nên khám ở đâu" thì câu trả lời kèm thẻ bệnh viện ở Hà Nội (địa chỉ, số điện thoại bấm để gọi, nút chỉ đường, cảnh báo nếu chưa xác minh). Mở lại hội thoại vẫn thấy thẻ ([docs/FACILITIES.md](../docs/FACILITIES.md)).

## Chạy

```bash
npm install
copy .env.example .env    # tuỳ chọn; macOS/Linux: cp .env.example .env
npm run dev               # http://localhost:5173
```

Cần backend đang chạy (mặc định `http://localhost:8000`). Nếu backend ở địa chỉ khác, sửa trong `.env`:

```
VITE_API_BASE_URL=http://localhost:8000
```

## Lệnh

| Lệnh | Tác dụng |
|---|---|
| `npm run dev` | Chạy dev server có HMR |
| `npm run build` | Build production ra thư mục `dist/` |
| `npm run preview` | Chạy thử bản build |
| `npm run lint` | Kiểm tra code bằng Oxlint |

## Cấu trúc

```
src/
├── App.jsx                  # Trạng thái đăng nhập, danh sách hội thoại, tin nhắn, gửi câu hỏi
├── api/chatApi.js           # Gọi backend: token, đăng nhập, hội thoại, hồ sơ, streamChatMessage(message, conversationId)
├── components/
│   ├── AuthScreen.jsx       # Màn đăng nhập / đăng ký
│   ├── Sidebar.jsx          # Danh sách cuộc trò chuyện, đổi tên / xóa, nút hồ sơ, tên user + đăng xuất
│   ├── ProfileModal.jsx     # Form hồ sơ sức khỏe
│   ├── MessageBubble.jsx    # Một tin nhắn + thẻ nơi khám + nguồn tham khảo
│   ├── FacilityCards.jsx    # Thẻ bệnh viện agent gợi ý
│   └── ChatInput.jsx        # Ô nhập câu hỏi
└── utils/sessions.js        # Lời chào, đổi tin nhắn từ API sang dạng hiển thị
```
