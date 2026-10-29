import { useState } from "react";
import { login, register } from "../api/chatApi";

/**
 * Màn đăng nhập / đăng ký, hiện khi chưa đăng nhập (App.jsx).
 * Đăng ký xong tự đăng nhập luôn. Thành công → gọi onLoggedIn(user).
 */
export default function AuthScreen({ onLoggedIn, notice }) {
  const [mode, setMode] = useState("login"); // "login" | "register"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const isRegister = mode === "register";

  async function handleSubmit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (isRegister) await register(email.trim(), password, displayName.trim());
      const user = await login(email.trim(), password);
      onLoggedIn(user);
    } catch (err) {
      setError(err.message);
      setBusy(false); // chỉ mở khoá khi lỗi; thành công thì màn này biến mất
    }
  }

  function switchMode() {
    setMode(isRegister ? "login" : "register");
    setError("");
  }

  return (
    <div className="auth">
      <form className="auth__card" onSubmit={handleSubmit}>
        <h2 className="auth__title">{isRegister ? "Tạo tài khoản" : "Đăng nhập"}</h2>
        <p className="auth__subtitle">
          {isRegister
            ? "Tạo tài khoản để lưu lại lịch sử trò chuyện."
            : "Đăng nhập để trò chuyện và xem lại lịch sử."}
        </p>

        {/* Ví dụ: "Phiên đăng nhập đã hết hạn" khi bị đưa về màn này do token hết hạn. */}
        {notice && !error && <p className="auth__notice">{notice}</p>}

        {isRegister && (
          <label className="auth__field">
            <span>Tên hiển thị</span>
            <input
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              maxLength={100}
              autoComplete="name"
              required
            />
          </label>
        )}

        <label className="auth__field">
          <span>Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
          />
        </label>

        <label className="auth__field">
          <span>Mật khẩu</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={isRegister ? "Ít nhất 8 ký tự" : ""}
            minLength={isRegister ? 8 : undefined}
            maxLength={128}
            autoComplete={isRegister ? "new-password" : "current-password"}
            required
          />
        </label>

        {error && (
          <p className="auth__error" role="alert">
            {error}
          </p>
        )}

        <button type="submit" className="auth__submit" disabled={busy}>
          {busy ? "Đang xử lý…" : isRegister ? "Đăng ký" : "Đăng nhập"}
        </button>

        <button type="button" className="auth__switch" onClick={switchMode} disabled={busy}>
          {isRegister ? "Đã có tài khoản? Đăng nhập" : "Chưa có tài khoản? Đăng ký"}
        </button>
      </form>
    </div>
  );
}
