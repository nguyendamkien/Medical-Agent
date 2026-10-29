# Cấu trúc Frontend (React + Vite)

Người dùng phải **đăng nhập** mới chat được. Lịch sử hội thoại lưu ở backend (PostgreSQL), không lưu trong trình duyệt. Trình duyệt chỉ giữ token đăng nhập. Phía backend xem [../docs/AUTH_CHAT_HISTORY.md](../docs/AUTH_CHAT_HISTORY.md).

## Cây thư mục

```
frontend/
├── index.html                     # HTML gốc, chứa <div id="root"> để React render vào
├── vite.config.js                 # Cấu hình build tool Vite
├── .env.example                   # Mẫu cấu hình (VITE_API_BASE_URL trỏ tới backend)
└── src/
    ├── main.jsx                   # Điểm khởi động: render <App /> vào DOM
    ├── App.jsx                    # Component gốc: trạng thái đăng nhập, hội thoại, tin nhắn, gửi câu hỏi
    ├── App.css                    # Style cho toàn bộ layout (header/sidebar/chat/đăng nhập/footer)
    ├── index.css                  # CSS reset tối giản, áp cho toàn trang
    ├── api/
    │   └── chatApi.js             # Mọi lời gọi backend: token, đăng nhập, hội thoại, chat stream (SSE)
    ├── components/
    │   ├── AuthScreen.jsx         # Màn đăng nhập / đăng ký
    │   ├── Sidebar.jsx            # Danh sách hội thoại, tạo mới / đổi tên / xoá, tên user + đăng xuất
    │   ├── MessageBubble.jsx      # 1 bong bóng chat; câu trả lời của bot render markdown (react-markdown)
    │   └── ChatInput.jsx          # Ô nhập tin nhắn + nút Gửi
    └── utils/
        └── sessions.js            # Lời chào mặc định + đổi tin nhắn từ API sang dạng hiển thị
```

## Cây component

```
main.jsx
  └── App.jsx            (state: user, conversations[], activeId, messagesById{}, isSending...)
        ├── <header>
        ├── user === undefined → "Đang tải…" (đang hỏi /api/auth/me), hoặc lỗi kết nối + nút Thử lại
        ├── user === null      → <AuthScreen>   đăng nhập / đăng ký → onLoggedIn(user)
        ├── user là object     →
        │     ├── <Sidebar>       hội thoại + user; callback chọn / tạo / đổi tên / xoá / đăng xuất
        │     ├── <main class="chat-window">
        │     │     └── <MessageBubble> × N   (tin nhắn của hội thoại đang mở, hoặc lời chào)
        │     └── <ChatInput>     onSend(text) → App.handleSend
        └── <footer>             disclaimer y tế cố định
```

## Đăng nhập

```
Mở trang
  ├─ Không có token trong localStorage → màn đăng nhập
  └─ Có token → GET /api/auth/me
        ├─ 200 → vào giao diện chat, tải GET /api/conversations cho sidebar
        ├─ 401 (token hết hạn) → xoá token, màn đăng nhập + thông báo "phiên đã hết hạn"
        └─ không tới được backend → thông báo lỗi + nút Thử lại

Đăng nhập: POST /api/auth/login → lưu access_token vào localStorage (key "chatbot-y-te-token")
Đăng ký:   POST /api/auth/register → tự đăng nhập luôn
Đăng xuất: xoá token + xoá dữ liệu đang hiện

Bất kỳ request nào bị 401 khi đang có token → chatApi phát sự kiện "auth:logout"
→ App xoá dữ liệu và về màn đăng nhập.
```

## Luồng dữ liệu khi gửi 1 tin nhắn

```
Người dùng gõ câu hỏi, bấm Gửi
    ▼
App.handleSend(text)
    │  1. Thêm { role: "user" } và bong bóng trả lời rỗng { pending: true } vào messagesById[key]
    │     key = id hội thoại đang mở, hoặc "draft" nếu là cuộc mới chưa gửi câu nào
    │  2. Gọi streamChatMessage(text, activeId)   (activeId = null ở cuộc mới)
    ▼
chatApi.streamChatMessage   POST /api/chat/stream  { message, conversation_id }  + header Authorization
    │   conversation → { id, title }: cuộc mới thì chuyển tin nhắn "draft" sang id thật, thêm vào
    │                  đầu sidebar. Cuộc cũ thì đẩy lên đầu sidebar
    │   status       → bong bóng hiện "Đang tìm tài liệu… / Đang soạn…"
    │   sources      → lưu nguồn trích dẫn
    │   delta        → nối thêm chữ (render markdown + con trỏ nhấp nháy)
    │   done         → bỏ cờ pending.   error → bong bóng lỗi
    ▼
Backend tự lưu câu hỏi + câu trả lời vào DB khi trả lời xong (lỗi giữa chừng thì không lưu),
nên frontend không phải gửi lịch sử hay tự lưu gì.
```

## Giải thích từng file

### `src/App.jsx`
Giữ toàn bộ **state chính**:
- `user`: `undefined` (đang kiểm tra), `null` (chưa đăng nhập) hoặc thông tin user.
- `conversations`: danh sách cho sidebar, lấy từ `GET /api/conversations`.
- `activeId`: hội thoại đang mở. `null` là cuộc mới chưa gửi câu nào.
- `messagesById`: cache tin nhắn theo id hội thoại (key `"draft"` cho cuộc mới). Mở một hội thoại lần đầu thì tải từ `GET /api/conversations/{id}/messages`, các lần sau dùng cache.
- `isSending`: đang chờ câu trả lời. Khi đó khóa ô nhập, nút tạo mới, đổi tên, xóa, đăng xuất. Vẫn cho chọn hội thoại khác để xem.

Các hàm chính: `handleSend`, `handleSelect`, `handleNew`, `handleRename` (dùng `window.prompt`), `handleDelete` (hỏi xác nhận), `handleLoggedIn`, `handleLogout`.

### `src/api/chatApi.js`
Nơi **duy nhất** gọi ra backend. Đọc `API_BASE_URL` từ `VITE_API_BASE_URL` (mặc định `http://localhost:8000`).
- `apiFetch()`: tự gắn `Authorization: Bearer <token>`. Lỗi HTTP thì ném `Error` với `detail` của backend; lỗi 422 được dịch sang tiếng Việt. Gặp 401 khi có token thì xóa token và phát `auth:logout`. Không tới được máy chủ thì báo "Không kết nối được máy chủ".
- Auth: `register`, `login` (lưu token), `fetchMe`, `getToken`, `clearToken`.
- Hội thoại: `listConversations`, `listMessages`, `renameConversation`, `deleteConversation`.
- `streamChatMessage(message, conversationId, handlers)`: dùng `fetch` + `ReadableStream` thay cho `EventSource`, vì `EventSource` chỉ gửi được GET và không gắn được header token.

### `src/components/AuthScreen.jsx`
Form đăng nhập và đăng ký (chuyển qua lại bằng nút dưới form). Hiện lỗi từ backend (sai mật khẩu, email đã đăng ký…) và thông báo "phiên đã hết hạn" nếu có.

### `src/components/Sidebar.jsx`
Component chỉ nhận props và gọi callback. Hiện danh sách hội thoại; nút ✎ (đổi tên) và × (xóa) chỉ hiện khi rê chuột vào hoặc ở mục đang mở. Cuối sidebar hiện tên, email và nút Đăng xuất.

### `src/components/MessageBubble.jsx`, `ChatInput.jsx`
Không đổi: bong bóng chat (markdown, nguồn trích dẫn, trạng thái chờ) và ô nhập.

### `src/utils/sessions.js`
- `WELCOME_MESSAGE`: lời chào hiện ở hội thoại chưa có tin nhắn. Chỉ hiển thị, không lưu vào DB.
- `fromApiMessage()`: đổi tin nhắn từ API sang dạng `MessageBubble` dùng (`sources` null thành `[]`).
- `clearLegacySessions()`: xóa lịch sử chat cũ còn trong `localStorage` (key `chatbot-y-te-sessions`, thời chưa có đăng nhập). `App.jsx` gọi hàm này mỗi khi mở trang. Dữ liệu cũ này không được chuyển lên DB.

## Vì sao tách như vậy?

- **State tập trung ở `App.jsx`**: các component con chỉ nhận props và gọi callback, nên sidebar và khung chat luôn khớp nhau.
- **`api/` tách riêng**: cách gọi backend (token, xử lý 401, định dạng lỗi) nằm gọn trong `chatApi.js`, không lẫn vào UI.
- **Backend là nơi lưu dữ liệu**: frontend không tự lưu lịch sử, nên đăng nhập ở trình duyệt hay máy khác vẫn thấy đúng lịch sử.
