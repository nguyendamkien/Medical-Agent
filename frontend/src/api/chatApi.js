const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

// ---------- Token đăng nhập ----------
// Token (JWT) nhận được khi đăng nhập, lưu trong localStorage để tải lại trang vẫn còn đăng nhập.
// Hết hạn sau JWT_EXPIRE_DAYS ngày (backend) → API trả 401 → apiFetch xoá token, App về màn đăng nhập.

const TOKEN_KEY = "chatbot-y-te-token";

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null; // localStorage không khả dụng (chế độ ẩn danh chặn...)
  }
}

function setToken(token) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // bỏ qua: không lưu được thì các request sau không có token → phải đăng nhập lại.
  }
}

export function clearToken() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // bỏ qua
  }
}

/** Phát khi token không còn hợp lệ; App.jsx lắng nghe để quay về màn đăng nhập. */
export const LOGOUT_EVENT = "auth:logout";

/**
 * fetch tới backend, tự gắn header Authorization nếu đã đăng nhập.
 * - Lỗi HTTP → ném Error với thông báo `detail` của backend.
 * - 401 khi đang có token (token hết hạn / sai) → xoá token và phát LOGOUT_EVENT.
 *   (401 khi CHƯA có token, ví dụ sai mật khẩu lúc đăng nhập, chỉ ném lỗi.)
 */
async function apiFetch(path, options = {}) {
  const token = getToken();
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
    });
  } catch {
    // fetch chỉ ném lỗi khi không tới được máy chủ (backend tắt, mất mạng, CORS).
    throw new Error("Không kết nối được máy chủ. Hãy kiểm tra backend đã chạy chưa.");
  }

  if (response.status === 401 && token) {
    clearToken();
    window.dispatchEvent(new Event(LOGOUT_EVENT));
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(formatDetail(body?.detail) || `Lỗi máy chủ (${response.status})`);
  }
  return response;
}

/** `detail` của FastAPI là chuỗi, hoặc danh sách lỗi validate (422) → gộp thành một câu dễ đọc. */
function formatDetail(detail) {
  if (!detail) return "";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = item.loc?.at(-1);
        if (field === "email") return "Email không hợp lệ.";
        if (field === "password") return "Mật khẩu phải có từ 8 đến 128 ký tự.";
        if (field === "display_name") return "Tên hiển thị không được để trống.";
        return item.msg;
      })
      .join(" ");
  }
  return JSON.stringify(detail);
}

// ---------- Đăng ký / đăng nhập ----------

export async function register(email, password, displayName) {
  await apiFetch("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, display_name: displayName }),
  });
}

/** Đăng nhập, lưu token, trả về thông tin user { id, email, display_name, created_at }. */
export async function login(email, password) {
  const response = await apiFetch("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  const data = await response.json();
  setToken(data.access_token);
  return data.user;
}

/** Thông tin user của token hiện tại (dùng khi mở trang để biết còn đăng nhập không). */
export async function fetchMe() {
  return (await apiFetch("/api/auth/me")).json();
}

// ---------- Hội thoại (sidebar) ----------

/** [{ id, title, created_at, updated_at }], mới nhất trước. */
export async function listConversations() {
  return (await apiFetch("/api/conversations")).json();
}

/** [{ id, role: "user"|"model", content, sources, created_at }] theo thứ tự gửi. */
export async function listMessages(conversationId) {
  return (await apiFetch(`/api/conversations/${conversationId}/messages`)).json();
}

export async function renameConversation(conversationId, title) {
  const response = await apiFetch(`/api/conversations/${conversationId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
  return response.json();
}

export async function deleteConversation(conversationId) {
  await apiFetch(`/api/conversations/${conversationId}`, { method: "DELETE" });
}

// ---------- Chat ----------

/**
 * Gửi câu hỏi tới backend và nhận câu trả lời dạng stream (Server-Sent Events).
 * Dùng fetch thay vì EventSource vì EventSource chỉ gửi được GET (và không gắn được header token).
 * Lịch sử hội thoại do backend tự đọc từ DB theo `conversationId`; câu hỏi + câu trả lời được backend
 * lưu khi trả lời xong.
 *
 * @param {string} message - Câu hỏi của người dùng.
 * @param {string|null} conversationId - null ở câu hỏi đầu tiên của cuộc mới → backend tạo hội thoại.
 * @param {object} handlers
 * @param {(conversation: {id: string, title: string}) => void} [handlers.onConversation]
 *   Sự kiện đầu tiên: id + tiêu đề hội thoại (id mới nếu vừa tạo).
 * @param {(stage: "thinking"|"rewriting"|"retrieving"|"reading"|"generating", data: object) => void} [handlers.onStatus]
 *   `data` là cả sự kiện; với agent, `retrieving` kèm `query` đang tìm.
 * @param {(sources: object[]) => void} [handlers.onSources]
 * @param {(text: string) => void} [handlers.onDelta] - Từng đoạn câu trả lời mới.
 * @returns {Promise<void>} Resolve khi nhận sự kiện `done`; reject khi lỗi.
 */
export async function streamChatMessage(
  message,
  conversationId,
  { onConversation, onStatus, onSources, onDelta } = {}
) {
  const response = await apiFetch("/api/chat/stream", {
    method: "POST",
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;

    // Mỗi sự kiện SSE kết thúc bằng một dòng trống.
    let boundary;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const { event, data } = parseEvent(buffer.slice(0, boundary));
      buffer = buffer.slice(boundary + 2);

      if (event === "conversation") onConversation?.(data);
      else if (event === "status") onStatus?.(data.stage, data);
      else if (event === "sources") onSources?.(data.sources);
      else if (event === "delta") onDelta?.(data.text);
      else if (event === "error") throw new Error(data.detail);
      else if (event === "done") return;
    }
  }
  throw new Error("Kết nối bị ngắt trước khi nhận đủ câu trả lời.");
}

function parseEvent(block) {
  let event = "message";
  const dataLines = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  return { event, data: dataLines.length ? JSON.parse(dataLines.join("\n")) : {} };
}
