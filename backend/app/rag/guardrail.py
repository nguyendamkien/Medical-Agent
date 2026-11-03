"""Guardrail liều thuốc: quét câu trả lời và che liều dùng thuốc trước khi gửi cho người dùng.

Quy tắc "không nêu liều" nằm trong prompt, nhưng LLM vẫn nêu liều ngẫu nhiên khi tài liệu có ghi
(eval 20/10/2026: agent nêu "40mg hoặc hai lần mỗi ngày" ở safety-05). Guardrail này là lớp chặn
bằng code phía sau prompt: cụm liều bị thay bằng REPLACEMENT.

Che gì:
- Hàm lượng thuốc: số + mg / mcg / µg / IU / viên / nhát / giọt / ống / gói — trừ khi theo sau là
  "/" (đơn vị nồng độ, xét nghiệm, khẩu phần: "6 mg/dL", "50 mg/100 g", "2.000mg/ngày natri").
  Cũng không che lượng chất dinh dưỡng tính bằng mg / mcg: ngay sau là natri / muối / kali / canxi /
  purin / cholesterol ("2.000mg natri/ngày", "451 mg kali"), hoặc chất đó đứng trước trong cùng câu
  ("lượng natri dưới 2.000 mg"), và trong câu không có từ chỉ thuốc ("kali clorid 600mg" vẫn che).
  Vitamin, sắt... không nằm trong danh sách: liều bổ sung của chúng là liều thuốc.
- Số lần dùng thuốc: "2 lần mỗi ngày", "hai lần/ngày" — chỉ khi gần đó (DRUG_CONTEXT_CHARS ký tự
  trước) có từ chỉ thuốc, để không che "chia 5–6 bữa, ăn 3 lần mỗi ngày".
Không che: g / ml (lượng muối, thịt, nước — "5g muối", "250 ml nước"), chỉ số xét nghiệm.

Câu trả lời được stream: StreamRedactor giữ lại HOLD_CHARS ký tự cuối chưa gửi. Một cụm liều luôn
ngắn hơn HOLD_CHARS, nên mọi cụm liều đều nằm trọn trong phần chưa gửi khi bị quét — không có
ký tự nào của nó kịp tới frontend.
"""

import re

REPLACEMENT = "[liều do bác sĩ chỉ định]"
HOLD_CHARS = 80
# Cụm khớp kết thúc trong LOOKAHEAD_CHARS ký tự cuối chưa được che khi đang stream: phần sau có thể
# đổi kết luận ("6 mg" + "/dL" là chỉ số xét nghiệm, "451 mg" + " kali" là dinh dưỡng). Lúc flush
# thì che hết. Phải dài hơn mọi chữ khớp NUTRIENT_AFTER (" cholesterol" = 12 ký tự).
LOOKAHEAD_CHARS = 16
DRUG_CONTEXT_CHARS = 120
# Chất dinh dưỡng đứng trước số trong cùng câu, cách tối đa chừng này ký tự ("natri dưới 2.000 mg").
NUTRIENT_BEFORE_CHARS = 40

_NUMBER = r"\d+(?:[.,]\d+)?(?:\s*(?:-|–|đến)\s*\d+(?:[.,]\d+)?)?"
_WORD_NUMBER = r"(?:một|hai|ba|bốn|năm|sáu)"

# Hàm lượng: "40mg", "0,5 mg", "1-2 viên", "2 nhát". Không theo sau bởi "/" (mg/dL, mg/100 g...).
# Nhóm `unit`: chỉ đơn vị khối lượng mới có thể là lượng dinh dưỡng (xem _is_nutrient_amount).
STRENGTH = re.compile(
	rf"(?<![\w.,]){_NUMBER}\s*(?P<unit>mg|mcg|µg|μg|microgam|miligam|IU|UI|viên|nhát|giọt|ống|gói)\b(?!\s*/)",
	re.IGNORECASE,
)
MASS_UNITS = {"mg", "mcg", "µg", "μg", "microgam", "miligam"}
_NUTRIENTS = r"(?:natri|muối|kali|canxi|purin|cholesterol)"
NUTRIENT_AFTER = re.compile(rf"\s*{_NUTRIENTS}\b", re.IGNORECASE)
NUTRIENT_BEFORE = re.compile(rf"\b{_NUTRIENTS}\b", re.IGNORECASE)
# Ranh giới câu: dấu câu + khoảng trắng, hoặc xuống dòng. "2.000" và "(0.5 mg" không bị cắt.
SENTENCE_END = re.compile(r"[.!?;]\s|\n")
# Số lần / ngày: "2 lần mỗi ngày", "hai lần/ngày", "1-2 lần một ngày".
FREQUENCY = re.compile(
	rf"(?<![\w.,])(?:{_NUMBER}|{_WORD_NUMBER})\s+lần\s*(?:mỗi|một|/|trong)\s*(?:ngày|ngày\b)",
	re.IGNORECASE,
)
DRUG_CONTEXT = re.compile(
	r"thuốc|liều|viên|xịt|tiêm|kê đơn|omeprazol|prazol|colchicin|allopurinol|febuxostat|"
	r"corticoid|kháng sinh|giảm đau|paracetamol|ibuprofen|insulin|salbutamol|clorid",
	re.IGNORECASE,
)


def _is_nutrient_amount(text: str, m: re.Match) -> bool:
	"""`m` (khớp STRENGTH) là lượng chất dinh dưỡng chứ không phải liều thuốc?

	Chỉ đọc từ đầu câu tới LOOKAHEAD_CHARS ký tự sau cụm khớp, để lúc stream và lúc flush cho cùng
	kết luận (phần xa hơn có thể chưa tới).
	"""
	if m.group("unit").lower() not in MASS_UNITS:
		return False
	sentence_start = max((b.end() for b in SENTENCE_END.finditer(text, 0, m.start())), default=0)
	before = text[sentence_start : m.start()]
	if DRUG_CONTEXT.search(before):
		return False
	if NUTRIENT_AFTER.match(text, m.end(), m.end() + LOOKAHEAD_CHARS):
		return True
	return bool(NUTRIENT_BEFORE.search(before[-NUTRIENT_BEFORE_CHARS:]))


def find_doses(text: str, start: int = 0) -> list[tuple[int, int]]:
	"""Các cụm liều (đầu, cuối) trong `text` bắt đầu từ vị trí `start` trở đi, không chồng nhau."""
	spans = [
		(m.start(), m.end())
		for m in STRENGTH.finditer(text)
		if m.start() >= start and not _is_nutrient_amount(text, m)
	]
	for m in FREQUENCY.finditer(text):
		if m.start() < start:
			continue
		if DRUG_CONTEXT.search(text, max(0, m.start() - DRUG_CONTEXT_CHARS), m.end()):
			spans.append((m.start(), m.end()))
	spans.sort()
	merged: list[tuple[int, int]] = []
	for s, e in spans:
		if merged and s < merged[-1][1]:
			merged[-1] = (merged[-1][0], max(merged[-1][1], e))
		else:
			merged.append((s, e))
	return merged


def redact_doses(text: str) -> tuple[str, list[str]]:
	"""Che mọi cụm liều trong một đoạn text hoàn chỉnh; trả về (text đã che, các cụm bị che)."""
	redactor = StreamRedactor()
	return redactor.feed(text) + redactor.flush(), redactor.redacted


class StreamRedactor:
	"""Che liều trong câu trả lời đang stream: feed() từng đoạn, nhận lại phần đã an toàn để gửi."""

	def __init__(self) -> None:
		self._sent = ""  # đã gửi đi — không sửa được nữa, chỉ dùng làm ngữ cảnh
		self._pending = ""  # chưa gửi
		self.redacted: list[str] = []

	def feed(self, chunk: str) -> str:
		self._pending += chunk
		self._redact_pending(final=False)
		if len(self._pending) <= HOLD_CHARS:
			return ""
		ready, self._pending = self._pending[:-HOLD_CHARS], self._pending[-HOLD_CHARS:]
		self._sent += ready
		return ready

	def flush(self) -> str:
		self._redact_pending(final=True)
		ready, self._pending = self._pending, ""
		self._sent += ready
		return ready

	def _redact_pending(self, final: bool) -> None:
		text = self._sent + self._pending
		offset = len(self._sent)
		spans = find_doses(text, start=offset)
		if not final:
			spans = [(s, e) for s, e in spans if e <= len(text) - LOOKAHEAD_CHARS]
		self.redacted += [text[s:e] for s, e in spans]
		for s, e in reversed(spans):
			text = text[:s] + REPLACEMENT + text[e:]
		self._pending = text[offset:]
