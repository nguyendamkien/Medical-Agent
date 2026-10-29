from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Cấu hình ứng dụng, đọc từ biến môi trường / file .env."""

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    vector_db_path: str = "app/storage/chroma"
    vector_collection: str = "medical_documents"
    rag_top_k: int = 5
    rag_min_score: float = 0.25
    # Retrieval: "hybrid" (BM25 + dense, gộp bằng RRF) hoặc "dense" (chỉ vector)
    retrieval_mode: str = "hybrid"
    rag_candidates: int = 20  # số ứng viên mỗi nhánh lấy về trước khi gộp / rerank
    rrf_k: int = 60
    # Rerank bằng cross-encoder bge-reranker-v2-m3 (xem app/rag/reranker.py)
    # Bật mặc định: các số đo trong docs/RAG_EVAL.md đều chạy với rerank; tắt thì câu ngoài phạm vi
    # không bị chặn ở retrieval (RAG_MIN_SCORE theo cosine gần như không lọc được).
    rerank_enabled: bool = True
    rerank_onnx_repo: str = "onnx-community/bge-reranker-v2-m3-ONNX"
    rerank_onnx_file: str = "onnx/model_int8.onnx"  # "onnx/model.onnx" = bản fp32 (~2.3 GB)
    rerank_candidates: int = 10  # số ứng viên RRF đưa vào cross-encoder (ít hơn → nhanh hơn)
    rerank_min_score: float = 0.4  # điểm rerank tốt nhất < ngưỡng → coi như không có tài liệu phù hợp
    # Hội thoại: viết lại câu hỏi nối tiếp thành câu độc lập trước khi retrieve
    # ("Vậy điều trị thế nào?" → "Điều trị bệnh gout thế nào?"), và chỉ giữ N lượt hỏi–đáp gần nhất.
    query_rewrite_enabled: bool = True
    history_max_turns: int = 10
    # Agentic RAG (docs/AGENTIC_RAG.md): Gemini tự gọi tool tìm tài liệu thay cho pipeline cố định.
    # Bật mặc định từ 21/10/2026, sau khi đạt mọi điều kiện ở bước đánh giá (bước 4).
    # false = pipeline cố định (viết lại câu hỏi → tìm 1 lần → trả lời), ít lần gọi Gemini hơn.
    agent_enabled: bool = True
    agent_max_tool_calls: int = 3  # số lần search_medical_docs tối đa (mỗi lần 12–30 s vì rerank)
    # Từ lần tìm thứ hai trong một câu hỏi chỉ rerank N ứng viên (lần đầu: RERANK_CANDIDATES).
    agent_followup_rerank_candidates: int = 5
    agent_max_total_calls: int = 8  # mọi lời gọi tool kể cả get_article; đủ thì vòng kế tiếp cấm gọi tool
    # Request kết thúc mà không lấy được chunk nào → bỏ câu trả lời của agent, sinh lại bằng
    # build_no_context_prompt (như pipeline cũ) để model không trả lời y khoa từ kiến thức riêng.
    agent_no_context_guard: bool = True
    # Guardrail liều thuốc (app/rag/guardrail.py): quét câu trả lời của agent, che hàm lượng / số lần
    # dùng thuốc trước khi gửi tới frontend.
    dose_guardrail: bool = True
    data_path: str = "../data-collection/data/benh_pho_bien_10.jsonl"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # PostgreSQL lưu user + lịch sử chat (docker-compose.yml ở thư mục gốc). Xem app/db.py.
    database_url: str = "postgresql+pg8000://medical:medical@localhost:5432/medical_agent"

    # Đăng nhập (app/security.py). JWT_SECRET bắt buộc, chỉ để trong .env.
    jwt_secret: str = ""
    jwt_expire_days: int = 7
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
