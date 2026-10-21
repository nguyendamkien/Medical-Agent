"""Tool của agent (xem docs/AGENTIC_RAG.md): khai báo cho Gemini và code chạy từng tool.

Hai tool, cả hai chỉ đọc dữ liệu đã có trong index:

    search_medical_docs(query, disease_group?)  bọc Retriever.search (hybrid + rerank + ngưỡng)
    get_article(document_id, section?)          đọc cả bài / một mục, ghép từ các chunk trong RAM

ToolSession giữ trạng thái của MỘT request, những việc không giao cho model:
- Đánh số [Nguồn i] xuyên suốt request: chunk của bài đã gặp dùng lại số cũ, bài mới đánh số
  tiếp. `results` giữ mọi chunk đã trả cho model theo đúng thứ tự đó, nên collect_sources(results)
  khớp với số trong câu trả lời.
- Không gửi lại chunk đã trả ở lần tìm trước.
- get_article chỉ nhận document_id đã xuất hiện trong kết quả tìm kiếm của request này.
"""

import logging
import time
from typing import Any

from app.config import get_settings
from app.rag.prompt import format_chunk, source_key
from app.rag.retriever import Retriever

logger = logging.getLogger(__name__)

NO_RESULTS = "Không có tài liệu phù hợp với truy vấn này."
# Nhắc lại ngay sau tài liệu, như build_rag_prompt đặt quy tắc cạnh CONTEXT. Chỉ nằm trong system
# instruction thì agent vẫn nêu liều khi tài liệu có ghi (eval 20/10: safety-05 "40mg hoặc hai lần
# mỗi ngày", pud-03 "liều 2 lần mỗi ngày") — pipeline cũ không lỗi lần nào trên cùng bộ câu.
DOSE_REMINDER = (
	"(Nhắc khi trả lời: KHÔNG nêu liều dùng thuốc — hàm lượng mg/g/ml/viên/nhát, số lần mỗi ngày, "
	"thời gian dùng, kể cả khi đoạn tài liệu trên có ghi và kể cả \"ví dụ\" hay đặt trong ngoặc. Được "
	"nêu tên thuốc / nhóm thuốc và lưu ý chung; liều do bác sĩ chỉ định.)"
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
		]
	}


class ToolSession:
	"""Trạng thái tool của một request."""

	def __init__(self, retriever: Retriever) -> None:
		self._retriever = retriever
		self.results: list[dict] = []  # mọi chunk đã trả cho model, theo thứ tự đánh số
		self.trace: list[dict] = []  # vết chạy: mỗi lần gọi tool một dòng
		self._numbers: dict[str, int] = {}  # source_key → số [Nguồn i]
		self._seen_chunk_ids: set[str] = set()
		self._document_ids: set[str] = set()
		self._partial_sections = 0
		self.searches = 0  # số lần search_medical_docs đã chạy retriever

	def run(self, name: str, args: dict[str, Any]) -> str:
		"""Chạy một lời gọi tool, trả về text gửi lại cho model. Lỗi do model (tham số sai, id lạ)
		cũng trả về thành text để model tự sửa, không làm hỏng request."""
		started = time.perf_counter()
		before = len(self.results)
		entry: dict[str, Any] = {"tool": name, "args": args}
		self._partial_sections = 0
		try:
			if name == "search_medical_docs":
				output = self._search(str(args.get("query") or "").strip(), args.get("disease_group") or None)
			elif name == "get_article":
				output = self._get_article(str(args.get("document_id") or "").strip(), args.get("section") or None)
			else:
				output = f"Không có tool tên {name!r}."
				entry["error"] = "unknown_tool"
		except ToolError as exc:
			output = str(exc)
			entry["error"] = exc.code
		entry["n_chunks"] = len(self.results) - before
		if self._partial_sections:
			entry["partial_sections"] = self._partial_sections  # số mục được gợi ý đọc tiếp bằng get_article
		entry["seconds"] = round(time.perf_counter() - started, 2)
		self.trace.append(entry)
		logger.info("Tool %s(%s) → %d chunk, %.1f s", name, args, entry["n_chunks"], entry["seconds"])
		return output

	def _number(self, metadata: dict) -> int:
		return self._numbers.setdefault(source_key(metadata), len(self._numbers) + 1)

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
		fresh = self._retriever.search(
			query, disease_group=disease_group, exclude_ids=self._seen_chunk_ids, rerank_candidates=rerank_candidates
		)
		if not fresh:
			if self._seen_chunk_ids:
				# Có thể đoạn phù hợp nhất đã được gửi ở lần trước nên không còn đoạn MỚI nào đủ điểm.
				return NO_NEW_RESULTS
			return NO_RESULTS
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
		return "\n\n".join(blocks + notes + [DOSE_REMINDER])

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
		self.results.append({
			"id": f"{document_id}#{section or 'article'}",
			"text": body,
			"metadata": {
				**{key: article.get(key, "") for key in ("document_id", "title", "url", "citation", "disease_group")},
				"section": ", ".join(item["heading"] for item in sections) if section else "Toàn bài",
			},
		})
		return (
			f"[Nguồn {index}] {article['title']} | document_id: {document_id}\n"
			f"URL: {article['url']}\n"
			f"Các mục của bài: {'; '.join(headings)}\n\n{body}\n\n{DOSE_REMINDER}"
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
