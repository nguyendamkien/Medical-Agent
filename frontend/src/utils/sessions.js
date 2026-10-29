// Lịch sử chat giờ lưu trong PostgreSQL ở backend (xem api/chatApi.js), không còn trong localStorage.

/** Lời chào hiển thị ở hội thoại chưa có tin nhắn nào. Chỉ hiển thị, không lưu vào DB. */
export const WELCOME_MESSAGE = {
  role: "model",
  content:
    "Xin chào! Tôi là trợ lý ảo hỗ trợ hỏi đáp thông tin y tế. Bạn có thể hỏi tôi về " +
    "triệu chứng, bệnh lý phổ biến, cách chăm sóc sức khỏe... Lưu ý: thông tin chỉ mang " +
    "tính tham khảo, không thay thế chẩn đoán của bác sĩ.",
};

/** Key thời lịch sử chat còn lưu trong localStorage (trước khi có đăng nhập). */
const LEGACY_SESSIONS_KEY = "chatbot-y-te-sessions";

/** Xoá lịch sử chat cũ còn nằm trong localStorage của trình duyệt (không còn dùng). */
export function clearLegacySessions() {
  try {
    localStorage.removeItem(LEGACY_SESSIONS_KEY);
  } catch {
    // localStorage không khả dụng, bỏ qua.
  }
}

/** Đổi tin nhắn từ API ({ id, role, content, sources, created_at }) sang dạng MessageBubble dùng. */
export function fromApiMessage({ role, content, sources }) {
  return { role, content, sources: sources ?? [] };
}
