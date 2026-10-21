import { useEffect, useRef, useState } from "react";
import "./App.css";
import Sidebar from "./components/Sidebar";
import MessageBubble from "./components/MessageBubble";
import ChatInput from "./components/ChatInput";
import { streamChatMessage } from "./api/chatApi";
import {
  createSession,
  deriveTitle,
  loadSessions,
  saveSessions,
} from "./utils/sessions";

function App() {
  const [sessions, setSessions] = useState(() => {
    const saved = loadSessions();
    return saved?.sessions ?? [createSession()];
  });
  const [activeSessionId, setActiveSessionId] = useState(() => {
    const saved = loadSessions();
    return saved?.activeSessionId ?? sessions[0].id;
  });
  const [isLoading, setIsLoading] = useState(false);
  const messagesEndRef = useRef(null);

  const activeSession =
    sessions.find((session) => session.id === activeSessionId) ?? sessions[0];

  useEffect(() => {
    saveSessions(sessions, activeSessionId);
  }, [sessions, activeSessionId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeSession?.messages, isLoading]);

  function updateSessionMessages(sessionId, updater) {
    setSessions((prev) =>
      prev.map((session) =>
        session.id === sessionId
          ? { ...session, messages: updater(session.messages) }
          : session
      )
    );
  }

  async function handleSend(text) {
    const sessionId = activeSession.id;
    const userMessage = { role: "user", content: text };
    // Bỏ tin nhắn lỗi / câu trả lời rỗng (bị ngắt) khỏi lịch sử gửi cho backend.
    const history = activeSession.messages
      .filter((m) => !m.isError && m.content)
      .map(({ role, content }) => ({ role, content }));

    setSessions((prev) =>
      prev.map((session) => {
        if (session.id !== sessionId) return session;
        const isFirstUserMessage = !session.messages.some((m) => m.role === "user");
        return {
          ...session,
          title: isFirstUserMessage ? deriveTitle(text) : session.title,
          // Bong bóng trả lời rỗng được thêm ngay, rồi điền dần khi stream về.
          messages: [
            ...session.messages,
            userMessage,
            { role: "model", content: "", sources: [], pending: true, status: "retrieving" },
          ],
        };
      })
    );
    setIsLoading(true);

    // Chỉ sửa tin nhắn cuối (bong bóng đang stream) của session đã gửi câu hỏi.
    const updateReply = (updater) =>
      updateSessionMessages(sessionId, (msgs) => [...msgs.slice(0, -1), updater(msgs.at(-1))]);

    try {
      await streamChatMessage(text, history, {
        onStatus: (stage, data) => updateReply((m) => ({ ...m, status: stage, statusQuery: data.query })),
        onSources: (sources) => updateReply((m) => ({ ...m, sources })),
        onDelta: (delta) => updateReply((m) => ({ ...m, content: m.content + delta })),
      });
      updateReply(({ pending: _pending, status: _status, statusQuery: _statusQuery, ...m }) => m);
    } catch (error) {
      updateReply(({ pending: _pending, status: _status, statusQuery: _statusQuery, ...m }) =>
        m.content
          ? // Đã có một phần câu trả lời: giữ lại, ghi chú bị ngắt.
            { ...m, content: `${m.content}\n\n*(Câu trả lời bị gián đoạn: ${error.message})*` }
          : { ...m, content: `Đã có lỗi xảy ra: ${error.message}`, isError: true, sources: [] }
      );
    } finally {
      setIsLoading(false);
    }
  }

  function handleNewSession() {
    const session = createSession();
    setSessions((prev) => [session, ...prev]);
    setActiveSessionId(session.id);
  }

  function handleDeleteSession(sessionId) {
    setSessions((prev) => {
      const next = prev.filter((session) => session.id !== sessionId);
      if (sessionId === activeSessionId && next.length > 0) {
        setActiveSessionId(next[0].id);
      }
      return next;
    });
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>🩺 ChatBot Y Tế</h1>
        <p>Hỏi đáp thông tin sức khỏe cùng trợ lý ảo (Gemini AI)</p>
      </header>

      <div className="app-body">
        <Sidebar
          sessions={sessions}
          activeSessionId={activeSession?.id}
          onSelectSession={setActiveSessionId}
          onNewSession={handleNewSession}
          onDeleteSession={handleDeleteSession}
        />

        <div className="app-main">
          <main className="chat-window">
            {activeSession?.messages.map((message, index) => (
              <MessageBubble
                key={index}
                role={message.role}
                content={message.content}
                isError={message.isError}
                sources={message.sources}
                pending={message.pending}
                status={message.status}
                statusQuery={message.statusQuery}
              />
            ))}
            <div ref={messagesEndRef} />
          </main>

          <ChatInput onSend={handleSend} disabled={isLoading} />
        </div>
      </div>

      <footer className="app-footer">
        Thông tin do chatbot cung cấp chỉ mang tính tham khảo, không thay thế
        cho tư vấn, chẩn đoán của bác sĩ hoặc chuyên gia y tế.
      </footer>
    </div>
  );
}

export default App;
