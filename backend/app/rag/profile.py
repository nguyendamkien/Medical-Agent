"""Hồ sơ sức khoẻ người dùng tự khai (bảng health_profiles) → thông tin đưa vào prompt.

Hồ sơ được đưa thẳng vào prompt ở mọi câu hỏi (không qua tool), vì hồ sơ ngắn và model cần biết
TRƯỚC khi viết truy vấn tìm kiếm ("tôi ăn hải sản được không" + gout → tìm "người bệnh gout ăn
hải sản"). Xem docs/HEALTH_PROFILE.md.

Hai phần:
- profile_instruction(): khối "THÔNG TIN NGƯỜI HỎI" + cách dùng, thêm vào system instruction của
  agent (hoặc vào prompt của pipeline cũ).
- allergy_notes(): quét các đoạn tài liệu bằng code, đoạn nào nhắc tới thuốc người dùng ghi dị ứng
  thì thêm dòng nhắc cảnh báo — không trông vào việc model tự để ý.

Module này không đụng tới DB: routers/chat.py đọc bảng rồi tạo UserProfile.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

# Tên thuốc ngắn hơn thế này dễ khớp nhầm (vd "ac" khớp nhiều từ) nên bỏ qua khi quét.
MIN_ALLERGY_CHARS = 3


@dataclass
class UserProfile:
	"""Những gì prompt cần biết về người hỏi. Trường trống thì không đưa vào prompt."""

	birth_year: int | None = None
	pregnant: bool = False
	conditions: list[str] = field(default_factory=list)
	allergies: list[str] = field(default_factory=list)

	@property
	def is_empty(self) -> bool:
		return not (self.birth_year or self.pregnant or self.conditions or self.allergies)

	@classmethod
	def from_dict(cls, data: dict[str, Any] | None) -> "UserProfile | None":
		"""Từ dict (vd trường `profile` trong golden set). Rỗng → None."""
		if not data:
			return None
		profile = cls(
			birth_year=data.get("birth_year"),
			pregnant=bool(data.get("pregnant")),
			conditions=list(data.get("conditions") or []),
			allergies=list(data.get("allergies") or []),
		)
		return None if profile.is_empty else profile


def profile_instruction(profile: UserProfile | None, today: date | None = None) -> str:
	"""Khối thêm vào cuối system instruction / prompt. Hồ sơ rỗng → chuỗi rỗng."""
	if profile is None or profile.is_empty:
		return ""
	lines = []
	if profile.birth_year:
		age = (today or date.today()).year - profile.birth_year
		lines.append(f"- Tuổi: khoảng {age} (sinh năm {profile.birth_year})")
	if profile.conditions:
		lines.append(f"- Bệnh nền: {', '.join(profile.conditions)}")
	if profile.allergies:
		lines.append(f"- Dị ứng thuốc: {', '.join(profile.allergies)}")
	if profile.pregnant:
		lines.append("- Đang mang thai")
	facts = "\n".join(lines)
	return f"""\
THÔNG TIN NGƯỜI HỎI (người dùng tự khai trong hồ sơ sức khỏe, chưa được bác sĩ xác nhận):
{facts}

Cách dùng thông tin người hỏi:
- CHỈ áp dụng khi người dùng hỏi về chính họ ("tôi", "mình", "em"... hoặc không nói là hỏi \
cho ai). Hỏi cho người khác (mẹ, con, chồng, bạn...) hoặc hỏi kiến thức chung về một bệnh \
thì KHÔNG áp dụng.
- Khi áp dụng và có tool tìm kiếm: viết truy vấn có kèm bệnh nền liên quan tới câu hỏi (vd \
"tôi ăn hải sản được không" + bệnh nền gout → tìm "người bệnh gout có nên ăn hải sản"). Bệnh \
nền không liên quan tới câu hỏi thì không thêm vào truy vấn.
- Khi trả lời dựa trên hồ sơ, nói rõ điều đó (vd "Vì bạn có ghi bệnh gout trong hồ sơ, …").
- Tài liệu nhắc tới thuốc người hỏi ghi dị ứng: cảnh báo rõ người hỏi đã ghi dị ứng thuốc đó \
và cần hỏi bác sĩ trước khi dùng.
- Đang mang thai và hỏi về bệnh / thuốc / triệu chứng của chính mình: tìm THÊM một lần riêng \
theo thai kỳ (vd hồ sơ tăng huyết áp + mang thai → tìm cả "tăng huyết áp thai kỳ"). Tài liệu \
có nội dung về thai kỳ / phụ nữ mang thai thì nêu ra; không có thì khuyên hỏi bác sĩ, KHÔNG tự \
suy diễn.
- Hồ sơ KHÔNG thay đổi các quy tắc khác: vẫn chỉ dùng thông tin y tế từ tài liệu, vẫn KHÔNG \
nêu liều thuốc, KHÔNG chẩn đoán. Hồ sơ không phải tài liệu y tế, không ghi hồ sơ làm [Nguồn i].
"""


def normalize(text: str) -> str:
	"""Chữ thường, bỏ dấu tiếng Việt: "Aspirin", "aspirin", "ÁSPIRIN" đều thành "aspirin"."""
	text = unicodedata.normalize("NFD", text.casefold()).replace("đ", "d")
	return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def _allergy_patterns(allergies: list[str]) -> list[tuple[str, re.Pattern]]:
	patterns = []
	for allergy in allergies:
		term = normalize(allergy).strip()
		if len(term) < MIN_ALLERGY_CHARS:
			continue
		# Khớp nguyên từ: "aspirin" không khớp trong "aspirinx"; khoảng trắng trong tên được co giãn.
		body = r"\s+".join(re.escape(part) for part in term.split())
		patterns.append((allergy, re.compile(rf"(?<!\w){body}(?!\w)")))
	return patterns


def find_allergies(text: str, allergies: list[str]) -> list[str]:
	"""Những tên trong `allergies` (giữ nguyên cách người dùng viết) có xuất hiện trong `text`."""
	normalized = normalize(text)
	return [allergy for allergy, pattern in _allergy_patterns(allergies) if pattern.search(normalized)]


def allergy_notes(
	results: list[dict], allergies: list[str], number: Callable[[dict], int]
) -> list[str]:
	"""Một dòng nhắc cho mỗi thuốc dị ứng có trong `results`, kèm số [Nguồn i] nhắc tới nó.

	`number(metadata)` trả số nguồn của chunk (ToolSession._number, hoặc đánh số như build_context).
	"""
	if not allergies:
		return []
	found: dict[str, list[int]] = {}
	for result in results:
		index = number(result.get("metadata", {}))
		for allergy in find_allergies(result.get("text", ""), allergies):
			sources = found.setdefault(allergy, [])
			if index not in sources:
				sources.append(index)
	return [
		f"(Lưu ý hồ sơ: người hỏi ghi DỊ ỨNG \"{allergy}\"; "
		f"{', '.join(f'[Nguồn {i}]' for i in sources)} có nhắc tới. Nếu câu hỏi là về chính người hỏi "
		f"và câu trả lời nhắc tới thuốc này, PHẢI cảnh báo rõ người hỏi đã ghi dị ứng và cần hỏi bác sĩ "
		f"trước khi dùng.)"
		for allergy, sources in found.items()
	]
