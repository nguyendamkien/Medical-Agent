import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Link trong câu trả lời của bot mở tab mới, không rời khỏi trang chat.
const MARKDOWN_COMPONENTS = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
};

// Trạng thái backend gửi về trong lúc chưa có chữ nào (xem api/chatApi.js).
const STATUS_TEXT = {
  thinking: "Đang phân tích câu hỏi…",
  rewriting: "Đang phân tích câu hỏi…",
  retrieving: "Đang tìm tài liệu liên quan…",
  reading: "Đang đọc bài viết…",
  generating: "Đang soạn câu trả lời…",
};

export default function MessageBubble({ role, content, isError, sources = [], pending, status, statusQuery }) {
  const isUser = role === "user";
  const isWaiting = pending && !content;
  // Câu trả lời của Gemini là markdown (**đậm**, danh sách…); tin nhắn người dùng và lỗi giữ nguyên văn.
  const renderMarkdown = !isUser && !isError;

  return (
    <div className={`message-row ${isUser ? "message-row--user" : "message-row--bot"}`}>
      <div
        className={[
          "message-bubble",
          isUser ? "message-bubble--user" : "message-bubble--bot",
          isError ? "message-bubble--error" : "",
          renderMarkdown ? "message-bubble--markdown" : "",
          pending && content ? "message-bubble--streaming" : "",
        ].join(" ")}
      >
        {isWaiting ? (
          <span className="message-status">
            {/* Agent gửi kèm truy vấn đang tìm (có thể tìm nhiều lần cho một câu hỏi). */}
            {status === "retrieving" && statusQuery
              ? `Đang tìm: “${statusQuery}”…`
              : (STATUS_TEXT[status] ?? STATUS_TEXT.generating)}
          </span>
        ) : renderMarkdown ? (
          <div className="message-content">
            <Markdown remarkPlugins={[remarkGfm]} components={MARKDOWN_COMPONENTS}>
              {content}
            </Markdown>
          </div>
        ) : (
          content
        )}
        {/* Nguồn về trước chữ — chờ có câu trả lời rồi mới hiện để không nằm lơ lửng. */}
        {!isUser && !isWaiting && sources.length > 0 && (
          <div className="message-sources">
            <strong>Nguồn tham khảo</strong>
            {/* Thứ tự khớp với "[Nguồn i]" trong câu trả lời — backend đánh số theo bài. */}
            <ol>
              {sources.map((source, index) => (
                <li key={index}>
                  {source.url ? (
                    <a href={source.url} target="_blank" rel="noreferrer">
                      {source.title || source.url}
                    </a>
                  ) : (
                    source.title
                  )}
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>
    </div>
  );
}
