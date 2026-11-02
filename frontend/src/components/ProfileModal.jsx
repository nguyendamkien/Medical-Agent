import { useEffect, useState } from "react";
import { getProfileOptions } from "../api/chatApi";

/** "aspirin, ibuprofen" → ["aspirin", "ibuprofen"] */
const splitList = (text) =>
  text
    .split(/[,;\n]/)
    .map((item) => item.trim())
    .filter(Boolean);

/**
 * Form hồ sơ sức khỏe (mở từ sidebar hoặc dòng tóm tắt trên ô chat).
 * - Bệnh nền: tick các nhóm bệnh có trong dữ liệu (GET /api/profile/options) + ô gõ thêm.
 * - Dị ứng thuốc: gõ tên, cách nhau bởi dấu phẩy.
 * `onSave(body)` / `onDelete()` trả Promise; lỗi thì hiện trong form, thành công thì App đóng form.
 */
export default function ProfileModal({ profile, onSave, onDelete, onClose }) {
  const [options, setOptions] = useState([]);
  const [birthYear, setBirthYear] = useState(profile?.birth_year ?? "");
  const [pregnant, setPregnant] = useState(profile?.pregnant ?? false);
  // Bệnh nền đã lưu hiện trong ô gõ tay cho tới khi tải xong danh sách gợi ý (effect bên dưới
  // chuyển những bệnh có trong danh sách sang ô tick). Tải gợi ý lỗi thì vẫn thấy đủ ở ô gõ tay.
  const [conditions, setConditions] = useState([]);
  const [otherConditions, setOtherConditions] = useState((profile?.conditions ?? []).join(", "));
  const [allergies, setAllergies] = useState((profile?.allergies ?? []).join(", "));
  const [useInChat, setUseInChat] = useState(profile?.use_in_chat ?? true);
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    getProfileOptions()
      .then((data) => setOptions(data.conditions))
      .catch(() => setOptions([])); // không có gợi ý thì vẫn gõ tay được
  }, []);

  useEffect(() => {
    if (!options.length) return;
    const saved = profile?.conditions ?? [];
    setConditions(saved.filter((c) => options.includes(c)));
    setOtherConditions(saved.filter((c) => !options.includes(c)).join(", "));
  }, [options, profile]);

  // Esc để đóng.
  useEffect(() => {
    const onKey = (event) => event.key === "Escape" && !isBusy && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isBusy, onClose]);

  function toggleCondition(name) {
    setConditions((current) =>
      current.includes(name) ? current.filter((c) => c !== name) : [...current, name]
    );
  }

  async function run(action) {
    setIsBusy(true);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(err.message);
      setIsBusy(false);
    }
  }

  function handleSubmit(event) {
    event.preventDefault();
    run(() =>
      onSave({
        birth_year: birthYear === "" ? null : Number(birthYear),
        pregnant,
        conditions: [...conditions, ...splitList(otherConditions)],
        allergies: splitList(allergies),
        use_in_chat: useInChat,
      })
    );
  }

  function handleDelete() {
    if (!window.confirm("Xóa toàn bộ hồ sơ sức khỏe? Không thể hoàn tác.")) return;
    run(onDelete);
  }

  return (
    <div className="modal-backdrop" onMouseDown={() => !isBusy && onClose()}>
      <form
        className="modal profile-form"
        onSubmit={handleSubmit}
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="profile-title"
      >
        <h2 id="profile-title" className="modal__title">
          Hồ sơ sức khỏe
        </h2>
        <p className="modal__subtitle">
          Chatbot dùng hồ sơ để tìm đúng tài liệu cho bạn và nhắc khi tài liệu nói tới thuốc bạn
          dị ứng. Hồ sơ chỉ bạn xem được và có thể xóa bất cứ lúc nào. Không điền gì cũng được.
        </p>

        <label className="profile-form__field">
          Năm sinh
          <input
            type="number"
            min="1900"
            max={new Date().getFullYear()}
            placeholder="vd 1985"
            value={birthYear}
            onChange={(event) => setBirthYear(event.target.value)}
          />
        </label>

        <fieldset className="profile-form__field">
          <legend>Bệnh nền</legend>
          {options.length > 0 && (
            <div className="profile-form__checks">
              {options.map((name) => (
                <label key={name} className="profile-form__check">
                  <input
                    type="checkbox"
                    checked={conditions.includes(name)}
                    onChange={() => toggleCondition(name)}
                  />
                  {name}
                </label>
              ))}
            </div>
          )}
          <input
            type="text"
            placeholder={options.length ? "Bệnh khác, cách nhau bởi dấu phẩy" : "vd gout, tăng huyết áp"}
            value={otherConditions}
            onChange={(event) => setOtherConditions(event.target.value)}
          />
        </fieldset>

        <label className="profile-form__field">
          Dị ứng thuốc
          <input
            type="text"
            placeholder="vd aspirin, penicillin"
            value={allergies}
            onChange={(event) => setAllergies(event.target.value)}
          />
          <span className="profile-form__help">
            Ghi đúng tên thuốc như trên vỉ / đơn thuốc. Chatbot chỉ nhận ra đúng tên bạn ghi, chưa
            tự hiểu nhóm thuốc.
          </span>
        </label>

        <label className="profile-form__check">
          <input type="checkbox" checked={pregnant} onChange={(e) => setPregnant(e.target.checked)} />
          Đang mang thai
        </label>

        <label className="profile-form__check profile-form__check--toggle">
          <input type="checkbox" checked={useInChat} onChange={(e) => setUseInChat(e.target.checked)} />
          Dùng hồ sơ khi trả lời câu hỏi
        </label>

        {error && <p className="auth__error">{error}</p>}

        <div className="modal__actions">
          {profile?.updated_at && (
            <button
              type="button"
              className="modal__btn modal__btn--danger"
              onClick={handleDelete}
              disabled={isBusy}
            >
              Xóa hồ sơ
            </button>
          )}
          <span className="modal__spacer" />
          <button type="button" className="modal__btn" onClick={onClose} disabled={isBusy}>
            Hủy
          </button>
          <button type="submit" className="modal__btn modal__btn--primary" disabled={isBusy}>
            {isBusy ? "Đang lưu…" : "Lưu"}
          </button>
        </div>
      </form>
    </div>
  );
}
