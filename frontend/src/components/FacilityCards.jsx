/**
 * Thẻ bệnh viện / phòng khám agent gợi ý (tool find_healthcare_facility, docs/FACILITIES.md).
 * Địa chỉ và số điện thoại lấy từ dữ liệu đã kiểm tra trên trang chính thức, không do model viết.
 * Cơ sở chưa xác minh (`verified: false`) không có số điện thoại và có nhãn cảnh báo.
 */

const formatDate = (iso) => {
  const [y, m, d] = (iso ?? "").split("-");
  return d ? `${d}/${m}/${y}` : iso;
};

export default function FacilityCards({ facilities }) {
  if (!facilities?.length) return null;
  const checkedAt = facilities.map((f) => f.checked_at).sort()[0];

  return (
    <div className="facilities">
      <strong className="facilities__title">Gợi ý nơi khám ở Hà Nội</strong>
      <ul className="facilities__list">
        {facilities.map((f) => (
          <li key={f.id} className="facility">
            <div className="facility__head">
              <span className="facility__name">{f.name}</span>
              <span className="facility__badges">
                <span className="facility__badge">{f.ownership === "cong" ? "Công lập" : "Tư nhân"}</span>
                {f.emergency && <span className="facility__badge facility__badge--emergency">Cấp cứu</span>}
                {f.children_only && <span className="facility__badge">Chỉ khám trẻ em</span>}
              </span>
            </div>

            <div className="facility__meta">
              {f.matched === "chuyen_khoa"
                ? `Chuyên khoa: ${f.specialty_labels.join(", ")}`
                : "Bệnh viện đa khoa"}
            </div>
            {f.address && <div className="facility__address">📍 {f.address}</div>}

            {f.phones.length > 0 && (
              <ul className="facility__phones">
                {f.phones.map((p) => (
                  <li key={p.number}>
                    {p.label}: <a href={`tel:${p.number.replace(/\s/g, "")}`}>{p.number}</a>
                  </li>
                ))}
              </ul>
            )}

            {!f.verified && (
              <div className="facility__warning">
                Chưa xác minh trên trang chính thức. Hãy kiểm tra thông tin trước khi đến.
              </div>
            )}

            <div className="facility__actions">
              <a href={f.maps_url} target="_blank" rel="noreferrer">
                Chỉ đường
              </a>
              {f.website && (
                <a href={f.website} target="_blank" rel="noreferrer">
                  Website
                </a>
              )}
            </div>
          </li>
        ))}
      </ul>
      <p className="facilities__note">
        Danh sách tham khảo, không xếp hạng (kiểm tra ngày {formatDate(checkedAt)}). Gọi trước để xác
        nhận lịch khám. Cấp cứu: gọi <a href="tel:115">115</a>.
      </p>
    </div>
  );
}
