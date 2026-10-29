/**
 * Danh sách hội thoại của user (lấy từ backend) + nút tạo mới, đổi tên, xoá, đăng xuất.
 * Mọi thao tác chỉ gọi callback; App.jsx gọi API và cập nhật state.
 * `disabled` (đang chờ câu trả lời): khoá tạo mới / đổi tên / xoá / đăng xuất để không làm hỏng
 * lượt chat đang stream; vẫn cho chọn hội thoại khác để xem.
 */
export default function Sidebar({
  user,
  conversations,
  activeId,
  isLoadingList,
  listError,
  disabled,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onRetry,
  onLogout,
}) {
  return (
    <aside className="sidebar">
      <button className="sidebar__new-btn" onClick={onNew} disabled={disabled}>
        + Cuộc trò chuyện mới
      </button>

      <div className="sidebar__list">
        {isLoadingList && <p className="sidebar__hint">Đang tải lịch sử…</p>}

        {listError && (
          <div className="sidebar__hint sidebar__hint--error">
            {listError}{" "}
            <button className="sidebar__link-btn" onClick={onRetry}>
              Thử lại
            </button>
          </div>
        )}

        {!isLoadingList && !listError && conversations.length === 0 && (
          <p className="sidebar__hint">Chưa có cuộc trò chuyện nào.</p>
        )}

        {conversations.map((conversation) => (
          <div
            key={conversation.id}
            className={
              "sidebar__item" + (conversation.id === activeId ? " sidebar__item--active" : "")
            }
            onClick={() => onSelect(conversation.id)}
            title={conversation.title}
          >
            <span className="sidebar__item-title">{conversation.title}</span>
            <span className="sidebar__item-actions">
              <button
                className="sidebar__item-btn"
                disabled={disabled}
                title="Đổi tên"
                aria-label="Đổi tên cuộc trò chuyện"
                onClick={(event) => {
                  event.stopPropagation(); // không chọn hội thoại khi bấm nút
                  onRename(conversation);
                }}
              >
                ✎
              </button>
              <button
                className="sidebar__item-btn sidebar__item-btn--delete"
                disabled={disabled}
                title="Xóa"
                aria-label="Xóa cuộc trò chuyện"
                onClick={(event) => {
                  event.stopPropagation();
                  onDelete(conversation);
                }}
              >
                ×
              </button>
            </span>
          </div>
        ))}
      </div>

      <div className="sidebar__user">
        <div className="sidebar__user-info">
          <span className="sidebar__user-name">{user.display_name}</span>
          <span className="sidebar__user-email">{user.email}</span>
        </div>
        <button className="sidebar__logout-btn" onClick={onLogout} disabled={disabled}>
          Đăng xuất
        </button>
      </div>
    </aside>
  );
}
