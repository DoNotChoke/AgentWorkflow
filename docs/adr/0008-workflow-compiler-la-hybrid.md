# ADR 0008 — Workflow Compiler là hybrid: LLM quyết định tổng quát hoá, code tất định dựng IR

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** TuanNghia (solo developer)
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §5.1, §5.2, §9, §10, §10.1, §10.2,
  §11, §39, §40, §46, §47; ADR-0003, ADR-0004, ADR-0006;
  [`docs/plans/wp-b-workflow-ir-va-generic-executor.md`](../plans/wp-b-workflow-ir-va-generic-executor.md)

## Bối cảnh

§46 gọi phép biến đổi trace → workflow là *"the central architectural and product
concept"* của hệ thống. Câu hỏi chưa ai trả lời: **bao nhiêu phần của phép biến
đổi đó là suy luận, và bao nhiêu là đọc dữ liệu đã có?**

Spec nghiêng hẳn về một phía, và nghiêng ở hai chỗ độc lập nhau:

**Vị trí.** §5.2 xếp Workflow Compiler vào **AI Plane**, cạnh Agent Runtime và
Model Gateway. §5.1 liệt kê Control Plane gồm *"Workflow Management"* — CRUD,
publish, version — và không có compiler. Đây là một phát biểu kiến trúc, không
phải cách sắp xếp mục lục.

**Động từ.** Danh sách trách nhiệm của compiler ở §5.2 dùng `infer` năm lần liên
tiếp: *infer Workflow inputs, infer constants, infer dependencies, infer branch
logic, infer data mappings*. `infer` không phải `parse`.

Nhưng nghiêng về phía AI không có nghĩa là giao hết cho AI. Nhìn vào thứ IR cần,
đối chiếu với thứ trace đã có, ranh giới hiện ra khá sắc:

| IR cần | Trace đã có chưa |
|---|---|
| Gọi tool nào, thứ tự nào | **Có nguyên vẹn** |
| `tool_id`, `tool_version`, `connection_id` | **Chuỗi chính xác** |
| Output trường X của bước N xuất hiện y hệt trong input bước N+1 | **Một phép so khớp** |
| `"C0912SUPPORT"` là hằng số hay workflow input | **Không. Tuỳ ý định người dùng.** |
| Tham số nên đặt tên gì | **Không** |
| Model đã tóm tắt 50 tin nhắn thành 12 gạch đầu dòng — theo quy tắc nào | **Không, và không suy ra được từ dữ liệu** |

Ba dòng đầu mà để LLM làm thì nó chỉ thêm được lỗi: thông tin đã nằm sẵn, chép
lại bằng suy luận là chép lại có xác suất sai. Ba dòng cuối thì code tất định
không làm nổi — không có thuật toán nào phân biệt được hằng số với biến khi cả
hai đều xuất hiện đúng một lần trong đúng một lần chạy.

Và có một tín hiệu mà chỉ LLM đọc được: **câu yêu cầu gốc bằng ngôn ngữ tự
nhiên**. Khi người dùng gõ *"mỗi sáng đọc kênh #support rồi tổng hợp vào
Notion"*, chữ *"mỗi sáng"* nói rằng kênh là hằng số còn ngày là biến. Không
heuristic nào đọc được câu đó. Câu đó nằm trong `agent.messages`, và từ ADR-0006
nó còn nằm trong payload của bước `model_call` — kể cả khi lượt chat vỡ giữa
chừng.

Ràng buộc chặn phía kia: **workflow phải tất định** (§2.3, §14, ràng buộc 2 của
§47). Nhưng thứ *tạo ra* workflow thì không bắt buộc phải tất định — miễn là
thứ nó tạo ra được kiểm tra trước khi chạy, và §10.2 đã bắt đầu ra luôn là
**draft có người duyệt**.

## Quyết định

**1. Compiler là hybrid, chia theo sáu chặng của §10.**

| Chặng | Ai làm | Vì sao |
|---|---|---|
| 1. Normalize (§10.1) | Tất định | Bỏ timestamp, request id, URL tạm — dọn dữ liệu, không phán đoán |
| 2. Dựng khung | Tất định | Node, cạnh, `tool_id`, version — đọc thẳng từ trace |
| 3. **Generalize (§10.2)** | **LLM** | Đúng danh sách `infer` của §5.2 |
| 4. Validate cấu trúc + schema (§39) | Tất định | Cổng chặn |
| 5. Policy validation (§39) | Tất định | Cổng chặn |
| 6. Draft → người duyệt | Người | §10.2 bắt buộc |

**2. LLM không bao giờ phát ra IR.**

Đây là quyết định trung tâm. Chặng 3 nhận vào một khung đã dựng sẵn cộng một
**tập câu hỏi đóng**, và trả về **lựa chọn**, không trả về IR. Code tất định lắp
ráp IR từ những lựa chọn đó.

Hình dạng minh hoạ — chi tiết wire format thuộc về lúc hiện thực, không bị ADR
này đóng băng:

```json
{
  "yeu_cau_goc": "mỗi sáng đọc kênh #support rồi tổng hợp vào Notion",
  "khung": { "nodes": [ "… đã điền tool_id, version, thứ tự …" ] },
  "cau_hoi": [
    { "id": "L1", "node": "n2", "field": "channel", "gia_tri": "C0912SUPPORT",
      "chon": ["const", "workflow_input", "from_node"] },
    { "id": "S1", "buoc": 5, "mo_ta": "model biến messages thành blocks",
      "chon": ["llm_node", "transform_tat_dinh", "khong_tong_quat_hoa_duoc"] }
  ]
}
```

Điều được đóng băng là **tính chất** của contract đó, không phải cú pháp:

- LLM chọn trong một tập hữu hạn đã liệt kê sẵn, cộng với các trường văn bản tự
  do **không ảnh hưởng tới thực thi** (tên, mô tả);
- LLM không bao giờ gõ `tool_id`, `tool_version`, `connection_id`, `node.type`,
  hay cấu trúc cạnh;
- một câu trả lời hỏng không tạo ra được IR sai cấu trúc — nó chỉ tạo ra một
  lựa chọn sai.

So sánh chế độ hỏng là toàn bộ lý lẽ:

| | LLM gõ IR | LLM chỉ chọn |
|---|---|---|
| Bịa `tool_id` | Có thể | **Bất khả thi về cấu trúc** |
| Sai `tool_version` | Có thể, âm thầm | Bất khả thi |
| Rơi mất một node | Có thể | Bất khả thi |
| Đúng schema, sai ngữ nghĩa | Khó phát hiện | — |
| Hỏng tệ nhất | Workflow chạy sai lúc 3 giờ sáng | Đoán nhầm hằng số/input — người duyệt sửa một lựa chọn |

§10.2 vốn đã bắt phải có người duyệt. Thiết kế này đặt sai lầm của LLM vào đúng
chỗ đã có sẵn cơ chế bắt lỗi, thay vì tạo ra một loại sai lầm mới mà không ai
canh.

**3. Mọi literal trong trace phải địa chỉ hoá được.**

Chặng 2 phải chỉ được tới từng giá trị hằng bằng `(node, path)` để chặng 3 hỏi
về nó. Đây là ràng buộc ngược lên IR v1 và lên bộ dựng khung, và nó là lý do
quyết định này phải có **trước** khi IR v1 được chốt, không phải ở Phase 5.

**4. Đầu ra của compiler đi qua đúng validator với IR viết tay.** Không có đường
vòng, không có cờ "đã tin vì máy sinh ra". Chặng 4 và 5 không biết IR đến từ đâu.

**5. Câu yêu cầu gốc là đầu vào bắt buộc của chặng 3.** Thiếu nó thì chặng 3
không chạy — hạ về hỏi người dùng, không hạ về đoán mò. Đây là tín hiệu duy nhất
phân biệt được hằng số với biến, nên nó không phải là đầu vào tuỳ chọn.

**6. Bước ngôn ngữ thuần tuý ở lại làm node `llm`.**

Khi chặng 3 kết luận một bước không tổng quát hoá được thành phép biến đổi tất
định — *"tóm tắt 50 tin nhắn thành gạch đầu dòng"* — thì bước đó **ở lại workflow
dưới dạng node `llm`**, không bị ép thành `transform`, và cũng không bị đông cứng
thành hằng số (đông cứng 12 gạch đầu dòng của hôm nay là tạo ra một workflow vô
dụng).

Hệ quả trực tiếp: **IR v1 phải có node `llm`.** Không có nó, compiler không có
chỗ đặt kết quả cho cả một lớp bước rất phổ biến.

**7. LLM đến với domain `workflow` qua một port, không qua import.**

Contract 1 cấm `app.domains.workflow` import `app.domains.agent`, nơi có Model
Gateway. Nên chặng 3 được khai bằng một port ở `workflow/application/ports.py`
(tạm gọi `GeneralizationAdvisor`), và hiện thực nằm ngoài domain — đúng khuôn mà
ADR-0003 dùng cho `ExecutionTraceSink` và ADR-0004 dùng cho `ConnectionResolver`.
Cùng lý do, trace đã chuẩn hoá đi **vào** compiler như một tham số, không phải do
compiler tự đi đọc domain `trace`.

Việc hiện thực port đó nằm ở tiến trình nào — Agent Runtime, hay một tiến trình
compiler riêng — để lại cho Phase 5. Port thì không.

## Hệ quả

### Tích cực

- Thứ LLM sai được bị giới hạn bằng cấu trúc, không bằng lời dặn trong prompt.
- IR giữ được tính đáng tin cần thiết để chạy không người trông, dù nó ra đời từ
  một quá trình xác suất.
- Đổi model, đổi prompt, đổi provider không chạm tới IR — chặng 3 nói chuyện với
  phần còn lại bằng một tập lựa chọn hữu hạn.
- Test được mà không cần gọi model: chặng 2 và 4 test bằng dữ liệu; chặng 3 test
  bằng cách đưa sẵn câu trả lời.
- Có đường lùi tự nhiên: mọi câu hỏi mà LLM trả lời được thì người dùng cũng trả
  lời được. Compiler hỏng, hoặc không có ngân sách model, thì UI hỏi thẳng người
  dùng — cùng bộ câu hỏi, không cần đường code thứ hai.

### Tiêu cực / chi phí phải trả

- Compiler hai tầng dài hơn cả hai phương án thuần. Phải viết bộ dựng khung tất
  định **và** tầng câu hỏi.
- **Tập câu hỏi trở thành một contract mới phải đánh version.** §40 liệt kê
  *"prompts where behavior must be reproducible"* và *"Workflow Compiler version"*
  vào danh sách bắt buộc có version. Hiện chưa có cơ chế nào cho cả hai.
- Mỗi lần biên dịch tốn một lời gọi model — chậm hơn và tốn hơn một hàm thuần.
- Tập câu hỏi đóng sẽ chật ở một loại tổng quát hoá chưa từng thấy. Khi đó phải
  mở rộng tập câu hỏi, và đó là sửa code chứ không phải sửa prompt.

### Rủi ro cần theo dõi

- **LLM đoán sai hằng số/input một cách có hệ thống.** Người dùng sẽ thấy draft
  nào cũng phải sửa, và niềm tin vào tính năng rơi nhanh hơn chất lượng của nó.
- **Tập câu hỏi phình thành một ngôn ngữ.** Mỗi lần thêm một loại câu hỏi là một
  bước tiến về phía "LLM gõ IR" bằng cửa sau. Tín hiệu cảnh báo: một câu hỏi có
  trường tự do ảnh hưởng tới thực thi.
- **Trace đi vào prompt.** Payload của trace giờ mang nguyên input/output của
  tool (ADR-0006), và chặng 3 đưa chúng cho model. Rủi ro §2.4 đã có một test
  canh (`test_prompt_luu_xuong_khong_mang_token_cua_nguoi_dung`), nhưng đó là
  test cho đường Agent, không phải cho đường compiler.
- **Chưa có version cho prompt.** Một workflow được biên dịch hôm nay và một cái
  biên dịch tháng sau có thể ra hai kết quả khác nhau từ cùng một trace, mà không
  gì ghi lại vì sao.

## Điều kiện xem xét lại

- Tỉ lệ lựa chọn bị người duyệt sửa vượt quá một phần tư trong vài chục lần biên
  dịch thật. Khi đó sửa prompt hoặc tập câu hỏi — đo trước, sửa sau.
- Ngược lại: LLM gần như luôn chọn đúng **và** đơn điệu (ví dụ hầu hết literal
  đều thành `const`). Khi đó một heuristic tất định có thể gánh phần lớn, và LLM
  lùi về chỉ còn đặt tên. Rẻ hơn, nhanh hơn.
- Xuất hiện một loại tổng quát hoá không diễn đạt được bằng câu hỏi đóng. Khi đó
  mở rộng tập câu hỏi — **không** mở đường cho LLM phát ra IR. Nếu việc mở rộng
  lặp lại đủ nhiều để thành gánh nặng, đó mới là lúc xem lại quyết định 2, bằng
  một ADR mới.
- Nhu cầu biên dịch lại hàng loạt trace cũ khi compiler nâng version. Việc đó
  cần prompt có version trước đã — xem rủi ro trên.

## Phương án đã cân nhắc và loại bỏ

**LLM đọc trace rồi gõ thẳng IR.** Đường ngắn nhất, và là thứ mà "một Agent biến
trace thành workflow" nghe như đang mô tả. Loại bỏ vì bảng chế độ hỏng ở quyết
định 2: nó giao cho quá trình xác suất cả những phần đã nằm sẵn trong trace dưới
dạng chuỗi chính xác, để đổi lấy đúng thứ nó giỏi ở một phần nhỏ. Sai lầm rơi
vào lúc chạy, không rơi vào lúc duyệt.

**Compiler hoàn toàn tất định.** Đây là cách tôi mô tả sai ở bản kế hoạch đầu
tiên của WP-B. Loại bỏ vì nó không làm được chính danh sách `infer` của §5.2:
phân biệt hằng số với workflow input trong một lần chạy duy nhất là việc không có
thuật toán, và heuristic cho nó ("giá trị xuất hiện hai lần thì là hằng số") sẽ
sai theo những cách người dùng không đoán được — tệ hơn một LLM sai có chủ đích
đọc được.

**Không LLM, hỏi thẳng người dùng từng câu.** Đúng đắn và rẻ, nhưng nó biến việc
"lưu lượt chat này thành workflow" thành một biểu mẫu mười lăm câu. Giữ nguyên
làm **đường lùi** — xem mục Tích cực — chứ không làm đường chính.

**Fine-tune một model riêng cho việc sinh IR.** Chưa có dữ liệu, chưa có ai dùng,
và nó khoá chặt vào một hình dạng IR mà chính ADR này nói là sẽ còn đổi.

**Để chặng 3 sửa trực tiếp khung đã dựng** (đưa khung cho LLM, bảo nó trả về khung
đã sửa). Gọn hơn về code, nhưng nó xoá mất tính chất quan trọng nhất của quyết
định 2: khi LLM được phép ghi đè lên khung thì nó lại có thể đổi `tool_id`, và
mọi bảo đảm về cấu trúc biến mất cùng lúc.
