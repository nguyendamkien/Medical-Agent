# Đi qua code: tính năng gợi ý nơi khám

> Viết ngày 01/11/2026. Mô tả từng bước trong code khi thêm tính năng gợi ý bệnh viện ở Hà Nội: file nào, sửa gì, vì sao. Thiết kế, dữ liệu và kết quả đo: [FACILITIES.md](FACILITIES.md).

## Bức tranh chung

```
"Tôi bị gout, nên khám ở đâu?"
   │
   ▼
frontend  POST /api/chat/stream
   │
   ▼
routers/chat.py ─► AgentService (vòng lặp agent)
                      │
                      ▼
                   Gemini quyết định gọi tool ─► find_healthcare_facility(specialty="co_xuong_khop")
                      │                              │
                      │                              ▼
                      │                         rag/tools.py: ToolSession._find_facility
                      │                              │  gọi services/facility_service.search_facilities
                      │                              │  (đọc app/data/facilities_hanoi.jsonl)
                      │                              ├─► session.facilities  (thẻ đầy đủ)
                      │                              └─► trả cho Gemini: chỉ SỐ LƯỢNG + lời nhắc
                      ▼
                   SSE "facilities" ─► frontend vẽ FacilityCards
                   SSE "delta"      ─► 1–3 câu dẫn của Gemini
                      │
                      ▼
routers/chat.py: lưu messages.facilities vào PostgreSQL
```

**Ý chính:** địa chỉ và số điện thoại **không bao giờ đi qua Gemini**. Model chỉ quyết định *có gọi tool không* và *chuyên khoa nào*. Dữ liệu đã kiểm tra đi thẳng từ file xuống giao diện, nên model không chép sai hay bịa ra được.

Thứ tự các bước dưới đây đi theo chiều dữ liệu, từ file dữ liệu tới giao diện rồi tới eval.

| Bước | File | Loại thay đổi |
|---|---|---|
| 1 | `backend/app/data/facilities_hanoi.jsonl` | mới |
| 2 | `backend/app/services/facility_service.py` | mới |
| 3 | `backend/app/rag/tools.py` | sửa |
| 4 | `backend/app/services/agent_service.py` | sửa |
| 5 | `backend/app/services/gemini_service.py` | sửa (prompt) |
| 6 | `backend/app/models.py`, `alembic/versions/5d2a8c4e1b70_…py` | sửa + migration mới |
| 7 | `backend/app/schemas.py` | sửa |
| 8 | `backend/app/routers/chat.py` | sửa |
| 9 | `backend/app/routers/facilities.py`, `backend/main.py` | mới + sửa |
| 10 | `frontend/src/…` | 1 component mới, 4 file sửa, CSS |
| 11 | `backend/eval/golden_set.jsonl`, `backend/eval/eval_rag.py` | sửa |

---

## Bước 1. Dữ liệu: `backend/app/data/facilities_hanoi.jsonl`

Mỗi dòng một cơ sở (24 dòng). Ví dụ một cơ sở đã xác minh:

```json
{"id": "than-hn", "name": "Bệnh viện Thận Hà Nội", "ownership": "cong", "level": "thanh_pho",
 "address": "70 Nguyễn Chí Thanh, phường Láng, Hà Nội",
 "phones": [{"label": "Cấp cứu", "number": "0929 303 868"}, {"label": "Hành chính", "number": "024 3773 2265"}],
 "emergency": true, "children_only": false, "specialties": ["than_tiet_nieu"],
 "website": "https://bvthanhanoi.vn", "verified": true, "source_url": "https://bvthanhanoi.vn",
 "checked_at": "2026-11-01", "note": null}
```

Một cơ sở **chưa** xác minh: `"verified": false`, `"phones": []`, `note` ghi lý do.

**Vì sao làm như vậy:**
- **Số điện thoại chỉ lấy từ trang chính thức.** Trang tổng hợp ghi lệch nhau, có bệnh viện từng cảnh báo số giả mạo. Không xác minh được thì để trống.
- `emergency` có 3 giá trị: `true` (trang ghi có cấp cứu), `null` (trang không ghi), không dùng `false` để đoán.
- `verified`, `source_url`, `checked_at` để người sau biết dòng nào cần kiểm tra lại.
- **Để file trong git, không để trong DB:** đây là dữ liệu tham chiếu do mình biên soạn, mỗi lần sửa cần review được qua `git diff`. Eval cũng chạy được mà không cần Postgres.

---

## Bước 2. Logic tìm: `backend/app/services/facility_service.py`

Module thuần code, không gọi Gemini, không đụng DB. Tool của agent (bước 3) và API (bước 9) cùng gọi vào đây.

### 2.1. Bảng mã chuyên khoa và ánh xạ bệnh → chuyên khoa

```python
SPECIALTIES: dict[str, str] = {
    "tim_mach": "Tim mạch",
    "tieu_hoa": "Tiêu hóa",
    "co_xuong_khop": "Cơ xương khớp",
    ...
    "noi_tong_quat": "Nội tổng quát (chưa rõ chuyên khoa)",
}
GENERAL = "da_khoa"   # nhãn bệnh viện đa khoa, không phải chuyên khoa để chọn

DISEASE_GROUP_SPECIALTY = {
    "Tăng huyết áp": "tim_mach",
    "Gout (Gút)": "co_xuong_khop",
    ...
}
```

`SPECIALTIES` vừa là danh sách hợp lệ khi đọc file, vừa là `enum` khai báo cho Gemini (bước 3). Nhờ vậy model không tự nghĩ ra mã chuyên khoa lạ.

### 2.2. Đọc file một lần

```python
@lru_cache
def load_facilities() -> list[dict]:
    ...
    unknown = set(item.get("specialties", [])) - set(SPECIALTIES) - {GENERAL}
    if unknown or item["id"] in seen:
        raise ValueError(...)
```

`lru_cache` để chỉ đọc file một lần trong vòng đời backend. Sửa file thì phải khởi động lại. Gõ sai mã chuyên khoa hoặc trùng `id` thì báo lỗi ngay, không để lỗi im lặng.

### 2.3. Tìm và sắp xếp

```python
def search_facilities(specialty, emergency=False, for_child=False, limit=5):
    for order, facility in enumerate(load_facilities()):
        if facility.get("children_only") and not for_child:
            continue                                        # người lớn: bỏ bệnh viện chỉ khám trẻ em
        if emergency and facility.get("emergency") is not True:
            continue                                        # cấp cứu: chỉ nơi chắc chắn có cấp cứu
        if specialty in specialties or (for_child and "nhi" in specialties):
            matched = "chuyen_khoa"
        elif GENERAL in specialties or (emergency and not specialties):
            matched = "da_khoa"                             # đa khoa làm dự phòng; BV ngoại khoa vẫn nhận cấp cứu
        else:
            continue
        rank = (matched != "chuyen_khoa", for_child and "nhi" not in specialties,
                not facility.get("verified"), order)
```

Thứ tự sắp xếp nằm hết trong tuple `rank`:
1. Đúng chuyên khoa trước, đa khoa sau.
2. Trẻ em: nơi có chuyên khoa nhi trước. Mục này được thêm sau khi test offline bắt lỗi: "trẻ bị hen" lại ra Bạch Mai đứng đầu thay vì Nhi Trung ương.
3. Đã xác minh trước.
4. Còn lại giữ thứ tự trong file (công trước, tư sau).

**Không có tiêu chí "uy tín" hay đánh giá sao.** Đây là chủ ý, để danh sách trung lập.

### 2.4. Link chỉ đường và thẻ gửi xuống frontend

```python
def maps_url(facility):
    query = ", ".join(part for part in (facility["name"], facility.get("address") or "Hà Nội") if part)
    return f"https://www.google.com/maps/search/?api=1&query={quote(query)}"

def facility_card(facility):
    keys = ("id", "name", "ownership", "address", "phones", "emergency", "children_only", "website",
            "verified", "checked_at", "note", "matched", "specialty_labels", "maps_url")
    return {key: facility.get(key) for key in keys}
```

Link Google Maps tìm theo tên + địa chỉ, không cần API key. Google Maps tự hiện giờ mở cửa và số điện thoại mới nhất, bù cho phần dữ liệu của mình có thể cũ. `facility_card` lọc bớt trường nội bộ (`source_url`, `level`…) trước khi gửi đi.

---

## Bước 3. Tool cho agent: `backend/app/rag/tools.py`

### 3.1. Khai báo tool cho Gemini

Thêm phần tử thứ 3 vào `tool_declarations()`:

```python
{
    "name": FACILITY_TOOL,   # "find_healthcare_facility"
    "description": "Gợi ý bệnh viện / phòng khám ở HÀ NỘI theo chuyên khoa. Gọi khi người dùng hỏi nên "
                   "khám ở đâu ... Chỉ có dữ liệu Hà Nội: người dùng nói đang ở tỉnh khác thì KHÔNG gọi.",
    "parameters": {
        "type": "object",
        "properties": {
            "specialty": {"type": "string", "format": "enum", "enum": list(SPECIALTIES),
                          "description": "tăng huyết áp, suy tim → tim_mach; dạ dày → tieu_hoa; ..."},
            "emergency": {"type": "boolean", ...},
            "for_child": {"type": "boolean", ...},
        },
        "required": ["specialty"],
    },
}
```

Phần `description` của `specialty` ghi sẵn bệnh → chuyên khoa, để model chọn đúng mà không phải đoán.

### 3.2. Trạng thái trong `ToolSession`

```python
self.facilities: list[dict] = []   # thẻ gửi xuống frontend

@property
def has_content(self) -> bool:
    return bool(self.results or self.facilities)
```

`has_content` được dùng ở bước 4. Trước đây agent chỉ coi "có chunk tài liệu" là có nội dung để trả lời.

### 3.3. Chạy tool

Trong `run()` thêm một nhánh:

```python
elif name == FACILITY_TOOL:
    output = self._find_facility(str(args.get("specialty") or ""),
                                 bool(args.get("emergency")), bool(args.get("for_child")))
    entry["n_facilities"] = len(self.facilities)      # ghi vào vết chạy cho eval
```

Hàm chính:

```python
def _find_facility(self, specialty, emergency, for_child) -> str:
    if specialty not in SPECIALTIES:
        raise ToolError("bad_specialty", ...)          # trả thành text để model tự sửa
    found = search_facilities(specialty, emergency=emergency, for_child=for_child)
    if not found:
        return NO_FACILITIES
    have = {item["id"] for item in self.facilities}
    self.facilities += [facility_card(item) for item in found if item["id"] not in have]   # gọi nhiều lần thì gộp
    exact = sum(item["matched"] == "chuyen_khoa" for item in found)
    ...
    return f"{summary}\n\n{FACILITY_REMINDER}"
```

**Thứ gửi cho model chỉ là câu tóm tắt số lượng**, ví dụ:

```
Đã tìm được 5 cơ sở ở Hà Nội (3 cơ sở có chuyên khoa Cơ xương khớp, 2 bệnh viện đa khoa).

(Danh sách hiện thành thẻ ngay dưới câu trả lời ... KHÔNG liệt kê tên cơ sở, KHÔNG viết địa chỉ hay
số điện thoại; KHÔNG nói cơ sở nào tốt nhất; nhắc gọi trước để xác nhận lịch khám. Có dấu hiệu cấp
cứu thì câu đầu tiên vẫn là gọi 115.)
```

Bản đầu tiên gửi cả **tên** các cơ sở. Eval cho thấy model chép tên thành danh sách, trùng với thẻ trên giao diện, và giám khảo chấm danh sách đó là "claim không có nguồn". Vì vậy giờ chỉ gửi số lượng (bước 11).

`FACILITY_REMINDER` đặt ngay cuối kết quả tool, theo cùng cách `DOSE_REMINDER` đã làm với liều thuốc: quy tắc đặt sát chỗ model đọc dữ liệu thì model tuân theo tốt hơn so với chỉ nằm trong system instruction.

---

## Bước 4. Vòng lặp agent: `backend/app/services/agent_service.py`

Năm chỗ sửa:

**4.1. Đếm lượt gọi tool đúng.** Trước đây mọi tool khác `get_article` đều bị coi là "lần tìm" và tính vào giới hạn 3 lần:

```python
# trước
is_search = call.name != "get_article"
# sau
is_search = call.name == "search_medical_docs"
```

Không sửa chỗ này thì tool mới sẽ ăn mất lượt tìm tài liệu.

**4.2. Trạng thái cho frontend:**

```python
elif call.name == FACILITY_TOOL:
    yield "status", {"stage": "finding_facility"}
```

**4.3. Phát danh sách thẻ** sau mỗi vòng gọi tool:

```python
yield "sources", {...}
if session.facilities:
    yield "facilities", {"facilities": session.facilities}
```

Mỗi lần phát là danh sách đầy đủ tới lúc đó, giống cách `sources` đang làm, nên frontend chỉ cần ghi đè.

**4.4. Chốt chặn "không có tài liệu":**

```python
# trước
if not answered and not session.results and settings.agent_no_context_guard:
# sau
if not answered and not session.has_content and settings.agent_no_context_guard:
```

Chốt chặn bỏ câu trả lời của model khi request chưa có tài liệu nào. Nếu không sửa, câu dẫn "Dưới đây là một số cơ sở…" sau khi gợi ý nơi khám sẽ bị chặn nhầm và thay bằng "chưa có thông tin".

**4.5. Đường dự phòng khi model không viết gì:**

```python
if session.facilities and not session.results:
    yield "delta", {"text": FACILITY_FALLBACK_TEXT}   # câu dẫn cố định
    return
```

Không có chỗ này thì trường hợp hiếm (model không trả text) sẽ rơi vào prompt "không có tài liệu", ra câu "chưa có thông tin" nằm cạnh các thẻ bệnh viện.

Thêm nữa, `ask_with_trace()` trả thêm `"facilities": session.facilities`, để `/api/chat` và eval lấy được.

---

## Bước 5. Quy tắc cho model: `backend/app/services/gemini_service.py`

Thêm một đoạn vào `AGENT_INSTRUCTION`:

```
Gợi ý nơi khám (find_healthcare_facility):
- CHỈ gọi khi người dùng hỏi nên khám ở đâu / bệnh viện nào / đi khám chỗ nào.
- Chọn specialty theo bệnh đang nói (từ câu hỏi hoặc lịch sử). Hỏi nơi khám mà không nêu bệnh:
  dùng bệnh nền trong THÔNG TIN NGƯỜI HỎI nếu có; không có thì noi_tong_quat. Vẫn PHẢI gọi tool.
  Câu hỏi vừa hỏi về bệnh vừa hỏi nơi khám: gọi cả search_medical_docs và find_healthcare_facility.
- Dữ liệu chỉ có ở Hà Nội. Người dùng ở tỉnh khác: KHÔNG gọi ...
- Cấp cứu: câu đầu tiên luôn là gọi 115; chỉ khi người dùng hỏi nơi cấp cứu thì thêm emergency=true.
- KHÔNG BAO GIỜ tự nêu tên, địa chỉ hay số điện thoại cơ sở y tế.
```

Dòng "hỏi nơi khám mà không nêu bệnh" được thêm sau eval (bước 11). Ở lượt đo đầu, người dùng có hồ sơ suy thận hỏi "nên khám định kỳ ở đâu" thì agent không gọi tool.

---

## Bước 6. Lưu vào DB: `backend/app/models.py` + migration

```python
class Message(Base):
    ...
    sources: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    facilities: Mapped[list | None] = mapped_column(JSONB, nullable=True)   # mới
```

Migration [`5d2a8c4e1b70_them_cot_facilities_vao_messages.py`](../backend/alembic/versions/5d2a8c4e1b70_them_cot_facilities_vao_messages.py):

```python
def upgrade() -> None:
    op.add_column('messages', sa.Column('facilities', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
```

**Vì sao lưu nguyên thẻ vào tin nhắn,** thay vì chỉ lưu id bệnh viện rồi tra lại khi mở hội thoại: mở lại hội thoại cũ phải thấy **đúng danh sách đã gợi ý lúc đó**, kể cả khi file dữ liệu đã được sửa sau đó.

Cột cho phép `null`, nên tin nhắn cũ không bị ảnh hưởng. **Kéo code về thì phải chạy `python -m alembic upgrade head`**, không thì mọi request đọc tin nhắn sẽ báo `column messages.facilities does not exist`.

---

## Bước 7. Schema API: `backend/app/schemas.py`

```python
class FacilityPhone(BaseModel):
    label: str
    number: str

class Facility(BaseModel):
    id: str
    name: str
    ownership: Literal["cong", "tu"]
    address: str | None = None
    phones: list[FacilityPhone] = Field(default_factory=list)
    emergency: bool | None = None
    ...
    matched: Literal["chuyen_khoa", "da_khoa"]
    maps_url: str

class ChatResponse(BaseModel):
    ...
    facilities: list[Facility] = Field(default_factory=list)   # mới

class MessageOut(BaseModel):
    ...
    facilities: list[Facility] | None = None                     # mới
```

Có schema riêng thì `/docs` (Swagger) hiện đúng cấu trúc thẻ, và FastAPI kiểm tra dữ liệu trả về.

---

## Bước 8. Router chat: `backend/app/routers/chat.py`

**`_save_turn()`** nhận thêm `facilities` và ghi vào tin nhắn của bot:

```python
db.add(Message(conversation_id=..., role="model", content=reply, sources=sources,
               facilities=facilities or None))
```

**`/api/chat/stream`** gom sự kiện `facilities` như đang gom `sources`, rồi lưu khi stream xong:

```python
elif event == "facilities":
    facilities = data["facilities"]   # sự kiện sau là danh sách đầy đủ
...
_save_turn(conversation_id, request.message, "".join(reply_parts), sources, facilities)
```

**`/api/chat`** (không stream): `AgentService.ask()` chỉ trả `(reply, sources)`, nên với agent thì gọi `ask_with_trace()` để lấy thêm `facilities`:

```python
if isinstance(service, AgentService):
    result = service.ask_with_trace(request.message, history, profile)
    reply, sources, facilities = result["reply"], result["sources"], result["facilities"]
else:
    reply, sources = service.ask(request.message, history, profile)
```

Pipeline cũ (`AGENT_ENABLED=false`) không có tool nên `facilities` luôn rỗng.

---

## Bước 9. API riêng: `backend/app/routers/facilities.py` + `main.py`

```python
@router.get("", response_model=list[Facility])
def list_facilities(specialty: str = Query(...), emergency: bool = False, for_child: bool = False,
                    limit: int = Query(5, ge=1, le=30), _: User = Depends(get_current_user)):
    ...
    return [facility_card(item) for item in search_facilities(...)]

@router.get("/specialties")
def list_specialties(...):
    return {"specialties": SPECIALTIES, "disease_groups": DISEASE_GROUP_SPECIALTY}
```

`main.py` thêm `facilities` vào dòng import router và gọi `app.include_router(facilities.router)`.

Frontend hiện chưa dùng API này, vì thẻ đến qua chat. Nó dành cho việc mở rộng sau, ví dụ nút "Tìm nơi khám" dưới mỗi câu trả lời, và để thử nhanh trên `/docs`. API này dùng chung `search_facilities` nên kết quả luôn khớp với tool.

---

## Bước 10. Frontend

**10.1. `src/api/chatApi.js`**: nhận sự kiện mới

```js
else if (event === "facilities") onFacilities?.(data.facilities);
```

**10.2. `src/App.jsx`**: ghi danh sách vào bong bóng trả lời đang stream, rồi truyền xuống `MessageBubble`

```js
onFacilities: (facilities) => updateReply((m) => ({ ...m, facilities })),
...
<MessageBubble ... facilities={message.facilities} />
```

Khi lỗi giữa chừng, bong bóng lỗi xoá luôn `facilities: []`, để không còn thẻ treo lơ lửng cạnh câu báo lỗi.

**10.3. `src/utils/sessions.js`**: tin nhắn tải lại từ API mang theo thẻ

```js
export function fromApiMessage({ role, content, sources, facilities }) {
  return { role, content, sources: sources ?? [], facilities: facilities ?? [] };
}
```

**10.4. `src/components/MessageBubble.jsx`**: thêm trạng thái chờ và chỗ đặt thẻ

```js
finding_facility: "Đang tìm nơi khám phù hợp…",
...
{!isUser && !isWaiting && <FacilityCards facilities={facilities} />}
```

Thẻ nằm sau câu trả lời, trước phần "Nguồn tham khảo", và chỉ hiện khi đã có chữ, giống cách nguồn đang hiện.

**10.5. `src/components/FacilityCards.jsx`** (mới). Mỗi thẻ gồm:
- tên, nhãn Công lập / Tư nhân, Cấp cứu, Chỉ khám trẻ em
- dòng chuyên khoa, hoặc "Bệnh viện đa khoa"
- địa chỉ
- số điện thoại dạng link `tel:`, bấm là gọi trên điện thoại
- nếu `verified: false`: hộp cảnh báo "Chưa xác minh trên trang chính thức…"
- nút **Chỉ đường** (`maps_url`) và **Website**

Cuối danh sách là dòng "Danh sách tham khảo, không xếp hạng (kiểm tra ngày …). Gọi trước để xác nhận lịch khám. Cấp cứu: gọi 115."

**10.6. `src/App.css`**: thêm nhóm class `.facilities`, `.facility__…`, theo màu đang dùng (xanh `#0f9d8c`, đỏ cấp cứu, vàng cảnh báo).

---

## Bước 11. Eval: `backend/eval/`

**11.1. Golden set** thêm 8 câu loại `facility`. Trường mới `expect_facility`:

```json
{"id": "facility-03", "question": "Con tôi 5 tuổi hay bị lên cơn hen, nên cho cháu khám ở bệnh viện nào?",
 "expect_facility": {"specialty": "ho_hap", "for_child": true},
 "must_not_mention": ["(?:\\d[ .]?){7,10}\\d"]}

{"id": "facility-04", "question": "Tôi ở Đà Nẵng, bị viêm loét dạ dày thì nên khám ở đâu?",
 "expect_facility": null, "must_mention": ["Hà Nội"]}
```

- `expect_facility: null` nghĩa là **không được** gọi tool.
- Regex `(?:\d[ .]?){7,10}\d` bắt chuỗi giống số điện thoại (8–11 chữ số, có thể cách bởi dấu cách hoặc dấu chấm), mà không bắt `115`, năm, hay `140/90`. Bản regex đầu bỏ sót dạng `024 3823 3075`; test trước khi chạy đã bắt được lỗi này.

**11.2. `eval_rag.py`**: thêm `facility_check()`, gắn vào `agent_metrics()`

```python
calls = [call for call in trace if call["tool"] == "find_healthcare_facility"]
if expected is None:
    ok = not calls
else:
    ok = any(all(... khớp từng khoá trong expected ...) for call in calls)
return {"facility_ok": ok, "facility_calls": [call["args"] for call in calls]}
```

Cuối lần chạy in "Gợi ý nơi khám: x/8 câu gọi find_healthcare_facility đúng" và lời gọi của các câu trượt.

**11.3. Hai lỗi eval tìm ra, đã sửa:**

| Lần đo | Kết quả | Lỗi | Sửa ở |
|---|---|---|---|
| 1 | 7/8 | `facility-08` (hồ sơ suy thận, "nên khám định kỳ ở đâu") không gọi tool, bị chốt chặn trả "chưa có thông tin" | Bước 5: thêm quy tắc lấy bệnh nền từ hồ sơ |
| 1 | — | 4/8 câu chép tên bệnh viện thành danh sách trùng với thẻ | Bước 3.3: tool chỉ trả số lượng, không trả tên |
| 2 | **8/8** | — | — |

Chạy lại: `cd backend`, rồi `python -m eval.eval_rag --agent --types facility --workers 1`.

---

## Kiểm tra nhanh sau khi sửa

1. `python -m alembic upgrade head` trong `backend/`.
2. Khởi động backend và frontend, đăng nhập.
3. Hỏi "Tôi bị gout, nên khám ở bệnh viện nào ở Hà Nội?". Thấy trạng thái "Đang tìm nơi khám phù hợp…", rồi thẻ bệnh viện cơ xương khớp; câu dẫn không chứa số điện thoại.
4. Tải lại trang, mở lại hội thoại: thẻ vẫn còn.
5. Hỏi "Bố tôi đau ngực dữ dội…": câu đầu khuyên gọi 115; thẻ chỉ gồm nơi có nhãn Cấp cứu.
6. Hỏi "Tôi ở Đà Nẵng…": không có thẻ, câu trả lời nói chỉ có dữ liệu Hà Nội.

## Muốn mở rộng thì sửa ở đâu

| Việc | Sửa |
|---|---|
| Thêm / sửa một bệnh viện | Một dòng trong `facilities_hanoi.jsonl`; đổi `checked_at`; khởi động lại backend |
| Thêm chuyên khoa | `SPECIALTIES` trong `facility_service.py` (tự vào `enum` của tool); thêm gợi ý vào `description` của tham số `specialty` trong `tools.py`; gán chuyên khoa cho các cơ sở trong file |
| Thêm nhóm bệnh mới vào dữ liệu bài viết | `DISEASE_GROUP_SPECIALTY` |
| Mở rộng sang tỉnh khác | Thêm trường `province` vào dữ liệu và tham số `province` cho tool; bỏ câu "chỉ có dữ liệu Hà Nội" trong `AGENT_INSTRUCTION`. Nhiều cơ sở thì nên chuyển sang DB, xem [FACILITIES.md](FACILITIES.md) |
| Nút "Tìm nơi khám" dưới câu trả lời | Frontend gọi `GET /api/facilities?specialty=…`; cần backend trả thêm `disease_group` trong `sources` để suy ra chuyên khoa |
