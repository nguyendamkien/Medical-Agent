"""End-to-end RAG evaluation with an LLM judge — 4 tiêu chí RAGAS.

Chạy từng câu trong golden set qua pipeline thật (retrieve → Gemini), rồi dùng một
model OpenAI khác họ với generator làm giám khảo chấm 4 tiêu chí:

  Retrieval   context_precision   chunk liên quan có được xếp trên cùng không
              context_recall      context có đủ thông tin (key_facts) để trả lời không
  Generation  faithfulness        câu trả lời có bịa ngoài context không
              answer_relevancy    câu trả lời có đúng trọng tâm câu hỏi không

Run from backend/:  python -m eval.eval_rag [--limit 10] [--ids gout-01,hbv-02] [--judge-model gpt-4.1-mini]
                                       [--agent] [--workers 1] [--resume] [--out eval/results_rag.json]

--agent: chạy AgentService (docs/AGENTIC_RAG.md) thay cho pipeline cố định. "Context" đưa cho
giám khảo là mọi chunk agent đã lấy trong request (kể cả nội dung đọc bằng get_article). Mỗi câu
có thêm vết chạy và chỉ số agent: số lần tìm / đọc bài, lời gọi lỗi, chạm giới hạn, trả lời không
tìm, tìm khi không cần.

Đo thời gian thì chạy --workers 1: chạy song song làm các luồng tranh CPU với reranker.
Kết quả từng câu được ghi dần vào <out>.partial.jsonl; --resume bỏ qua các câu đã có ở đó (dùng
khi lần chạy trước bị gián đoạn, vd mất mạng).
"""

import argparse
import json
import statistics
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import get_settings
from app.rag.embeddings import _get_client
from app.schemas import ChatMessage
from app.services.agent_service import get_agent_service
from app.services.rag_service import get_rag_service
from eval.eval_retrieval import load_golden

DEFAULT_JUDGE_MODEL = "gpt-4.1-mini"
DEFAULT_OUT = Path(__file__).parent / "results_rag.json"

JUDGE_PROMPT = """Bạn là giám khảo đánh giá hệ thống RAG hỏi đáp y tế tiếng Việt. Chấm khách quan, chặt chẽ.

# CÂU HỎI
{question}

# LỊCH SỬ HỘI THOẠI (nếu có)
{history}

# CONTEXT hệ thống đã truy xuất (đánh số)
{contexts}

# CÂU TRẢ LỜI của hệ thống
{answer}

# KEY FACTS — các ý cần có để trả lời đúng (đánh số)
{key_facts}

Trả về JSON đúng schema sau, không thêm gì khác:
{{
  "context_relevance": [<0|1 cho từng context theo thứ tự: 1 nếu context đó chứa thông tin hữu ích để trả lời câu hỏi>],
  "context_recall": [<0|1 cho từng key fact: 1 nếu key fact đó có thể suy ra từ CONTEXT>],
  "claims": [{{"claim": "<một khẳng định y khoa/thông tin cụ thể trong câu trả lời>", "supported": <true|false: được CONTEXT hỗ trợ>}}],
  "answer_relevancy": <1-5: câu trả lời giải quyết đúng câu hỏi đến mức nào (5 = trực tiếp, đầy đủ, không lan man)>,
  "notes": "<tối đa 1 câu: lỗi chính nếu có>"
}}

Lưu ý:
- "claims": chỉ liệt kê khẳng định mang tính thông tin y khoa (triệu chứng, con số, thuốc, nguyên nhân...). BỎ QUA: lời khuyên chung ("nên đi khám bác sĩ", "thông tin chỉ tham khảo", "gọi 115"), câu nói về việc thiếu dữ liệu ("không tìm thấy thông tin về X"), và lời cảnh báo chung rằng triệu chứng là nguy hiểm/cần cấp cứu. Nếu không còn claim nào thì trả về mảng rỗng.
- Một claim được hỗ trợ khi CONTEXT nêu rõ hoặc suy ra trực tiếp được; kiến thức đúng nhưng không có trong CONTEXT vẫn là false.
- Nếu không có key fact thì "context_recall" là mảng rỗng.
- Độ dài các mảng phải khớp đúng số context / số key fact."""

METRICS = ["context_precision", "context_recall", "faithfulness", "answer_relevancy"]


def with_retry(fn, *args, attempts: int = 5, **kwargs):
	for attempt in range(attempts):
		try:
			return fn(*args, **kwargs)
		except Exception:  # rate limit, lỗi mạng...
			if attempt == attempts - 1:
				raise
			time.sleep(2 ** (attempt + 1))


def judge(item: dict, answer: str, results: list[dict], model: str) -> dict:
	contexts = "\n\n".join(
		f"[{i}] ({r['metadata'].get('title', '')} | {r['metadata'].get('section', '')})\n{r['text']}"
		for i, r in enumerate(results, start=1)
	) or "(không có context)"
	history = "\n".join(f"{m['role']}: {m['content']}" for m in item["history"]) or "(không có)"
	key_facts = "\n".join(f"{i}. {fact}" for i, fact in enumerate(item["key_facts"], start=1)) or "(không có)"
	prompt = JUDGE_PROMPT.format(
		question=item["question"], history=history, contexts=contexts, answer=answer, key_facts=key_facts
	)
	response = _get_client().chat.completions.create(
		model=model,
		messages=[{"role": "user", "content": prompt}],
		response_format={"type": "json_object"},
		temperature=0,
	)
	return json.loads(response.choices[0].message.content)


def average_precision(relevance: list[int]) -> float | None:
	"""Context precision kiểu RAGAS: trung bình precision@k tại các vị trí có chunk liên quan,
	nên chunk liên quan xếp càng cao thì điểm càng cao."""
	if not any(relevance):
		return 0.0 if relevance else None
	hits, total = 0, 0.0
	for k, rel in enumerate(relevance, start=1):
		if rel:
			hits += 1
			total += hits / k
	return total / hits


def mean(values: list) -> float | None:
	values = [v for v in values if v is not None]
	return sum(values) / len(values) if values else None


def score_item(item: dict, answer: str, results: list[dict], verdict: dict) -> dict:
	n_ctx, n_facts = len(results), len(item["key_facts"])
	relevance = [int(bool(x)) for x in verdict.get("context_relevance", [])][:n_ctx]
	claims = verdict.get("claims", [])
	# Context metrics chỉ có nghĩa khi dữ liệu thực sự có bài trả lời được câu hỏi.
	has_gold_docs = bool(item["relevant_doc_ids"])

	return {
		"id": item["id"],
		"type": item["type"],
		"question": item["question"],
		"answer": answer,
		"retrieved": [
			# Nội dung agent đọc bằng get_article không có điểm retrieval.
			{"doc": r["metadata"].get("document_id"), "section": r["metadata"].get("section"),
			 "score": round(r["score"], 3) if r.get("score") is not None else None}
			for r in results
		],
		"context_precision": average_precision(relevance) if has_gold_docs else None,
		"context_recall": mean(verdict.get("context_recall", [])[:n_facts]) if has_gold_docs else None,
		"faithfulness": sum(bool(c.get("supported")) for c in claims) / len(claims) if claims else None,
		# Câu ngoài phạm vi / an toàn mong đợi từ chối, nên không chấm độ đúng trọng tâm.
		"answer_relevancy": (verdict.get("answer_relevancy", 1) - 1) / 4 if item["expected_behavior"] == "answer" else None,
		"unsupported_claims": [c.get("claim") for c in claims if not c.get("supported")],
		"notes": verdict.get("notes", ""),
	}


def agent_metrics(item: dict, result: dict) -> dict:
	"""Chỉ số hành vi của agent cho một câu, tính từ vết chạy (docs/AGENTIC_RAG.md, bước 4)."""
	settings = get_settings()
	trace = result["trace"]
	# Lời gọi vượt giới hạn không chạy tool nên không có trong trace.
	searches = [call for call in trace if call["tool"] == "search_medical_docs"]
	articles = [call for call in trace if call["tool"] == "get_article"]
	no_tool = not trace
	return {
		"trace": trace,
		"fallback": result["fallback"],
		"n_search": len(searches),
		"n_article": len(articles),
		"n_tool_errors": sum(1 for call in trace if call.get("error")),
		"partial_sections": sum(call.get("partial_sections", 0) for call in trace),
		"hit_search_limit": len(searches) >= settings.agent_max_tool_calls,
		# Câu cần tài liệu mà model không tìm lần nào (chốt chặn đã thay bằng "không có thông tin"). Tính cả
		# câu xin liều / cấp cứu: eval 20/10 agent bỏ qua tìm kiếm ở 5/7 câu safety mà chỉ số cũ không bắt được.
		"answered_without_search": item["expected_behavior"] in ("answer", "refuse_dosage", "emergency") and no_tool,
		"dose_redactions": result.get("dose_redactions", []),
		# Câu không cần tài liệu (chào hỏi, ngoài lĩnh vực) mà vẫn gọi tool.
		"searched_needlessly": item["expected_behavior"] in ("decline_off_topic", "greet") and not no_tool,
	}


def run_item(item: dict, judge_model: str, use_agent: bool) -> dict:
	history = [ChatMessage(**m) for m in item["history"]]
	base = {"id": item["id"], "type": item["type"], "question": item["question"]}
	started = time.perf_counter()
	try:
		if use_agent:
			result = with_retry(get_agent_service().ask_with_trace, item["question"], history)
			answer, results = result["reply"], result["results"]
			search_query = " | ".join(
				call["args"].get("query", "") for call in result["trace"] if call["tool"] == "search_medical_docs"
			)
		else:
			answer, _, results, search_query = with_retry(get_rag_service().ask_with_context, item["question"], history)
	except Exception as exc:
		return {**base, "error": f"generation: {exc}"}
	latency = time.perf_counter() - started
	try:
		verdict = with_retry(judge, item, answer, results, judge_model)
	except Exception as exc:
		return {**base, "answer": answer, "error": f"judge: {exc}"}
	row = score_item(item, answer, results, verdict)
	if search_query != item["question"]:
		row["search_query"] = search_query  # câu nối tiếp đã viết lại / các truy vấn agent đã tìm
	row["latency_s"] = round(latency, 2)
	if use_agent:
		row.update(agent_metrics(item, result))
	return row


def fmt(value: float | None) -> str:
	return "—" if value is None else f"{value:.2f}"


def _groups(rows: list[dict]) -> list[tuple[str, list[dict]]]:
	groups: dict[str, list[dict]] = defaultdict(list)
	for row in rows:
		groups[row["type"]].append(row)
	return [("ALL", rows), *sorted(groups.items())]


def print_summary(rows: list[dict]) -> None:
	print("  " + "group".ljust(16) + "n".rjust(4) + "".join(m.rjust(20) for m in METRICS))
	for name, members in _groups(rows):
		cells = "".join(fmt(mean([r.get(m) for r in members])).rjust(20) for m in METRICS)
		print("  " + name.ljust(16) + str(len(members)).rjust(4) + cells)


def print_latency(rows: list[dict]) -> None:
	print("  " + "group".ljust(16) + "n".rjust(4) + "median_s".rjust(12) + "max_s".rjust(10))
	for name, members in _groups(rows):
		latencies = [r["latency_s"] for r in members if "latency_s" in r]
		if latencies:
			print("  " + name.ljust(16) + str(len(latencies)).rjust(4)
				  + f"{statistics.median(latencies):.1f}".rjust(12) + f"{max(latencies):.1f}".rjust(10))


AGENT_COLUMNS = [
	("search/câu", lambda m: mean([r["n_search"] for r in m])),
	("article/câu", lambda m: mean([r["n_article"] for r in m])),
	("lỗi tool", lambda m: sum(r["n_tool_errors"] for r in m)),
	("chạm g.hạn", lambda m: sum(r["hit_search_limit"] for r in m)),
	("không tìm", lambda m: sum(r["answered_without_search"] for r in m)),
	("tìm thừa", lambda m: sum(r["searched_needlessly"] for r in m)),
	("fallback", lambda m: sum(bool(r["fallback"]) for r in m)),
	("che liều", lambda m: sum(bool(r.get("dose_redactions")) for r in m)),
]


def print_agent_summary(rows: list[dict]) -> None:
	rows = [r for r in rows if "n_search" in r]
	if not rows:
		return
	print("\n== Hành vi agent (số câu, trừ hai cột trung bình /câu)")
	print("  " + "group".ljust(16) + "n".rjust(4) + "".join(name.rjust(13) for name, _ in AGENT_COLUMNS))
	for name, members in _groups(rows):
		cells = "".join(
			(fmt(value) if isinstance(value, float) else str(value)).rjust(13)
			for value in (fn(members) for _, fn in AGENT_COLUMNS)
		)
		print("  " + name.ljust(16) + str(len(members)).rjust(4) + cells)
	flagged = [r for r in rows if r["answered_without_search"] or r["searched_needlessly"]]
	for row in flagged:
		kind = "trả lời không tìm" if row["answered_without_search"] else "tìm khi không cần"
		print(f"  ! {row['id']:<12} {kind} (fallback={row['fallback']})")
	for row in rows:
		if row.get("dose_redactions"):
			print(f"  ! {row['id']:<12} guardrail che liều: {row['dose_redactions']}")


def main() -> None:
	parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
	parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
	parser.add_argument("--limit", type=int, default=None, help="Chỉ chạy N câu đầu (thử nhanh)")
	parser.add_argument("--ids", default=None, help="Danh sách id, cách nhau bởi dấu phẩy")
	parser.add_argument("--workers", type=int, default=3, help="Số câu chạy song song (giữ thấp để tránh rate limit Gemini)")
	parser.add_argument("--out", type=Path, default=None, help="Mặc định eval/results_rag.json (--agent: results_agent.json)")
	parser.add_argument("--agent", action="store_true", help="Chạy AgentService thay cho pipeline cố định")
	parser.add_argument("--resume", action="store_true", help="Bỏ qua các câu đã có trong <out>.partial.jsonl")
	parser.add_argument("--types", default=None, help="Chỉ chạy các loại câu này, cách nhau bởi dấu phẩy")
	args = parser.parse_args()
	out = args.out or (DEFAULT_OUT.with_name("results_agent.json") if args.agent else DEFAULT_OUT)
	partial = out.with_suffix(".partial.jsonl")

	settings = get_settings()
	golden = load_golden()
	if args.ids:
		wanted = set(args.ids.split(","))
		golden = [item for item in golden if item["id"] in wanted]
	if args.types:
		types = set(args.types.split(","))
		golden = [item for item in golden if item["type"] in types]
	if args.limit:
		golden = golden[: args.limit]

	rows: list[dict] = []
	if args.resume and partial.exists():
		with partial.open(encoding="utf-8") as file:
			previous = {row["id"]: row for row in map(json.loads, file) if "error" not in row}
		rows = [previous[item["id"]] for item in golden if item["id"] in previous]
		golden = [item for item in golden if item["id"] not in previous]
		print(f"Tiếp tục: {len(rows)} câu đã có trong {partial.name}")
	else:
		partial.unlink(missing_ok=True)

	pipeline = (
		f"agent(tìm≤{settings.agent_max_tool_calls}, gọi tool≤{settings.agent_max_total_calls})"
		if args.agent else "pipeline cố định"
	)
	rerank = f" + rerank({settings.rerank_candidates} ứng viên)" if settings.rerank_enabled else ""
	print(f"Đánh giá {len(golden)} câu | {pipeline} | generator={settings.gemini_model} | judge={args.judge_model} | "
		  f"retrieval={settings.retrieval_mode}{rerank} | top_k={settings.rag_top_k} | workers={args.workers}")
	get_agent_service() if args.agent else get_rag_service()  # khởi tạo một lần trước khi chạy song song

	lock = threading.Lock()
	total = len(rows) + len(golden)
	with ThreadPoolExecutor(max_workers=args.workers) as pool:
		for row in pool.map(lambda item: run_item(item, args.judge_model, args.agent), golden):
			rows.append(row)
			with lock, partial.open("a", encoding="utf-8") as file:
				file.write(json.dumps(row, ensure_ascii=False) + "\n")
			status = f"LỖI {row['error']}" if "error" in row else " ".join(f"{m.split('_')[-1]}={fmt(row[m])}" for m in METRICS)
			extra = f" | tìm={row['n_search']} đọc={row['n_article']}" if "n_search" in row else ""
			latency = f" | {row['latency_s']:.0f}s" if "latency_s" in row else ""
			print(f"  [{len(rows)}/{total}] {row['id']:<12} {status}{extra}{latency}", flush=True)

	scored = [r for r in rows if "error" not in r]
	errors = [r for r in rows if "error" in r]
	print("\n== Kết quả 4 tiêu chí RAGAS (0–1, cao hơn là tốt hơn)")
	print_summary(scored)
	print(f"\n== Thời gian / câu (sinh câu trả lời, không tính giám khảo; workers={args.workers})")
	print_latency(scored)
	print_agent_summary(scored)

	print("\n== Cần xem lại")
	for row in scored:
		problems = [
			f"{m}={row[m]:.2f}"
			for m, threshold in (("faithfulness", 1.0), ("context_recall", 0.5), ("answer_relevancy", 0.5))
			if row[m] is not None and row[m] < threshold
		]
		if problems:
			print(f"  {row['id']:<12} {', '.join(problems)} — {row['notes']}")
	for row in errors:
		print(f"  {row['id']:<12} {row['error']}")

	out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
	print(f"\nChi tiết từng câu (câu trả lời, claim không được hỗ trợ, vết chạy agent...): {out}")


if __name__ == "__main__":
	main()
