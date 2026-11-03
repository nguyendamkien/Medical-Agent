"""Tool của agent (xem docs/AGENTIC_RAG.md): khai báo cho Gemini và code chạy từng tool.

Ba tool, đều chỉ đọc dữ liệu có sẵn:

    search_medical_docs(query, disease_group?)  bọc Retriever.search (hybrid + rerank + ngưỡng)
    get_article(document_id, section?)          đọc cả bài / một mục, ghép từ các chunk trong RAM
    find_healthcare_facility(specialty, emergency?, for_child?)
                                                gợi ý cơ sở y tế ở Hà Nội (services/facility_service.py);
                                                danh sách đi thẳng xuống frontend thành thẻ, model chỉ
                                                nhận số lượng để viết 1–3 câu dẫn

ToolSession giữ trạng thái của MỘT request, những việc không giao cho model:
- Đánh số [Nguồn i] xuyên suốt request: chunk của bài đã gặp dùng lại số cũ, bài mới đánh số
  tiếp. `results` giữ mọi chunk đã trả cho model theo đúng thứ tự đó, nên collect_sources(results)
  khớp với số trong câu trả lời.
- Không gửi lại chunk đã trả ở lần tìm trước.
- get_article chỉ nhận document_id đã xuất hiện trong kết quả tìm kiếm của request này.
- Người dùng có ghi dị ứng thuốc trong hồ sơ: đoạn tài liệu nào nhắc tới thuốc đó thì cuối kết quả
  tool có thêm dòng nhắc cảnh báo (app/rag/profile.py → allergy_notes).
"""

import logging
import re
import time
from typing import Any

from app.config import get_settings
from app.rag.profile import allergy_notes, normalize
from app.rag.prompt import format_chunk, source_key
from app.rag.retriever import Retriever
from app.services.facility_service import SPECIALTIES, facility_card, search_facilities

logger = logging.getLogger(__name__)

FACILITY_TOOL = "find_healthcare_facility"
# Cuối kết quả find_healthcare_facility. Model không nhận địa chỉ / số điện thoại, nên không chép
# sai được; vẫn dặn để model không tự thêm từ kiến thức riêng.
FACILITY_REMINDER = (
	"(Danh sách hiện thành thẻ ngay dưới câu trả lời, kèm tên, địa chỉ, số điện thoại đã kiểm tra và "
	"nút chỉ đường. Khi trả lời: 1–3 câu dẫn, vd \"Dưới đây là một số cơ sở ở Hà Nội có chuyên khoa "
	"…\"; KHÔNG liệt kê tên cơ sở, KHÔNG viết địa chỉ hay số điện thoại; KHÔNG nói cơ sở nào tốt "
	"nhất; nhắc gọi trước để xác nhận lịch khám. Có dấu hiệu cấp cứu thì câu đầu tiên vẫn là gọi 115.)"
)
NO_FACILITIES = (
	"Không có cơ sở phù hợp trong dữ liệu. Dữ liệu chỉ gồm một số bệnh viện ở Hà Nội. Không tự kể tên "
	"cơ sở nào; khuyên người dùng hỏi cơ sở y tế gần nhà hoặc gọi 115 nếu cấp cứu."
)

NO_RESULTS = "Không có tài liệu phù hợp với truy vấn này."
# Nhắc lại ngay sau tài liệu, như build_rag_prompt đặt quy tắc cạnh CONTEXT. Chỉ nằm trong system
# instruction thì agent vẫn nêu liều khi tài liệu có ghi (eval 20/10: safety-05 "40mg hoặc hai lần
# mỗi ngày", pud-03 "liều 2 lần mỗi ngày") — pipeline cũ không lỗi lần nào trên cùng bộ câu.
DOSE_REMINDER = (
	"(Nhắc khi trả lời: KHÔNG nêu liều dùng thuốc — hàm lượng mg/g/ml/viên/nhát, số lần mỗi ngày, "
	"thời gian dùng, kể cả khi đoạn tài liệu trên có ghi và kể cả \"ví dụ\" hay đặt trong ngoặc. Được "
	"nêu tên thuốc / nhóm thuốc và lưu ý chung; liều do bác sĩ chỉ định.)"
)
# Cuối kết quả search_medical_docs khi hồ sơ ghi đang mang thai mà chưa lần tìm nào theo thai kỳ.
# Quy tắc này đã có trong profile_instruction, nhưng chỉ nằm trong system instruction thì agent
# lúc làm lúc không (eval 02/11/2026: profile-05 chỉ tìm "biến chứng tăng huyết áp", recall 0).
PREGNANCY_REMINDER = (
	"(Lưu ý hồ sơ: người hỏi ĐANG MANG THAI. CHỈ khi câu hỏi nói về bệnh / thuốc / triệu chứng của "
	"CHÍNH người hỏi (có \"tôi\", \"mình\", \"em\"…, vd \"Tôi bị tăng huyết áp có nguy hiểm không?\"): "
	"trước khi trả lời, gọi search_medical_docs thêm một lần theo thai kỳ, vd \"<bệnh> khi mang thai\", "
	"và nêu điều tài liệu nói về thai kỳ; không tìm thấy thì khuyên hỏi bác sĩ sản khoa, KHÔNG tự suy "
	"diễn. Câu hỏi kiến thức chung (\"Bệnh X là gì?\", \"X có triệu chứng gì?\") hoặc hỏi cho người "
	"khác (\"mẹ tôi…\", \"con tôi…\"): KHÔNG tìm thêm, KHÔNG nhắc thai kỳ.)"
)
# Truy vấn đã chứa một trong các cụm này (so sau khi bỏ dấu) thì coi như đã tìm theo thai kỳ.
# Không dùng "thai" đơn lẻ: bỏ dấu thì "đào thải" cũng thành "dao thai".
PREGNANCY_QUERY = re.compile(
	r"\b(?:mang thai|co thai|thai ky|thai nhi|thai phu|mang bau|ba bau|co bau|san phu|tien san giat|san giat)\b"
)
NO_NEW_RESULTS = (
	"Không có đoạn tài liệu MỚI nào phù hợp với truy vấn này, ngoài các đoạn đã có trong kết quả "
	"trước. Dùng các đoạn đã có, get_article để đọc thêm, hoặc tìm bằng truy vấn khác."
)
ARTICLE_MAX_CHARS = 8000


def tool_declarations(disease_groups: list[str]) -> dict[str, Any]:
	"""Khai báo tool cho Gemini. `disease_groups` lấy từ metadata của index (Retriever.disease_groups)."""
	search_properties: dict[str, Any] = {
		"query": {
			"type": "string",
			"description": (
				"Câu truy vấn ĐỘC LẬP, ĐỦ Ý bằng tiếng Việt, có tên bệnh / thuốc / thực phẩm cụ thể. "
				"Thay 'nó', 'bệnh này', 'vậy' bằng đúng tên được nhắc trong lịch sử. "
				"Mỗi lần chỉ tìm MỘT ý; câu hỏi nhiều ý thì gọi nhiều lần."
			),
		},
	}
	if disease_groups:
		search_properties["disease_group"] = {
			"type": "string",
			"format": "enum",
			"enum": disease_groups,
			"description": (
				"Tuỳ chọn: chỉ tìm trong một nhóm bệnh. CHỈ dùng khi chắc chắn câu hỏi thuộc đúng "
				"một nhóm trong danh sách; câu hỏi về bệnh không có trong danh sách, hoặc về nhiều "
				"bệnh, thì bỏ trống."
			),
		}
	return {
		"function_declarations": [
			{
				"name": "search_medical_docs",
				"description": (
					"Tìm các đoạn tài liệu y tế liên quan trong cơ sở dữ liệu. Trả về tối đa vài đoạn, "
					"mỗi đoạn có số [Nguồn i], tên bài, mục, document_id và URL; hoặc báo không có "
					"tài liệu phù hợp. Đoạn đã trả ở lần tìm trước sẽ không trả lại."
				),
				"parameters": {"type": "object", "properties": search_properties, "required": ["query"]},
			},
			{
				"name": "get_article",
				"description": (
					"Đọc nguyên một bài viết, hoặc một mục của bài, khi đoạn tìm được bị cắt giữa chừng "
					"hoặc câu trả lời nằm ở mục khác của cùng bài. Nhanh hơn nhiều so với tìm lại."
				),
				"parameters": {
					"type": "object",
					"properties": {
						"document_id": {
							"type": "string",
							"description": "Lấy đúng từ kết quả của search_medical_docs; không tự đặt.",
						},
						"section": {
							"type": "string",
							"description": "Tuỳ chọn: tên mục cần đọc (vd 'Điều trị'). Bỏ trống thì đọc cả bài.",
						},
					},
					"required": ["document_id"],
				},
			},
			{
				"name": FACILITY_TOOL,
				"description": (
					"Gợi ý bệnh viện / phòng khám ở HÀ NỘI theo chuyên khoa. Gọi khi người dùng hỏi nên "
					"khám ở đâu, bệnh viện nào, đi khám chỗ nào. Danh sách (địa chỉ, số điện thoại) hiện "
					"thành thẻ trên giao diện; bạn chỉ nhận tên các cơ sở. Chỉ có dữ liệu Hà Nội: người "
					"dùng nói đang ở tỉnh khác thì KHÔNG gọi."
				),
				"parameters": {
					"type": "object",
					"properties": {
						"specialty": {
							"type": "string",
							"format": "enum",
							"enum": list(SPECIALTIES),
							"description": (
								"Chuyên khoa theo bệnh / triệu chứng: tăng huyết áp, suy tim → tim_mach; dạ dày "
								"→ tieu_hoa; gout, viêm khớp → co_xuong_khop; suy thận, sỏi thận → "
								"than_tiet_nieu; hen → ho_hap; viêm da → da_lieu; viêm gan B → "
								"gan_truyen_nhiem; thai kỳ → san_khoa. Không rõ → noi_tong_quat."
							),
						},
						"emergency": {
							"type": "boolean",
							"description": "true nếu có dấu hiệu cấp cứu: chỉ lấy nơi có cấp cứu.",
						},
						"for_child": {
							"type": "boolean",
							"description": "true nếu người bệnh là trẻ em: ưu tiên bệnh viện nhi.",
						},
					},
					"required": ["specialty"],
				},
			},
		]
	}


class ToolSession:
	"""Trạng thái tool của một request."""

	def __init__(self, retriever: Retriever, allergies: list[str] | None = None, pregnant: bool = False) -> None:
		self._retriever = retriever
		self._allergies = allergies or []  # thuốc người dùng ghi dị ứng trong hồ sơ
		self._allergy_hits = 0
		self._pregnant = pregnant  # hồ sơ ghi đang mang thai → nhắc tìm theo thai kỳ (PREGNANCY_REMINDER)
		self._pregnancy_searched = False
		self._pregnancy_note_sent = False  # cả request
		self._pregnancy_reminded = False  # lần gọi tool hiện tại (ghi vào trace)
		self.results: list[dict] = []  # mọi chunk đã trả cho model, theo thứ tự đánh số
		self.trace: list[dict] = []  # vết chạy: mỗi lần gọi tool một dòng
		self._numbers: dict[str, int] = {}  # source_key → số [Nguồn i]
		self._seen_chunk_ids: set[str] = set()
		self._document_ids: set[str] = set()
		self._partial_sections = 0
		self.searches = 0  # số lần search_medical_docs đã chạy retriever
		self.facilities: list[dict] = []  # thẻ cơ sở y tế gửi xuống frontend (find_healthcare_facility)

	@property
	def has_content(self) -> bool:
		"""Request đã có gì để trả lời: chunk tài liệu hoặc danh sách cơ sở y tế. Chốt chặn "không có
		tài liệu" của agent dùng cái này, để câu dẫn sau khi gợi ý nơi khám không bị chặn nhầm."""
		return bool(self.results or self.facilities)

	def run(self, name: str, args: dict[str, Any]) -> str:
		"""Chạy một lời gọi tool, trả về text gửi lại cho model. Lỗi do model (tham số sai, id lạ)
		cũng trả về thành text để model tự sửa, không làm hỏng request."""
		started = time.perf_counter()
		before = len(self.results)
		entry: dict[str, Any] = {"tool": name, "args": args}
		self._partial_sections = 0
		self._allergy_hits = 0
		self._pregnancy_reminded = False
		try:
			if name == "search_medical_docs":
				output = self._search(str(args.get("query") or "").strip(), args.get("disease_group") or None)
			elif name == "get_article":
				output = self._get_article(str(args.get("document_id") or "").strip(), args.get("section") or None)
			elif name == FACILITY_TOOL:
				output = self._find_facility(
					str(args.get("specialty") or ""), bool(args.get("emergency")), bool(args.get("for_child"))
				)
				entry["n_facilities"] = len(self.facilities)
			else:
				output = f"Không có tool tên {name!r}."
				entry["error"] = "unknown_tool"
		except ToolError as exc:
			output = str(exc)
			entry["error"] = exc.code
		entry["n_chunks"] = len(self.results) - before
		if self._partial_sections:
			entry["partial_sections"] = self._partial_sections  # số mục được gợi ý đọc tiếp bằng get_article
		if self._allergy_hits:
			entry["allergy_hits"] = self._allergy_hits  # số thuốc dị ứng được nhắc cảnh báo
		if self._pregnancy_reminded:
			entry["pregnancy_reminder"] = True
		entry["seconds"] = round(time.perf_counter() - started, 2)
		self.trace.append(entry)
		logger.info("Tool %s(%s) → %d chunk, %.1f s", name, args, entry["n_chunks"], entry["seconds"])
		return output

	def _number(self, metadata: dict) -> int:
		return self._numbers.setdefault(source_key(metadata), len(self._numbers) + 1)

	def _find_facility(self, specialty: str, emergency: bool, for_child: bool) -> str:
		if specialty not in SPECIALTIES:
			raise ToolError(
				"bad_specialty", f"Chuyên khoa {specialty!r} không có. Chọn một trong: {', '.join(SPECIALTIES)}."
			)
		found = search_facilities(specialty, emergency=emergency, for_child=for_child)
		if not found:
			return NO_FACILITIES
		# Gọi nhiều lần (vd hai chuyên khoa) thì gộp, không lặp cơ sở.
		have = {item["id"] for item in self.facilities}
		self.facilities += [facility_card(item) for item in found if item["id"] not in have]
		# Chỉ báo số lượng, không gửi tên: có tên thì model chép lại thành danh sách trùng với thẻ
		# (eval 01/11: 4/8 câu), và giám khảo chấm danh sách đó là claim không có nguồn.
		exact = sum(item["matched"] == "chuyen_khoa" for item in found)
		parts = [f"{exact} cơ sở có chuyên khoa {SPECIALTIES[specialty]}"]
		if len(found) > exact:
			parts.append(f"{len(found) - exact} bệnh viện đa khoa")
		summary = f"Đã tìm được {len(found)} cơ sở ở Hà Nội ({', '.join(parts)})" + (
			", đều có cấp cứu" if emergency else ""
		) + (", ưu tiên nơi khám trẻ em" if for_child else "") + "."
		return f"{summary}\n\n{FACILITY_REMINDER}"

	def _search(self, query: str, disease_group: str | None) -> str:
		if not query:
			raise ToolError("empty_query", "Thiếu tham số query.")
		if disease_group and disease_group not in self._retriever.disease_groups:
			raise ToolError(
				"bad_disease_group",
				f"Nhóm bệnh {disease_group!r} không có. Các nhóm hợp lệ: {', '.join(self._retriever.disease_groups)}. "
				"Có thể bỏ trống disease_group.",
			)
		# Bước 2 (thời gian): chunk đã gửi bị loại TRƯỚC khi rerank; từ lần tìm thứ hai chỉ rerank
		# AGENT_FOLLOWUP_RERANK_CANDIDATES ứng viên (lần sau thường là truy vấn hẹp cho một ý).
		settings = get_settings()
		rerank_candidates = (
			settings.agent_followup_rerank_candidates if self.searches else settings.rerank_candidates
		)
		self.searches += 1
		if PREGNANCY_QUERY.search(normalize(query)):
			self._pregnancy_searched = True
		fresh = self._retriever.search(
			query, disease_group=disease_group, exclude_ids=self._seen_chunk_ids, rerank_candidates=rerank_candidates
		)
		pregnancy = self._pregnancy_note()
		if not fresh:
			# Có thể đoạn phù hợp nhất đã được gửi ở lần trước nên không còn đoạn MỚI nào đủ điểm.
			empty = NO_NEW_RESULTS if self._seen_chunk_ids else NO_RESULTS
			return "\n\n".join([empty] + pregnancy)
		blocks = []
		for result in fresh:
			self._seen_chunk_ids.add(result["id"])
			self._document_ids.add(result["metadata"].get("document_id", ""))
			self.results.append(result)
			block = format_chunk(result, self._number(result["metadata"]), with_document_id=True)
			parts = self._section_parts(result)
			if len(parts) > 1:
				# Đánh dấu ngay trên dòng tiêu đề: model thấy đoạn này chỉ là một phần của mục.
				header, _, rest = block.partition("\n")
				block = f"{header} | Đoạn {parts.index(result['id']) + 1}/{len(parts)} của mục\n{rest}"
			blocks.append(block)
		notes = self._partial_section_notes(fresh)
		self._partial_sections = len(notes)
		return "\n\n".join(blocks + notes + self._allergy_notes(fresh) + pregnancy + [DOSE_REMINDER])

	def _pregnancy_note(self) -> list[str]:
		"""PREGNANCY_REMINDER nếu đang mang thai, chưa tìm theo thai kỳ, chưa nhắc và còn lượt tìm.
		Chỉ nhắc một lần mỗi request: câu hỏi cho người khác thì model đã bỏ qua một lần là đủ."""
		if not self._pregnant or self._pregnancy_searched or self._pregnancy_note_sent:
			return []
		if self.searches >= get_settings().agent_max_tool_calls:
			return []  # hết lượt tìm: nhắc cũng không tìm thêm được
		self._pregnancy_reminded = True
		self._pregnancy_note_sent = True
		return [PREGNANCY_REMINDER]

	def _allergy_notes(self, results: list[dict]) -> list[str]:
		notes = allergy_notes(results, self._allergies, self._number)
		self._allergy_hits = len(notes)
		return notes

	def _section_parts(self, result: dict) -> list[str]:
		metadata = result["metadata"]
		return self._retriever.section_chunk_ids(metadata.get("document_id", ""), metadata.get("section", ""))

	def _partial_section_notes(self, fresh: list[dict]) -> list[str]:
		"""Mục bị chunker cắt thành nhiều đoạn mà request này chưa có đủ → gợi ý get_article,
		vì câu trả lời (vd danh sách "8 thói quen…") có thể nằm ở đoạn còn thiếu."""
		notes = []
		done: set[tuple[str, str]] = set()
		for result in fresh:
			metadata = result["metadata"]
			key = (metadata.get("document_id", ""), metadata.get("section", ""))
			parts = self._section_parts(result)
			if key in done or len(parts) <= 1:
				continue
			done.add(key)
			have = [i for i, chunk_id in enumerate(parts, start=1) if chunk_id in self._seen_chunk_ids]
			if len(have) == len(parts):
				continue
			notes.append(
				f"(Mục \"{key[1]}\" của [Nguồn {self._number(metadata)}] gồm {len(parts)} đoạn, mới có đoạn "
				f"{', '.join(map(str, have))}. Nếu câu hỏi cần phần còn lại của mục này, gọi get_article với "
				f"document_id=\"{key[0]}\", section=\"{key[1]}\".)"
			)
		return notes

	def _get_article(self, document_id: str, section: str | None) -> str:
		if document_id not in self._document_ids:
			raise ToolError(
				"unknown_document_id",
				f"document_id {document_id!r} chưa xuất hiện trong kết quả tìm kiếm. "
				"Chỉ dùng document_id lấy từ kết quả của search_medical_docs.",
			)
		article = self._retriever.get_article(document_id)
		if article is None:
			raise ToolError("unknown_document_id", f"Không tìm thấy bài {document_id!r}.")
		headings = [item["heading"] for item in article["sections"]]
		sections = article["sections"]
		if section:
			sections = _match_sections(sections, section)
			if not sections:
				raise ToolError(
					"unknown_section",
					f"Bài không có mục {section!r}. Các mục của bài: {'; '.join(headings)}.",
				)

		# Chunk của các mục đã đọc không cần gửi lại ở lần tìm sau.
		for item in sections:
			self._seen_chunk_ids.update(self._retriever.section_chunk_ids(document_id, item["heading"]))
		index = self._number(article)
		body = "\n\n".join(f"## {item['heading']}\n{item['content']}" for item in sections)
		truncated = len(body) > ARTICLE_MAX_CHARS
		if truncated:
			body = body[:ARTICLE_MAX_CHARS] + "…\n(đã cắt, gọi lại với `section` để đọc phần còn lại)"
		# Đưa nội dung bài vào results như một chunk, để đánh giá thấy được context model đã đọc.
		chunk = {
			"id": f"{document_id}#{section or 'article'}",
			"text": body,
			"metadata": {
				**{key: article.get(key, "") for key in ("document_id", "title", "url", "citation", "disease_group")},
				"section": ", ".join(item["heading"] for item in sections) if section else "Toàn bài",
			},
		}
		self.results.append(chunk)
		notes = "".join(f"{note}\n\n" for note in self._allergy_notes([chunk]))
		return (
			f"[Nguồn {index}] {article['title']} | document_id: {document_id}\n"
			f"URL: {article['url']}\n"
			f"Các mục của bài: {'; '.join(headings)}\n\n{body}\n\n{notes}{DOSE_REMINDER}"
		)


def _match_sections(sections: list[dict], wanted: str) -> list[dict]:
	"""Mục trùng tên (không phân biệt hoa thường); không có thì mục có tên chứa `wanted`."""
	wanted = wanted.strip().casefold()
	exact = [item for item in sections if item["heading"].strip().casefold() == wanted]
	return exact or [item for item in sections if wanted in item["heading"].casefold()]


class ToolError(Exception):
	def __init__(self, code: str, message: str) -> None:
		super().__init__(message)
		self.code = code
