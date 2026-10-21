const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

/**
 * Gửi câu hỏi tới backend và nhận câu trả lời dạng stream (Server-Sent Events).
 * Dùng fetch thay vì EventSource vì EventSource chỉ gửi được GET.
 *
 * @param {string} message - Câu hỏi của người dùng.
 * @param {{role: "user"|"model", content: string}[]} history - Lịch sử hội thoại trước đó.
 * @param {object} handlers
 * @param {(stage: "thinking"|"rewriting"|"retrieving"|"reading"|"generating", data: object) => void} [handlers.onStatus]
 *   `data` là cả sự kiện; với agent, `retrieving` kèm `query` đang tìm.
 * @param {(sources: object[]) => void} [handlers.onSources]
 * @param {(text: string) => void} [handlers.onDelta] - Từng đoạn câu trả lời mới.
 * @returns {Promise<void>} Resolve khi nhận sự kiện `done`; reject khi lỗi.
 */
export async function streamChatMessage(message, history, { onStatus, onSources, onDelta } = {}) {
  const response = await fetch(`${API_BASE_URL}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, history }),
  });

  if (!response.ok) {
    const errorBody = await response.json().catch(() => null);
    const detail = errorBody?.detail || `Lỗi máy chủ (${response.status})`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }

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

      if (event === "status") onStatus?.(data.stage, data);
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
