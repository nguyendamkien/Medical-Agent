"""Gợi ý bệnh viện / phòng khám ở Hà Nội theo chuyên khoa (docs/FACILITIES.md).

Dữ liệu: app/data/facilities_hanoi.jsonl, tự tổng hợp và kiểm tra trên trang chính thức của từng
cơ sở (`verified`, `source_url`, `checked_at`). Số điện thoại CHỈ ghi khi lấy được từ trang chính
thức: số trên các trang tổng hợp thường lệch nhau, có bệnh viện từng cảnh báo số giả mạo.

Tìm kiếm thuần code, không gọi Gemini; dùng chung cho tool find_healthcare_facility của agent
(app/rag/tools.py) và API GET /api/facilities.

Thứ tự kết quả — trung lập, không xếp hạng "uy tín":
1. Đúng chuyên khoa trước, bệnh viện đa khoa sau.
2. Người bệnh là trẻ em: nơi có chuyên khoa nhi trước.
3. Cơ sở đã xác minh trước cơ sở chưa xác minh.
4. Cùng nhóm thì giữ thứ tự trong file (công trước, tư sau).
"""

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "facilities_hanoi.jsonl"

# Mã chuyên khoa → tên hiển thị. "da_khoa" không phải chuyên khoa để chọn, chỉ đánh dấu bệnh viện
# đa khoa (dùng làm lựa chọn dự phòng khi ít cơ sở đúng chuyên khoa).
SPECIALTIES: dict[str, str] = {
	"tim_mach": "Tim mạch",
	"tieu_hoa": "Tiêu hóa",
	"co_xuong_khop": "Cơ xương khớp",
	"than_tiet_nieu": "Thận, tiết niệu",
	"ho_hap": "Hô hấp",
	"da_lieu": "Da liễu",
	"gan_truyen_nhiem": "Gan, truyền nhiễm",
	"san_khoa": "Sản khoa",
	"nhi": "Nhi",
	"noi_tong_quat": "Nội tổng quát (chưa rõ chuyên khoa)",
}
GENERAL = "da_khoa"

# Nhóm bệnh trong dữ liệu bài viết → chuyên khoa nên khám. Viết sẵn để model không phải đoán.
DISEASE_GROUP_SPECIALTY: dict[str, str] = {
	"Tăng huyết áp": "tim_mach",
	"Suy tim": "tim_mach",
	"Viêm loét dạ dày": "tieu_hoa",
	"Gout (Gút)": "co_xuong_khop",
	"Viêm khớp": "co_xuong_khop",
	"Viêm gan B": "gan_truyen_nhiem",
	"Suy thận": "than_tiet_nieu",
	"Sỏi thận": "than_tiet_nieu",
	"Viêm da cơ địa": "da_lieu",
	"Hen phế quản (Hen suyễn)": "ho_hap",
}

DEFAULT_LIMIT = 5


@lru_cache
def load_facilities() -> list[dict[str, Any]]:
	"""Đọc file dữ liệu một lần. Mã chuyên khoa lạ / id trùng → báo lỗi ngay khi khởi động."""
	if not DATA_PATH.exists():
		logger.warning("Không có file dữ liệu cơ sở y tế: %s", DATA_PATH)
		return []
	facilities = []
	seen: set[str] = set()
	with DATA_PATH.open(encoding="utf-8") as file:
		for line_number, line in enumerate(file, start=1):
			if not line.strip():
				continue
			item = json.loads(line)
			unknown = set(item.get("specialties", [])) - set(SPECIALTIES) - {GENERAL}
			if unknown or item["id"] in seen:
				raise ValueError(f"{DATA_PATH.name} dòng {line_number}: chuyên khoa lạ {unknown} hoặc id trùng")
			seen.add(item["id"])
			facilities.append(item)
	return facilities


def maps_url(facility: dict[str, Any]) -> str:
	"""Link Google Maps tìm theo tên + địa chỉ: Maps hiện địa chỉ, giờ mở cửa, số điện thoại mới
	nhất, nên dữ liệu của mình ít bị cũ. Không cần API key."""
	query = ", ".join(part for part in (facility["name"], facility.get("address") or "Hà Nội") if part)
	return f"https://www.google.com/maps/search/?api=1&query={quote(query)}"


def search_facilities(
	specialty: str,
	emergency: bool = False,
	for_child: bool = False,
	limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
	"""Cơ sở phù hợp, đã sắp xếp (xem đầu file). Mỗi kết quả có thêm `matched`
	("chuyen_khoa" | "da_khoa"), `specialty_labels`, `maps_url`.

	- `emergency`: chỉ lấy nơi có cấp cứu (emergency = true).
	- `for_child`: người bệnh là trẻ em → ưu tiên bệnh viện nhi; ngược lại bỏ bệnh viện chỉ khám trẻ em.
	- `specialty` "noi_tong_quat" (hoặc mã lạ): chỉ trả bệnh viện đa khoa.
	"""
	if specialty not in SPECIALTIES:
		specialty = "noi_tong_quat"
	ranked = []
	for order, facility in enumerate(load_facilities()):
		specialties = facility.get("specialties", [])
		if facility.get("children_only") and not for_child:
			continue
		if emergency and facility.get("emergency") is not True:
			continue
		if specialty in specialties or (for_child and "nhi" in specialties):
			matched = "chuyen_khoa"
		elif GENERAL in specialties or (emergency and not specialties):
			# Cấp cứu: bệnh viện ngoại khoa (không có chuyên khoa nội nào) vẫn nhận cấp cứu.
			matched = "da_khoa"
		else:
			continue
		# Trẻ em: trong cùng nhóm, nơi có chuyên khoa nhi lên trước.
		rank = (matched != "chuyen_khoa", for_child and "nhi" not in specialties, not facility.get("verified"), order)
		ranked.append((rank, matched, facility))
	ranked.sort(key=lambda item: item[0])
	return [
		{
			**facility,
			"matched": matched,
			"specialty_labels": [SPECIALTIES[s] for s in facility.get("specialties", []) if s in SPECIALTIES],
			"maps_url": maps_url(facility),
		}
		for _, matched, facility in ranked[:limit]
	]


def facility_card(facility: dict[str, Any]) -> dict[str, Any]:
	"""Phần gửi xuống frontend / lưu vào tin nhắn: bỏ trường nội bộ."""
	keys = (
		"id", "name", "ownership", "address", "phones", "emergency", "children_only", "website",
		"verified", "checked_at", "note", "matched", "specialty_labels", "maps_url",
	)
	return {key: facility.get(key) for key in keys}
