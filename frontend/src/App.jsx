import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";
import AuthScreen from "./components/AuthScreen";
import Sidebar from "./components/Sidebar";
import MessageBubble from "./components/MessageBubble";
import ChatInput from "./components/ChatInput";
import {
  LOGOUT_EVENT,
  clearToken,
  deleteConversation,
  fetchMe,
  getToken,
  listConversations,
  listMessages,
  renameConversation,
  streamChatMessage,
} from "./api/chatApi";
import { WELCOME_MESSAGE, clearLegacySessions, fromApiMessage } from "./utils/sessions";

// Key tạm cho tin nhắn của cuộc trò chuyện mới, dùng cho tới khi backend trả về id thật
// (sự kiện `conversation` ở đầu stream).
const DRAFT = "draft";

/** Bỏ các cờ tạm của bong bóng đang stream. */
const stripPending = ({ pending: _pending, status: _status, statusQuery: _statusQuery, ...m }) => m;

function App() {
  // undefined = đang kiểm tra token lúc mở trang; null = chưa đăng nhập; object = đã đăng nhập.
  const [user, setUser] = useState(undefined);
  const [bootError, setBootError] = useState(""); // không kết nối được backend lúc mở trang
  const [authNotice, setAuthNotice] = useState(""); // vd "phiên đăng nhập đã hết hạn"

  // Sidebar: danh sách hội thoại từ GET /api/conversations (mới nhất trước).
  const [conversations, setConversations] = useState([]);
  const [isLoadingList, setIsLoadingList] = useState(false);
  const [listError, setListError] = useState("");

  // Hội thoại đang mở. null = cuộc mới chưa gửi câu nào (tin nhắn nằm ở messagesById[DRAFT]).
  const [activeId, setActiveId] = useState(null);
  // Cache tin nhắn đã tải: { [conversationId | DRAFT]: messages[] }. Chỉ tải từ API lần đầu mở.
  const [messagesById, setMessagesById] = useState({});
  const [loadingMessagesId, setLoadingMessagesId] = useState(null);
  const [messagesError, setMessagesError] = useState(null); // { id, message }
  const [isSending, setIsSending] = useState(false);
  const messagesEndRef = useRef(null);

  const activeKey = activeId ?? DRAFT;
  const activeMessages = messagesById[activeKey];
  const isLoadingMessages = activeId !== null && loadingMessagesId === activeId;
  const activeError = messagesError?.id === activeId ? messagesError.message : "";

  // ---------- Đăng nhập ----------

  /** Mở trang: có token thì hỏi /me xem còn đăng nhập không. */
  const checkSession = useCallback(async () => {
    setBootError("");
    if (!getToken()) {
      setUser(null);
      return;
    }
    try {
      setUser(await fetchMe());
    } catch (err) {
      // Token hết hạn → apiFetch đã xoá token (và phát LOGOUT_EVENT) → về màn đăng nhập.
      // Còn token mà vẫn lỗi = không tới được backend → hiện lỗi + nút thử lại.
      if (getToken()) setBootError(err.message);
      else setUser(null);
    }
  }, []);

  useEffect(() => {
    clearLegacySessions(); // lịch sử chat cũ trong trình duyệt, nay đã chuyển sang lưu ở backend
    checkSession();
  }, [checkSession]);

  // Bất kỳ API nào trả 401 (token hết hạn) → xoá dữ liệu đang hiện, về màn đăng nhập.
  useEffect(() => {
    function handleExpired() {
      setConversations([]);
      setMessagesById({});
      setActiveId(null);
      setUser(null);
      setAuthNotice("Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.");
    }
    window.addEventListener(LOGOUT_EVENT, handleExpired);
    return () => window.removeEventListener(LOGOUT_EVENT, handleExpired);
  }, []);

  function handleLoggedIn(loggedInUser) {
    setAuthNotice("");
    setConversations([]);
    setMessagesById({});
    setActiveId(null);
    setUser(loggedInUser);
  }

  function handleLogout() {
    clearToken();
    setConversations([]);
    setMessagesById({});
    setActiveId(null);
    setAuthNotice("");
    setUser(null);
  }

  // ---------- Danh sách hội thoại ----------

  const loadConversations = useCallback(async () => {
    setIsLoadingList(true);
    setListError("");
    try {
      setConversations(await listConversations());
    } catch (err) {
      setListError(`Không tải được lịch sử: ${err.message}`);
    } finally {
      setIsLoadingList(false);
    }
  }, []);

  useEffect(() => {
    if (user) loadConversations();
  }, [user, loadConversations]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [activeMessages, isSending]);

  /** Mở một hội thoại; lần đầu mở thì tải tin nhắn từ backend. Bấm lại khi lỗi = thử lại. */
  async function handleSelect(id) {
    setActiveId(id);
    if (messagesById[id]) return;

    setMessagesError(null);
    setLoadingMessagesId(id);
    try {
      const messages = await listMessages(id);
      // Không ghi đè nếu trong lúc chờ đã có tin nhắn (vd vừa gửi câu mới vào hội thoại này).
      setMessagesById((prev) => (prev[id] ? prev : { ...prev, [id]: messages.map(fromApiMessage) }));
    } catch (err) {
      setMessagesError({ id, message: err.message });
    } finally {
      setLoadingMessagesId((current) => (current === id ? null : current));
    }
  }

  /** Cuộc mới chỉ là bản nháp ở frontend; backend tạo hội thoại khi gửi câu hỏi đầu tiên. */
  function handleNew() {
    setActiveId(null);
    setMessagesById((prev) => ({ ...prev, [DRAFT]: [] }));
  }

  async function handleRename(conversation) {
    const title = window.prompt("Tên mới cho cuộc trò chuyện:", conversation.title)?.trim();
    if (!title || title === conversation.title) return;
    try {
      const updated = await renameConversation(conversation.id, title.slice(0, 200));
      setConversations((prev) =>
        prev.map((c) => (c.id === conversation.id ? { ...c, title: updated.title } : c))
      );
    } catch (err) {
      window.alert(`Không đổi tên được: ${err.message}`);
    }
  }

  async function handleDelete(conversation) {
    if (!window.confirm(`Xóa cuộc trò chuyện "${conversation.title}"? Không thể hoàn tác.`)) return;
    try {
      await deleteConversation(conversation.id);
      setConversations((prev) => prev.filter((c) => c.id !== conversation.id));
      setMessagesById((prev) => {
        const { [conversation.id]: _removed, ...rest } = prev;
        return rest;
      });
      if (conversation.id === activeId) setActiveId(null);
    } catch (err) {
      window.alert(`Không xóa được: ${err.message}`);
    }
  }

  // ---------- Gửi tin nhắn ----------

  async function handleSend(text) {
    // Tin nhắn của lượt này nằm ở messagesById[key]. Cuộc mới: key = DRAFT, đổi sang id thật
    // khi nhận sự kiện `conversation` (mọi cập nhật sau đó ghi vào id thật).
    let key = activeKey;

    /** Cập nhật danh sách tin nhắn của một key (bỏ qua nếu đã bị xoá, vd vừa đăng xuất). */
    const updateMessages = (k, updater) =>
      setMessagesById((prev) => (prev[k] ? { ...prev, [k]: updater(prev[k]) } : prev));
    /** Chỉ sửa tin nhắn cuối (bong bóng đang stream). */
    const updateReply = (updater) => {
      const k = key;
      updateMessages(k, (msgs) => [...msgs.slice(0, -1), updater(msgs.at(-1))]);
    };

    // Bong bóng trả lời rỗng được thêm ngay, rồi điền dần khi stream về.
    setMessagesById((prev) => ({
      ...prev,
      [key]: [
        ...(prev[key] ?? []),
        { role: "user", content: text },
        { role: "model", content: "", sources: [], pending: true, status: "thinking" },
      ],
    }));
    setIsSending(true);

    try {
      await streamChatMessage(text, activeId, {
        onConversation: ({ id, title }) => {
          if (key === DRAFT) {
            // Cuộc mới vừa được backend tạo: chuyển tin nhắn từ DRAFT sang id thật.
            setMessagesById((prev) => {
              const { [DRAFT]: draft, ...rest } = prev;
              return { ...rest, [id]: draft ?? [] };
            });
            // Chỉ chuyển sang hội thoại mới nếu người dùng vẫn đang ở bản nháp.
            setActiveId((current) => (current === null ? id : current));
            key = id;
          }
          // Đưa hội thoại lên đầu sidebar (vừa tạo, hoặc vừa có tin nhắn mới).
          setConversations((prev) => [
            { ...prev.find((c) => c.id === id), id, title },
            ...prev.filter((c) => c.id !== id),
          ]);
        },
        onStatus: (stage, data) =>
          updateReply((m) => ({ ...m, status: stage, statusQuery: data.query })),
        onSources: (sources) => updateReply((m) => ({ ...m, sources })),
        onDelta: (delta) => updateReply((m) => ({ ...m, content: m.content + delta })),
      });
      updateReply(stripPending);
    } catch (error) {
      // Backend không lưu lượt bị lỗi, nên bong bóng lỗi chỉ hiện ở đây (tải lại trang sẽ mất).
      updateReply((reply) => {
        const m = stripPending(reply);
        return m.content
          ? // Đã có một phần câu trả lời: giữ lại, ghi chú bị ngắt.
            { ...m, content: `${m.content}\n\n*(Câu trả lời bị gián đoạn: ${error.message})*` }
          : { ...m, content: `Đã có lỗi xảy ra: ${error.message}`, isError: true, sources: [] };
      });
    } finally {
      setIsSending(false);
    }
  }

  // ---------- Giao diện ----------

  let body;
  if (user === undefined) {
    body = bootError ? (
      <div className="auth">
        <div className="auth__card">
          <h2 className="auth__title">Không kết nối được</h2>
          <p className="auth__error">{bootError}</p>
          <button className="auth__submit" onClick={checkSession}>
            Thử lại
          </button>
        </div>
      </div>
    ) : (
      <div className="app-loading">Đang tải…</div>
    );
  } else if (user === null) {
    body = <AuthScreen onLoggedIn={handleLoggedIn} notice={authNotice} />;
  } else {
    // Hội thoại chưa có tin nhắn nào (cuộc mới) → hiện lời chào.
    const shownMessages = activeMessages?.length
      ? activeMessages
      : isLoadingMessages || activeError
        ? []
        : [WELCOME_MESSAGE];

    body = (
      <div className="app-body">
        <Sidebar
          user={user}
          conversations={conversations}
          activeId={activeId}
          isLoadingList={isLoadingList}
          listError={listError}
          disabled={isSending}
          onSelect={handleSelect}
          onNew={handleNew}
          onRename={handleRename}
          onDelete={handleDelete}
          onRetry={loadConversations}
          onLogout={handleLogout}
        />

        <div className="app-main">
          <main className="chat-window">
            {isLoadingMessages && <p className="chat-window__hint">Đang tải tin nhắn…</p>}
            {activeError && (
              <p className="chat-window__hint chat-window__hint--error">
                Không tải được tin nhắn: {activeError}. Bấm lại vào cuộc trò chuyện để thử lại.
              </p>
            )}
            {shownMessages.map((message, index) => (
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

          <ChatInput
            onSend={handleSend}
            disabled={isSending || isLoadingMessages || Boolean(activeError)}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>🩺 ChatBot Y Tế</h1>
        <p>Hỏi đáp thông tin sức khỏe cùng trợ lý ảo (Gemini AI)</p>
      </header>

      {body}

      <footer className="app-footer">
        Thông tin do chatbot cung cấp chỉ mang tính tham khảo, không thay thế
        cho tư vấn, chẩn đoán của bác sĩ hoặc chuyên gia y tế.
      </footer>
    </div>
  );
}

export default App;
