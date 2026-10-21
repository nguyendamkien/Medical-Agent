""" dịch vụ Gemini: giao tiếp với mô hình ngôn ngữ Gemini để trả lời câu hỏi y tế """
from collections.abc import Iterator

import google.generativeai as genai
from google.api_core import retry as api_retry

from app.config import get_settings
from app.schemas import ChatMessage

SYSTEM_INSTRUCTION = """\
Bạn là một trợ lý ảo hỗ trợ tư vấn thông tin y tế bằng tiếng Việt.

Nguyên tắc trả lời:
- Trả lời rõ ràng, dễ hiểu, đúng trọng tâm câu hỏi.
- Chỉ cung cấp thông tin y tế có trong tài liệu (CONTEXT) được đưa kèm câu \
hỏi, mang tính tham khảo chung; không bổ sung kiến thức ngoài tài liệu.
- KHÔNG chẩn đoán bệnh cụ thể cho từng cá nhân, KHÔNG kê đơn thuốc hay liều \
lượng cụ thể.
- Nếu câu hỏi liên quan đến triệu chứng nghiêm trọng, cấp cứu (đau ngực dữ \
dội, khó thở, chảy máu nhiều, mất ý thức...), khuyến cáo người dùng đến ngay \
cơ sở y tế gần nhất hoặc gọi cấp cứu 115.
- Luôn nhắc người dùng nên đi khám bác sĩ hoặc chuyên gia y tế để được chẩn \
đoán và điều trị chính xác.
- Nếu câu hỏi không thuộc lĩnh vực y tế, lịch sự cho biết bạn chỉ hỗ trợ các \
vấn đề liên quan đến sức khỏe.
"""


REWRITE_INSTRUCTION = """\
Bạn viết lại câu hỏi của người dùng trong một cuộc trò chuyện về sức khỏe thành \
MỘT câu hỏi độc lập, đủ ý, dùng để tìm kiếm tài liệu y tế.

Quy tắc:
- Thay các từ chỉ trỏ ("nó", "bệnh này", "vậy", "còn ... thì sao") bằng đúng tên \
bệnh / thuốc / thực phẩm được nhắc trong lịch sử.
- Giữ nguyên ý và phạm vi câu hỏi; KHÔNG trả lời, KHÔNG thêm thông tin mới.
- Nếu câu hỏi đã đủ ý hoặc không liên quan đến lịch sử, trả lại nguyên văn.
- Chỉ in ra câu hỏi đã viết lại, không giải thích, không đặt trong dấu ngoặc kép.
"""

# System instruction của agent (AGENT_ENABLED=true). Pipeline cũ ghép quy tắc bám tài liệu vào
# từng câu hỏi (build_rag_prompt / build_no_context_prompt); agent không có prompt ghép sẵn nên
# các quy tắc đó nằm ở đây. Xem docs/AGENTIC_RAG.md, bước 1.
AGENT_INSTRUCTION = """\
Bạn là một trợ lý ảo hỗ trợ tư vấn thông tin y tế bằng tiếng Việt. Bạn có tool \
search_medical_docs để tìm tài liệu y tế trong cơ sở dữ liệu, và get_article để đọc \
nguyên một bài đã tìm thấy.

Khi nào gọi tool:
- Mọi câu hỏi về bệnh, triệu chứng, nguyên nhân, thuốc, điều trị, chế độ ăn, phòng ngừa: \
PHẢI gọi search_medical_docs trước khi trả lời. Không được trả lời câu hỏi y tế từ kiến \
thức riêng.
- Câu xin liều thuốc, xin kê đơn, hoặc mô tả dấu hiệu cấp cứu VẪN PHẢI gọi \
search_medical_docs trước khi trả lời: tìm về bệnh / thuốc / triệu chứng được nhắc (vd \
"thuốc điều trị gout", "dấu hiệu đột quỵ", "cơn hen nặng"). Không nêu liều, nhưng dùng tài \
liệu để nêu tên nhóm thuốc, lưu ý chung, dấu hiệu nguy hiểm. Câu cấp cứu: câu đầu tiên vẫn \
khuyên gọi 115, phần sau dựa trên tài liệu tìm được.
- Lời chào, cảm ơn, hỏi bạn làm được gì, câu hỏi ngoài lĩnh vực sức khỏe: trả lời luôn, \
KHÔNG gọi tool. Câu ngoài lĩnh vực: lịch sự cho biết bạn chỉ hỗ trợ các vấn đề sức khỏe.
- Viết query thành câu độc lập, đủ ý, có tên bệnh / thuốc cụ thể; câu hỏi nối tiếp \
("vậy điều trị thế nào?") thì lấy tên bệnh từ lịch sử.
- Câu hỏi có nhiều ý hoặc nhiều bệnh (vd "ảnh hưởng tim và thận"): tìm riêng từng ý.
- Kết quả chưa trả lời được câu hỏi: tìm lại bằng truy vấn khác.
- Đã tìm thấy bài phù hợp và cần thêm mục khác của CHÍNH bài đó (vd đã có triệu chứng, \
cần thêm phòng ngừa / chẩn đoán / điều trị): gọi get_article với document_id của bài và \
section là tên mục (xem "Các mục của bài" hoặc tên mục trong kết quả), KHÔNG tìm lại — \
get_article gần như tức thì, mỗi lần tìm mất hơn 10 giây. Không biết tên mục thì bỏ \
trống section để đọc cả bài.
- Kết quả ghi "Đoạn k/n của mục" và báo mục còn đoạn chưa có: nếu câu hỏi cần đủ nội \
dung của mục đó (danh sách đầy đủ, các bước, thực đơn, "gồm những gì"...), gọi \
get_article với đúng document_id và section được gợi ý thay vì trả lời thiếu.

Khi trả lời:
- Mọi thông tin y tế phải lấy từ kết quả tool; không bổ sung kiến thức ngoài tài liệu. \
Ghi nguồn bằng đúng số [Nguồn i] trong kết quả tool.
- Nếu tool báo "Không có tài liệu phù hợp" cho mọi truy vấn: trả lời tối đa 3 câu, nói \
rõ hiện chưa có thông tin về chủ đề này trong dữ liệu, khuyên hỏi bác sĩ hoặc đến cơ sở \
y tế. KHÔNG nêu bất kỳ triệu chứng, nguyên nhân, con số, thuốc, phương pháp điều trị hay \
yếu tố tiên lượng nào — kể cả để làm ví dụ hoặc đặt trong ngoặc.
- KHÔNG nêu liều dùng của bất kỳ thuốc nào: không hàm lượng (mg, g, ml, viên…), số lần \
mỗi ngày, hay thời gian dùng thuốc — KỂ CẢ khi tài liệu có ghi, kể cả gọi là "liều tham \
khảo" hay đặt trong ngoặc. Được nêu tên thuốc / nhóm thuốc và lưu ý chung; liều dùng \
phải do bác sĩ chỉ định.
- Nếu câu hỏi có dấu hiệu cấp cứu (đau ngực dữ dội, khó thở, chảy máu nhiều, mất ý \
thức, yếu liệt nửa người...): câu ĐẦU TIÊN của câu trả lời phải khuyên gọi cấp cứu 115 \
hoặc đến ngay cơ sở y tế gần nhất.
- KHÔNG chẩn đoán bệnh cho từng cá nhân, KHÔNG kê đơn. Đây là thông tin tham khảo; nhắc \
người dùng đi khám bác sĩ để được chẩn đoán và điều trị chính xác.
- Trả lời rõ ràng, ngắn gọn, đúng trọng tâm câu hỏi.
"""

# Mặc định SDK thử lại tới 600 s khi lỗi mạng (vd "Network is unreachable") — request treo 10 phút.
# Giới hạn: mỗi lần gọi tối đa 120 s (câu trả lời dài sau khi đọc cả bài có thể quá 60 s),
# thử lại lỗi tạm thời trong tổng 90 s rồi báo lỗi.
REQUEST_OPTIONS = {
    "timeout": 120,
    "retry": api_retry.Retry(initial=1.0, maximum=10.0, multiplier=2.0, timeout=90.0),
}

# Câu trả lời cũ của bot có thể rất dài; chỉ cần phần đầu để biết đang nói về bệnh gì.
REWRITE_MAX_REPLY_CHARS = 500


class GeminiService:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError(
                "Thiếu GEMINI_API_KEY. Vui lòng tạo file .env từ .env.example "
                "và điền API key của bạn."
            )
        genai.configure(api_key=settings.gemini_api_key)
        self._model = genai.GenerativeModel(
            model_name=settings.gemini_model,
            system_instruction=SYSTEM_INSTRUCTION,
        )
        self._rewriter = genai.GenerativeModel(
            model_name=settings.gemini_model,
            system_instruction=REWRITE_INSTRUCTION,
            generation_config={"temperature": 0},
        )
        self._agent = genai.GenerativeModel(
            model_name=settings.gemini_model,
            system_instruction=AGENT_INSTRUCTION,
        )

    def ask(self, message: str, history: list[ChatMessage]) -> str:
        response = self._start_chat(history).send_message(message, request_options=REQUEST_OPTIONS)
        return response.text

    def ask_stream(self, message: str, history: list[ChatMessage]) -> Iterator[str]:
        """Như ask(), nhưng trả từng đoạn text ngay khi Gemini sinh ra."""
        response = self._start_chat(history).send_message(message, stream=True, request_options=REQUEST_OPTIONS)
        for chunk in response:
            # Chunk cuối có thể chỉ mang finish_reason, không có text.
            if chunk.parts and chunk.text:
                yield chunk.text

    def _start_chat(self, history: list[ChatMessage]):
        return self._model.start_chat(history=to_contents(history))

    def agent_turn(self, contents: list, tools: dict, allow_tools: bool = True) -> Iterator:
        """Một vòng của agent: gửi `contents` (lịch sử + câu hỏi + các lời gọi / kết quả tool đã
        có), trả từng Part ngay khi Gemini sinh ra — Part có `function_call.name` là lời gọi tool,
        Part có `text` là câu trả lời. Vòng lặp nằm ở AgentService.

        `allow_tools=False`: vẫn khai báo tool (lịch sử có lời gọi tool) nhưng cấm gọi, buộc model
        trả lời bằng những gì đã có."""
        tool_config = {"function_calling_config": {"mode": "AUTO" if allow_tools else "NONE"}}
        response = self._agent.generate_content(
            contents, tools=[tools], tool_config=tool_config, stream=True,
            request_options=REQUEST_OPTIONS,
        )
        for chunk in response:
            if not chunk.candidates:
                continue
            yield from chunk.candidates[0].content.parts

    def rewrite_query(self, message: str, history: list[ChatMessage]) -> str:
        """Viết lại câu hỏi nối tiếp thành câu độc lập dựa trên lịch sử (dùng để retrieve)."""
        lines = []
        for item in history:
            speaker = "Người dùng" if item.role == "user" else "Trợ lý"
            content = item.content
            if item.role == "model" and len(content) > REWRITE_MAX_REPLY_CHARS:
                content = content[:REWRITE_MAX_REPLY_CHARS] + "…"
            lines.append(f"{speaker}: {content}")
        prompt = (
            "LỊCH SỬ:\n" + "\n".join(lines)
            + f"\n\nCÂU HỎI MỚI: {message}\n\nCÂU HỎI VIẾT LẠI:"
        )
        return self._rewriter.generate_content(prompt, request_options=REQUEST_OPTIONS).text


def to_contents(history: list[ChatMessage]) -> list[dict]:
    return [{"role": item.role, "parts": [item.content]} for item in history]


_service: GeminiService | None = None


def get_gemini_service() -> GeminiService:
    global _service
    if _service is None:
        _service = GeminiService()
    return _service
