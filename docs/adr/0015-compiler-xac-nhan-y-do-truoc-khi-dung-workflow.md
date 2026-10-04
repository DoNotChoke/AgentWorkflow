# ADR 0015 — Compiler xác nhận ý đồ với người dùng trước khi dựng workflow, và ý đồ quyết định bước nào ở lại

- **Status:** Accepted
- **Date:** 2026-10-01
- **Deciders:** TuanNghia (solo developer)
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.3, §2.4, §5.2, §9, §10, §10.1,
  §10.2, §27, §39, §40, §46; ADR-0003, ADR-0006, **ADR-0008**, ADR-0014;
  [`docs/plans/wp-b-quan-sat-trace.md`](../plans/wp-b-quan-sat-trace.md) (P5, P6)

## Bối cảnh

ADR-0008 chốt compiler là hybrid: code tất định dựng khung từ trace, LLM trả lời một
tập câu hỏi đóng, code lắp IR. Nó để lại ba chỗ hở, và cả ba cùng một gốc — **ý đồ
của người dùng không được nói ra ở đâu cả.**

**1. Ý đồ chỉ được đọc ngầm, từ một câu.** Quyết định 5 của ADR-0008 lấy *câu yêu cầu
gốc* làm tín hiệu phân biệt hằng số với biến. Nhưng một trace là một lượt chat. Công
việc thật thường trải qua vài lượt: lượt đầu tìm, lượt sau sửa yêu cầu, lượt cuối
mới ghi. Câu gốc của lượt cuối có thể chỉ là *"ừ, đăng đi"*.

**2. Khung giữ mọi bước đã thành công.** ADR-0008 liệt *"rơi mất một node: bất khả
thi"* thành một bảo đảm. Trace thật thì có những bước không thuộc về công việc: Agent
tìm nhầm trang rồi tìm lại, liệt kê kênh mà không dùng, đăng một tin thử trước tin
thật. Luật duy nhất hiện có là bỏ bước `failed` (P6). Một workflow chép lại cả những
bước đó chạy đúng theo trace và sai theo ý người dùng.

**3. LLM đoán sai thì người dùng chỉ thấy ở draft.** Chặng 6 đưa cho người duyệt một
IR. Họ phải đọc ngược từ binding ra giả định — *"à, nó coi kênh là hằng số"* — thay
vì được hỏi thẳng. Sai lầm về ý đồ bị phát hiện ở chỗ đắt nhất để đọc.

§46 gọi trace → workflow là khái niệm trung tâm của sản phẩm. Một hệ thống làm việc
đó mà không biết workflow phục vụ nhiệm vụ gì thì chỉ đang ghi âm lại.

## Quyết định

**1. Biên dịch mở đầu bằng một phiên hội thoại, và đi qua hai lần xác nhận.**

Khi người dùng bấm chuyển một cuộc hội thoại thành workflow, hai việc được làm theo
thứ tự, mỗi việc do một lời gọi model riêng đảm nhận và mỗi việc kết thúc bằng một lần
người dùng xác nhận:

1. **hiểu ý đồ** — workflow này để làm gì;
2. **trả lời tập câu hỏi** theo ý đồ đã được chấp nhận.

Không có draft nào được dựng trước lần xác nhận thứ hai.

**2. Model hiểu ý đồ chỉ đọc conversation và tên các tool đã gọi.**

Nó thấy tin nhắn của người dùng, câu trả lời cuối của Agent ở từng lượt, và mỗi lời
gọi tool dưới dạng *tên tool, thành công hay hỏng*. Nó **không** thấy tham số hay
output của tool. Ý đồ nằm trong lời người dùng nói; payload ở bước này chỉ là chi phí
và nhiễu.

Nó đề xuất ý đồ bằng lời: nhiệm vụ, thứ thay đổi mỗi lần chạy, thứ cố định, điều kiện
nếu có, và việc không thuộc nhiệm vụ. Người dùng chat lại để sửa; vòng này lặp tới khi
họ chấp nhận.

Người dùng nói chuyện với nó ở một cửa sổ chat riêng, không phải trong conversation
nguồn — conversation nguồn là nguyên liệu, không bị ghi thêm.

**Chấp nhận là một nút bấm, không phải một câu chat.** Chat để đổi; nút để chốt. Model
không phải đoán một câu có phải là đồng ý hay không.

**3. Ý đồ đã chấp nhận là căn cứ của model thứ hai, và model đó không viết lại nó.**

Ý đồ thay *câu yêu cầu gốc* ở quyết định 5 của ADR-0008. Model thứ hai nhận ý đồ đã
chấp nhận, tập câu hỏi, và phần dữ liệu trace mà các câu hỏi cần — đã cắt ngắn. Nó trả
lời bằng lựa chọn. Câu nêu nhiệm vụ không nằm trong câu trả lời của nó.

Các lựa chọn đó hiện cho người dùng dưới dạng một bảng giả định; họ sửa bằng lời — câu
sửa đi tới model thứ hai kèm câu trả lời hiện tại — hoặc bấm tạo workflow.

**4. Bước nào ở lại do ý đồ quyết định — không do loại bước.**

Câu hỏi cho từng bước là *"nhiệm vụ đã xác nhận có cần bước này không"*. Bước đọc
hay bước ghi không phải là tiêu chí: một tin nhắn thử đã đăng thật vẫn bị bỏ nếu
nhiệm vụ không cần nó, và một lần tìm kiếm vẫn ở lại nếu nhiệm vụ cần kết quả của
nó, kể cả khi output của nó không xuất hiện nguyên văn ở bước nào sau.

Bước `failed` vẫn bị bỏ theo luật cứng của P6, trước khi câu hỏi này được đặt ra.

**5. Bỏ bước là một lựa chọn, không phải một lần viết lại.**

Quyết định 2 của ADR-0008 — *LLM không bao giờ phát ra IR* — giữ nguyên. Tập câu hỏi
đóng có thêm một loại: với mỗi bước của khung, `giu` hoặc `bo`. Code tất định là thứ
gỡ node. Validator ở chặng 6 chặn trường hợp một node bị bỏ mà node khác còn trỏ tới.

Bảo đảm của ADR-0008 đổi từ *"không thể rơi mất một node"* thành **"không thể rơi mất
một node trong im lặng"**: mọi bước bị bỏ đều nằm trong tin nhắn giả định trước khi
bỏ, và nằm trong draft sau khi bỏ, khôi phục được.

**6. Mỗi giả định ứng với đúng một lựa chọn đóng.**

Câu phát biểu nhiệm vụ là văn bản tự do và không ảnh hưởng tới thực thi. Mọi giả định
khác — hằng số hay đầu vào, giữ hay bỏ, lượt nào được dùng — là một lựa chọn trong
tập hữu hạn. Người dùng sửa bằng lời; Agent dịch lời đó thành lựa chọn; thứ đi tiếp
vào bộ lắp IR chỉ là các lựa chọn.

**7. Conversation là ngữ cảnh; node chỉ đến từ bước có thật trong trace.**

Agent đọc tin nhắn để hiểu, nhưng không dựng được node nào mà trace không có. Nó
được gom bước từ nhiều lượt của cùng một conversation; danh sách lượt được dùng là
một giả định phải được xác nhận.

**8. Ý đồ đã xác nhận được lưu kèm workflow, ngoài phần thực thi.**

Nó không nằm trong những gì executor đọc. Nó ở đó cho người đọc workflow, và cho
việc sửa workflow bằng lời về sau — một Agent sửa workflow cần biết workflow để làm
gì trước khi đổi nó.

**9. Phiên biên dịch có trạng thái, và kết thúc ở một draft.**

Phiên giữ: các trace nguồn, ý đồ đã xác nhận, các lựa chọn, draft đang dựng. Đầu ra
vẫn là draft đi qua đúng validator với IR viết tay, và vẫn không tự publish (§10.2).

**10. `instructions` của node `llm` do LLM viết, và hiện nguyên văn ở chặng xác nhận.**

Một bước ngôn ngữ ở lại làm node `llm` (quyết định 6 của ADR-0008) cần `instructions`
và `output_schema`. Trace không có sẵn hai thứ đó — nó chỉ có prompt của Agent. LLM
là thứ viết chúng.

Đây là ngoại lệ duy nhất của tính chất *"văn bản tự do không ảnh hưởng tới thực
thi"* trong ADR-0008, nên nó không được đi tắt: `instructions` của từng node `llm`
nằm nguyên văn trong tin nhắn giả định, người dùng sửa bằng lời hoặc đồng ý, như mọi
giả định khác. Không rút gọn, không tóm tắt.

**11. Thứ LLM sinh ra được kiểm ở ba tầng.**

| Tầng | Kiểm gì | Trạng thái |
|---|---|---|
| 1. Lựa chọn | Câu hỏi có thật; giá trị nằm trong tập đã liệt kê; không câu nào bỏ trống; nguồn `from_node` là ứng viên bộ so khớp đã tìm ra; tên đầu vào hợp lệ, không trùng | viết mới |
| 2. IR đã lắp | `validate(ir, catalog)` hiện có — cấu trúc, schema, catalog tool — rồi policy | dùng lại nguyên |
| 3. Đối chiếu với trace | Nạp giá trị của lần chạy mẫu, lấy output đã ghi trong trace thay cho mọi lời gọi ra ngoài, tính input của từng node và so với input trace đã thật sự gửi | viết mới |

Tầng 3 **không gọi tool nào**, đọc hay ghi: nó chỉ tính binding trên dữ liệu trace đã
lưu, nên không có tác động nào lặp lại. Nó bắt thứ tầng 2 không thấy: một binding
đúng kiểu mà trỏ sai chỗ. Chỗ nào
người dùng đã chủ động đổi so với lần chạy mẫu thì không so.

**12. Lỗi ở bất kỳ tầng nào đều trả thẳng về cho LLM sửa.**

Cả ba tầng báo lỗi bằng cùng một hình dạng — mã lỗi, node, trường, thông báo — và
báo **mọi** lỗi một lần, không dừng ở lỗi đầu. LLM nhận báo cáo, đổi lựa chọn, và cả
ba tầng chạy lại từ đầu.

- Vòng sửa có trần số lần. Hết trần thì dừng và nêu những lỗi còn lại cho người
  dùng — không lặp vô hạn, không giấu lỗi.
- LLM sửa bằng cách **đổi lựa chọn**, không bằng cách sửa IR. Quyết định 5 vẫn giữ.
- Vòng sửa chạy **trước** khi người dùng thấy bảng giả định. Người dùng chỉ xác nhận
  một bảng đã qua cả ba tầng, nên không lần sửa nào đổi được thứ họ đã xác nhận.

**13. Sau lần xác nhận thứ hai thì dừng: không còn lời gọi model nào.**

Sau nút tạo workflow, code tất định lắp IR từ đúng các lựa chọn đã hiện cho người dùng
và lưu draft. Thứ được lưu là thứ đã được xác nhận.

**14. Sáu chặng của ADR-0008 thành chín.**

| Chặng | Ai làm |
|---|---|
| 1. Normalize | Tất định |
| 2. Dựng khung, từ mọi lượt của conversation | Tất định |
| 3. Đề xuất ý đồ, từ conversation và tên tool | Model thứ nhất |
| **4. Chấp nhận ý đồ** | **Người; muốn đổi thì quay về chặng 3** |
| 5. Trả lời tập câu hỏi theo ý đồ: generalize, giữ/bỏ bước | Model thứ hai chọn |
| 6. Validate: lựa chọn, cấu trúc + schema, policy, đối chiếu với trace | Tất định; lỗi quay về chặng 5 |
| **7. Xác nhận bảng giả định** | **Người; muốn đổi thì quay về chặng 5** |
| 8. Lắp IR, lưu draft | Tất định, không gọi model |
| 9. Draft → người duyệt | Người |

Agent biên dịch với tới LLM, conversation và trace theo đúng khuôn của quyết định 7
trong ADR-0008: qua port, không qua import. Nó chạy ở tiến trình nào để lại cho
Phase 5.

## Hệ quả

### Tích cực

- Sai lầm về ý đồ bị bắt ở chỗ rẻ nhất: một câu tiếng Việt người dùng đọc được,
  trước khi có IR.
- Workflow chỉ chứa những bước nhiệm vụ cần, không chứa lịch sử mò mẫm của Agent.
- Công việc trải qua nhiều lượt chat biên dịch được.
- Workflow biết nó để làm gì. Đó là nền cho việc sửa workflow bằng lời.
- Draft tới tay người duyệt đã qua cả ba tầng kiểm. Người duyệt đọc ý, không đi tìm
  lỗi cấu trúc.
- Tầng 3 kiểm được bằng dữ liệu rằng workflow tái tạo đúng lần chạy mẫu, trước khi
  nó chạm vào provider nào.
- Đường lùi của ADR-0008 — *"mọi câu LLM trả lời được thì người dùng cũng trả lời
  được"* — thành đường chính: người dùng luôn nhìn thấy các câu trả lời.

### Tiêu cực / chi phí phải trả

- Biên dịch không còn là một cú bấm. Có ít nhất một lượt hỏi–đáp trước khi thấy
  draft.
- Thêm một thứ có trạng thái phải lưu, phải hết hạn, phải dọn: phiên biên dịch.
- Nhiều lời gọi model hơn cho mỗi lần biên dịch, và prompt mang cả conversation.
- Tập câu hỏi đóng — vốn đã là một contract phải đánh version — có thêm loại `giu`/`bo`
  và loại *lượt nào được dùng*.
- Hai bộ kiểm phải viết mới (tầng 1, tầng 3), và một ánh xạ ngược từ lỗi của IR về
  lựa chọn đã gây ra nó — không có ánh xạ đó thì LLM không biết phải đổi gì.
- Mỗi vòng sửa là thêm một lời gọi model.
- Bộ dựng khung phải làm việc trên nhiều trace, và giữ được thứ tự giữa các lượt.

### Rủi ro cần theo dõi

- **Bỏ nhầm một bước cần thiết.** Validator chỉ bắt được khi còn node trỏ tới bước
  đó. Một bước ghi không ai tiêu thụ output — đúng loại bước mà workflow tồn tại để
  làm — bị bỏ thì không validator nào kêu. Chốt chặn duy nhất là người dùng đọc danh
  sách bước bị bỏ.
- **Người dùng đồng ý mà không đọc.** Khi đó vòng xác nhận chỉ là một cú bấm thừa, và
  mọi sai lầm lại rơi về draft như trước.
- **Tin nhắn giả định phình thành biểu mẫu.** ADR-0008 đã loại *"biểu mẫu mười lăm
  câu"*. Một trace dài sinh ra nhiều giả định; tin nhắn phải nêu những cái đáng hỏi,
  không liệt kê tất cả.
- **`instructions` là văn bản LLM viết mà executor chạy.** Không validator nào kiểm
  được nó đúng ý. Chốt chặn duy nhất là người dùng đọc nó ở chặng 7.
- **Vòng sửa hội tụ về một IR hợp lệ mà sai ý.** LLM bị ép qua validator có thể đổi
  lựa chọn theo hướng dễ qua nhất — bỏ luôn bước đang lỗi. Vì vòng sửa chạy trước
  khi xác nhận, bước bị bỏ theo cách đó vẫn hiện trong tin nhắn giả định; chốt chặn là
  người dùng đọc nó.
- **Tầng 3 báo lỗi giả.** Output có thành phần không tất định — thời gian, id sinh
  ngẫu nhiên — sẽ lệch dù binding đúng. Normalize ở chặng 1 phải dọn được chúng.
- **Các lượt mâu thuẫn nhau.** Người dùng đổi ý giữa conversation. Agent phải nêu nó
  theo phát biểu nào, không lặng lẽ chọn.
- **Conversation và payload của trace đi vào prompt.** Rủi ro §2.4 mà ADR-0008 đã
  nêu rộng hơn một bậc. Đường compiler cần test riêng canh credential, như đường
  Agent đã có.

## Điều kiện xem xét lại

- Hầu hết phiên kết thúc bằng đồng ý ngay ở đề xuất đầu tiên, **và** draft sau đó
  hiếm khi bị sửa. Khi đó cho phép bỏ vòng xác nhận với những trace đơn giản.
- Ngược lại: draft bị sửa ở quá một phần tư số phiên dù ý đồ đã được xác nhận. Vòng
  xác nhận không bắt được thứ nó sinh ra để bắt — xem lại hình dạng tin nhắn giả
  định trước khi xem lại quyết định.
- Bước bị bỏ được người dùng khôi phục thường xuyên. Tiêu chí *"nhiệm vụ có cần
  không"* đang được LLM áp quá tay.
- Một phiên trung bình cần hơn ba lượt mới tới đồng ý.
- Vòng sửa thường xuyên chạm trần. Tập câu hỏi hoặc báo cáo lỗi đang không đủ để LLM
  tự sửa.
- Tầng 3 báo lệch ở những chỗ người duyệt coi là đúng. Khi đó hạ nó xuống cảnh báo
  trước khi bỏ nó.

## Phương án đã cân nhắc và loại bỏ

- **Giữ nguyên ADR-0008: suy ý đồ trong im lặng, duyệt ở draft.** Loại vì ba chỗ hở ở
  phần Bối cảnh.
- **Bỏ bước theo loại: bước đọc được bỏ, bước ghi luôn giữ.** Loại. Tiêu chí là nhiệm
  vụ cần gì. Một tin nhắn thử là bước ghi mà workflow không cần; một lần tìm kiếm là
  bước đọc mà workflow có thể không sống thiếu.
- **Tỉa tất định theo phụ thuộc dữ liệu** — bỏ bước nào không ai dùng output. Loại:
  output đi qua suy luận của model thì không để lại dấu so khớp được, và bước ghi
  cuối cùng thì không bao giờ có ai dùng output.
- **Để LLM viết lại IR sau khi đã hiểu ý đồ.** Loại, cùng lý do ADR-0008 loại *"LLM gõ
  thẳng IR"*. Hiểu ý đồ tốt hơn không làm cho việc chép `tool_id` bằng suy luận an
  toàn hơn.
- **Lỗi validate đưa thẳng cho người dùng.** Loại: người dùng xác nhận ý đồ, không gỡ
  lỗi binding. Họ chỉ thấy lỗi khi LLM đã hết trần mà không sửa được.
- **Chỉ kiểm IR đã lắp, bỏ tầng 1 và tầng 3.** Loại: lỗi ở lựa chọn hiện ra dưới dạng
  lỗi IR khó truy ngược, và binding trỏ sai chỗ thì lọt.
- **Để người dùng tự viết `instructions`.** Loại: đó là bắt người dùng viết prompt.
  LLM viết, người đọc và sửa.
- **Hỏi từng giả định một.** Loại: đó là biểu mẫu mười lăm câu dưới dạng chat.
- **Chỉ biên dịch một lượt do người dùng chọn.** Loại: bắt người dùng biết trước lượt
  nào chứa công việc, và không biên dịch được công việc trải qua nhiều lượt.
