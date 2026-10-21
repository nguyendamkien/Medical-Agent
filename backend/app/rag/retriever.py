"""Hybrid retrieval: BM25 (từ khoá) + dense (ngữ nghĩa), gộp bằng RRF, tuỳ chọn rerank.

Luồng xử lý một câu hỏi (RETRIEVAL_MODE=hybrid):

    câu hỏi ─┬─► Dense: embed câu hỏi → Chroma (cosine) ─► top-N ─┐
             │                                                     ├─► RRF ─► [rerank] ─► top-k
             └─► BM25: tách token → chỉ mục BM25 trong RAM ─► top-N ┘

1. Dense (ngữ nghĩa) — text-embedding-3-small + Chroma HNSW, khoảng cách cosine.
   Hiểu từ đồng nghĩa / cách diễn đạt khác ("cao huyết áp" ≈ "tăng huyết áp"),
   nhưng dễ bỏ lỡ thuật ngữ hiếm, mã xét nghiệm, tên thuốc ("tophi", "HBsAg").

2. BM25 (từ khoá) — xem app/rag/bm25.py. Bắt chính xác thuật ngữ hiếm nhờ IDF,
   nhưng không hiểu đồng nghĩa. Chỉ mục dựng trong RAM từ chính các chunk trong
   Chroma lúc khởi tạo Retriever, nên luôn khớp với index hiện tại.

3. Gộp bằng Reciprocal Rank Fusion (RRF). Điểm BM25 (vd 12.7) và cosine (vd 0.68)
   nằm trên hai thang khác nhau nên không cộng thẳng được; RRF chỉ dùng THỨ HẠNG:

       rrf(d) = Σ_{nhánh r chứa d}  1 / (RRF_K + rank_r(d))        (rank bắt đầu từ 1)

   - Chunk xếp cao ở cả hai nhánh → điểm cao nhất.
   - Chunk chỉ có ở một nhánh vẫn được tính (nhánh kia đóng góp 0), nên thuật ngữ hiếm
     mà dense bỏ lỡ vẫn có cơ hội lọt top.
   - RRF_K = 60 (giá trị chuẩn trong tài liệu gốc của RRF) làm phẳng chênh lệch giữa
     hạng 1 và hạng 2, để một nhánh không lấn át hoàn toàn nhánh kia.
   - Mỗi nhánh lấy RAG_CANDIDATES (mặc định 20) ứng viên trước khi gộp.

4. Rerank (tuỳ chọn, RERANK_ENABLED=true) — cross-encoder chấm lại RERANK_CANDIDATES
   ứng viên RRF tốt nhất rồi giữ top-k (xem app/rag/reranker.py). Tắt thì lấy top-k
   theo điểm RRF.

5. Ngưỡng "không có tài liệu phù hợp" (áp cho cả câu hỏi, không cho từng chunk):
   - Luôn kiểm tra trước: cosine tốt nhất của nhánh dense < RAG_MIN_SCORE → trả về rỗng.
   - Khi bật rerank, thêm: điểm rerank tốt nhất < RERANK_MIN_SCORE → trả về rỗng. Đo trên
     golden set: câu có đáp án đúng có điểm rerank ≥ 0.73, câu ngoài phạm vi ≤ 0.17, trong
     khi cosine của hai nhóm chồng lên nhau (0.48 và 0.52) nên không tách được.

Mỗi chunk trả về có:
    score   điểm dùng để xếp hạng cuối cùng (rerank nếu bật, ngược lại là RRF)
    scores  điểm từng thành phần để debug: dense, bm25, dense_rank, bm25_rank, rrf, rerank

RETRIEVAL_MODE=dense giữ hành vi cũ: chỉ dense, lọc từng chunk theo RAG_MIN_SCORE.

Lọc theo nhóm bệnh (search(..., disease_group=...)): nhánh dense lọc bằng `where` của Chroma,
nhánh BM25 chấm điểm toàn bộ rồi bỏ chunk khác nhóm — cả hai nhánh vẫn lấy đủ N ứng viên
trong nhóm. Danh sách nhóm hợp lệ (Retriever.disease_groups) đọc từ metadata của index.
Sau khi chạy lại ingest cần khởi động lại server để dựng lại chỉ mục BM25.
"""

from app.config import get_settings
from app.rag.bm25 import BM25Index
from app.rag.chunker import chunk_prefix
from app.rag.embeddings import embed_query
from app.rag.reranker import get_reranker
from app.rag.vector_store import ChromaVectorStore


def reciprocal_rank_fusion(rankings: dict[str, list[str]], k: int) -> dict[str, float]:
	"""Gộp nhiều danh sách chunk_id đã xếp hạng thành {chunk_id: điểm RRF}."""
	fused: dict[str, float] = {}
	for ranking in rankings.values():
		for rank, chunk_id in enumerate(ranking, start=1):
			fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + rank)
	return fused


# Chunker lặp lại tối đa 300 ký tự cuối của chunk trước ở đầu chunk sau (overlap_chars).
MAX_OVERLAP_CHARS = 400
MIN_OVERLAP_CHARS = 20  # ngắn hơn thì coi là trùng ngẫu nhiên, không cắt


def _chunk_order(chunk_id: str) -> tuple[str, int]:
	"""ID chunk là "{document_id}-{số thứ tự}" — sắp theo bài rồi theo vị trí trong bài."""
	document_id, _, index = chunk_id.rpartition("-")
	return document_id, int(index) if index.isdigit() else 0


def _join_overlapping(previous: str, following: str) -> str:
	"""Nối hai chunk liền nhau của cùng một section, bỏ đoạn overlap bị lặp."""
	for size in range(min(len(previous), len(following), MAX_OVERLAP_CHARS), MIN_OVERLAP_CHARS - 1, -1):
		if previous.endswith(following[:size]):
			return previous + following[size:]
	return f"{previous} {following}"


class Retriever:
	def __init__(self, store: ChromaVectorStore | None = None, mode: str | None = None, rerank: bool | None = None) -> None:
		settings = get_settings()
		self._store = store or ChromaVectorStore()
		self.mode = mode or settings.retrieval_mode
		self.rerank_enabled = settings.rerank_enabled if rerank is None else rerank
		if self.mode not in ("dense", "hybrid"):
			raise ValueError(f"RETRIEVAL_MODE không hợp lệ: {self.mode} (chọn 'dense' hoặc 'hybrid')")

		# Giữ toàn bộ chunk trong RAM: dựng BM25, lọc theo nhóm bệnh, ghép lại bài (get_article).
		self._chunks: dict[str, dict] = {chunk["id"]: chunk for chunk in self._store.get_all()}
		self.disease_groups: list[str] = sorted(
			{chunk["metadata"].get("disease_group") for chunk in self._chunks.values()} - {None, ""}
		)
		# Section dài bị chunker cắt thành nhiều chunk: (document_id, section) → id các chunk theo thứ tự.
		self._section_chunks: dict[tuple[str, str], list[str]] = {}
		for chunk_id in sorted(self._chunks, key=_chunk_order):
			metadata = self._chunks[chunk_id]["metadata"]
			key = (metadata.get("document_id", ""), metadata.get("section", ""))
			self._section_chunks.setdefault(key, []).append(chunk_id)
		self._bm25: BM25Index | None = None
		if self.mode == "hybrid":
			# Dựng BM25 từ đúng các chunk đang có trong Chroma (vài trăm ms với vài chục nghìn chunk).
			ids = list(self._chunks)
			self._bm25 = BM25Index(ids, [self._chunks[i]["text"] for i in ids])

	def search(
		self,
		query: str,
		top_k: int | None = None,
		query_embedding: list[float] | None = None,
		disease_group: str | None = None,
		exclude_ids: set[str] | None = None,
		rerank_candidates: int | None = None,
	) -> list[dict]:
		"""`query_embedding` cho phép truyền sẵn vector (script đánh giá embed theo lô).
		`disease_group` chỉ tìm trong một nhóm bệnh — phải là một giá trị của `disease_groups`.

		Dùng cho agent (docs/AGENTIC_RAG.md, bước 2) — rerank là phần đắt nhất (~1,1 s / ứng viên trên CPU):
		- `exclude_ids`: bỏ các chunk này (đã gửi cho model ở lần tìm trước) TRƯỚC khi rerank, để
		  không tốn thời gian chấm chunk sẽ bị bỏ, và để các ứng viên rerank đều là chunk mới.
		- `rerank_candidates`: số ứng viên đưa vào reranker, thay cho RERANK_CANDIDATES."""
		settings = get_settings()
		top_k = top_k or settings.rag_top_k
		disease_group = disease_group or None
		if disease_group and disease_group not in self.disease_groups:
			raise ValueError(
				f"Nhóm bệnh không có trong index: {disease_group!r}. Các nhóm hợp lệ: {self.disease_groups}"
			)
		where = {"disease_group": disease_group} if disease_group else None
		embedding = query_embedding or embed_query(query)
		if self.mode == "dense":
			results = self._store.search(embedding, top_k + len(exclude_ids or ()), where)
			results = [result for result in results if result["id"] not in (exclude_ids or ())][:top_k]
			return [result for result in results if result["score"] >= settings.rag_min_score]
		return self._hybrid_search(
			query, embedding, top_k, disease_group, where,
			exclude_ids or set(), rerank_candidates or settings.rerank_candidates,
		)

	def section_chunk_ids(self, document_id: str, section: str) -> list[str]:
		"""Id mọi chunk của một section, theo thứ tự trong bài (1 phần tử nếu section không bị cắt)."""
		return self._section_chunks.get((document_id, section), [])

	def get_article(self, document_id: str) -> dict | None:
		"""Ghép lại một bài viết từ các chunk của nó; None nếu index không có bài này.

		Trả về metadata của bài kèm `sections: [{"heading", "content"}]` theo đúng thứ tự trong
		bài. Phần overlap giữa các chunk của cùng một section được bỏ, tên bài / heading mà
		chunker gắn vào đầu text chunk cũng được bỏ.
		"""
		chunks = [chunk for chunk in self._chunks.values() if chunk["metadata"].get("document_id") == document_id]
		if not chunks:
			return None
		chunks.sort(key=lambda chunk: _chunk_order(chunk["id"]))
		sections: list[dict[str, str]] = []
		for chunk in chunks:
			metadata = chunk["metadata"]
			heading = metadata.get("section", "")
			body = chunk["text"]
			for prefix in (chunk_prefix(metadata.get("title", ""), heading), f"{heading}\n"):
				if body.startswith(prefix):
					body = body[len(prefix):]
					break
			if sections and sections[-1]["heading"] == heading:
				sections[-1]["content"] = _join_overlapping(sections[-1]["content"], body)
			else:
				sections.append({"heading": heading, "content": body})
		metadata = chunks[0]["metadata"]
		return {
			"document_id": document_id,
			**{key: metadata.get(key, "") for key in ("title", "url", "citation", "source", "category", "disease_group")},
			"sections": sections,
		}

	def _hybrid_search(
		self,
		query: str,
		embedding: list[float],
		top_k: int,
		disease_group: str | None,
		where: dict | None,
		exclude_ids: set[str],
		rerank_candidates: int,
	) -> list[dict]:
		settings = get_settings()
		n_candidates = max(settings.rag_candidates, top_k)

		# Nhánh 1 — dense: top-N theo cosine.
		dense_hits = self._store.search(embedding, n_candidates, where)
		if not dense_hits or dense_hits[0]["score"] < settings.rag_min_score:
			return []  # không có chunk nào đủ gần về ngữ nghĩa → coi như ngoài phạm vi dữ liệu

		# Nhánh 2 — BM25: top-N theo từ khoá.
		bm25_hits = []
		if self._bm25 and disease_group:
			# BM25 không có bộ lọc: chấm toàn bộ rồi giữ N chunk đầu thuộc nhóm bệnh.
			bm25_hits = [
				(chunk_id, score)
				for chunk_id, score in self._bm25.search(query, len(self._bm25))
				if self._chunks[chunk_id]["metadata"].get("disease_group") == disease_group
			][:n_candidates]
		elif self._bm25:
			bm25_hits = self._bm25.search(query, n_candidates)

		# Gộp: RRF trên thứ hạng của hai nhánh.
		rankings = {
			"dense": [hit["id"] for hit in dense_hits],
			"bm25": [chunk_id for chunk_id, _ in bm25_hits],
		}
		fused = reciprocal_rank_fusion(rankings, settings.rrf_k)

		# Gắn điểm thành phần để debug / đánh giá.
		dense_by_id = {hit["id"]: (rank, hit["score"]) for rank, hit in enumerate(dense_hits, start=1)}
		bm25_by_id = {chunk_id: (rank, score) for rank, (chunk_id, score) in enumerate(bm25_hits, start=1)}
		candidates = []
		for chunk_id, rrf_score in sorted(fused.items(), key=lambda item: item[1], reverse=True):
			if chunk_id in exclude_ids:
				continue
			chunk = self._chunks.get(chunk_id) or next(hit for hit in dense_hits if hit["id"] == chunk_id)
			dense_rank, dense_score = dense_by_id.get(chunk_id, (None, None))
			bm25_rank, bm25_score = bm25_by_id.get(chunk_id, (None, None))
			candidates.append({
				"id": chunk_id,
				"text": chunk["text"],
				"metadata": chunk["metadata"],
				"score": rrf_score,
				"scores": {
					"rrf": rrf_score,
					"dense": dense_score,
					"dense_rank": dense_rank,
					"bm25": bm25_score,
					"bm25_rank": bm25_rank,
				},
			})

		# Rerank (tuỳ chọn): cross-encoder chấm lại RERANK_CANDIDATES ứng viên RRF tốt nhất.
		if self.rerank_enabled:
			reranked = get_reranker().rerank(query, candidates[:rerank_candidates], top_k)
			# Điểm cross-encoder tách "liên quan / không liên quan" rõ hơn cosine nhiều, nên dùng nó
			# làm ngưỡng "không có tài liệu phù hợp" thay cho RAG_MIN_SCORE.
			if not reranked or reranked[0]["score"] < settings.rerank_min_score:
				return []
			return reranked
		return candidates[:top_k]
