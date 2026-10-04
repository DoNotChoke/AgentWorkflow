# ADR 0016 — LLM biên dịch được viết node `transform`, và code đó phải tái tạo được lần chạy mẫu

- **Status:** Accepted
- **Date:** 2026-10-01
- **Deciders:** TuanNghia (solo developer)
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.4, §10.2, §11.2, §39, §40; ADR-0008,
  ADR-0015; [`wp-c-transform-sandbox.md`](../plans/wp-c-transform-sandbox.md) §1, §3.3, §5,
  §6c; [`wp-d-workflow-compiler.md`](../plans/wp-d-workflow-compiler.md)

## Bối cảnh

WP-C đặt code của node `transform` trên một lý lẽ: nó *"không phải IR … nó là một giá
trị **đã chạy thật và đã được quan sát**"* — Agent viết nó trong lượt chat, sandbox
chạy nó, trace ghi cả code lẫn kết quả, compiler chỉ bê sang.

Lý lẽ đó cần Agent thật sự gọi `system.transform`. Bốn lần chạy kịch bản 5 ngày
2026-10-01 — *"kênh có nhiều hơn 5 tin nhắn thì đăng"* — cho thấy nó không gọi:

| Lần | Agent có gì | Model làm gì |
|---|---|---|
| 1 | `system.transform` trong danh sách tool | đọc lịch sử, tự kết luận, đăng |
| 2 | thêm prompt riêng *"khi đếm, luôn gọi system.transform"* | xin `limit: 6`, đăng |
| 3, 4 | thêm prompt nền tảng dạy cách dùng tool hệ thống | xin `limit: 6`, đăng |

Xin đúng 6 tin rồi coi *nhận đủ 6* là *nhiều hơn 5* là một cách làm đúng và rẻ. Nó
cũng không để lại phép so nào trong trace. Model chỉ dùng `transform` khi phép tính
không nhẩm nổi (đếm 52 tin theo từng người) hoặc khi phải chép nguyên văn.

Vậy với một điều kiện, compiler không có gì để bê sang. Hai đường còn lại:

- ép Agent gọi `transform` bằng prompt mạnh hơn — bắt mọi lượt chat làm việc thừa để
  phục vụ một tính năng người dùng có thể không bao giờ bấm, và vẫn không bảo đảm;
- giữ mẹo của model: `exists` trên `messages.5`. Ngưỡng đóng cứng trong đường, không
  thành đầu vào được, và `limit` phải bằng ngưỡng cộng một.

## Quyết định

**1. Ở bước biên dịch, LLM được đề xuất một node `transform` mà trace không có.**

Chỉ khi ý đồ đã nêu cần một phép tính hay phép so mà không bước nào trong trace làm.
Trace có sẵn bước `system.transform` thì code của bước đó được bê nguyên — nó vẫn là
nguồn đáng tin hơn.

**2. LLM viết `code`. Mọi thứ khác của node vẫn là lựa chọn đóng.**

`args` chọn trong output của các bước được giữ, hoặc là hằng, hoặc là đầu vào của
workflow. Kiểu kết quả chọn trong một danh sách. Node đứng sau bước nào là một lựa
chọn. Code tất định dựng node và cạnh — quyết định 2 của ADR-0008 giữ nguyên.

`code` là văn bản tự do có ảnh hưởng tới thực thi: ngoại lệ thứ hai của tính chất đó
trong ADR-0008, sau `instructions` của node `llm` (ADR-0015, quyết định 10).

**3. Code phải tái tạo được lần chạy mẫu, trước khi người dùng thấy nó.**

Agent biên dịch chạy ở Agent Runtime, nơi sandbox được nạp (contract 8 chỉ cấm
Control Plane). Nên ngoài vòng duyệt AST và trần độ dài, code còn được **chạy thật**
trên output đã ghi trong trace:

- chạy được, và kết quả khớp kiểu đã khai;
- nếu kết quả nuôi một node `condition`, điều kiện phải ra **đúng nhánh mà trace đã
  đi** — bước đứng sau điều kiện đã chạy trong trace nghĩa là điều kiện phải đúng.

Hỏng ở đâu thì trả về cho LLM sửa, theo vòng tự sửa của ADR-0015.

**4. Giá trị nghiệp vụ đi qua `args`, và `hang_viet_cung` là lỗi với code do LLM viết.**

Luật 2 của WP-C §3.3 chỉ là cảnh báo lúc publish. Với code LLM biên dịch viết, nó chặn:
ngưỡng nằm trong chuỗi code thì không có địa chỉ, không hỏi được *"hằng hay đầu vào"*,
và quyết định 3 của ADR-0008 vỡ.

**5. Code hiện nguyên văn ở chặng xác nhận**, như `instructions`. Người dùng sửa bằng
lời hoặc đồng ý.

**6. Tham số bị model nắn theo ngưỡng phải được nêu ra.**

`limit: 6` đúng với ngưỡng 5 và sai với mọi ngưỡng khác. Giá trị không có nguồn đứng
ở bước mà node `transform` đọc là một giả định phải hiện cho người dùng, không được
lặng lẽ thành hằng.

## Hệ quả

### Tích cực

- Điều kiện biên dịch được mà không cần Agent đổi cách làm việc.
- Ngưỡng nằm ở vế phải của `condition`, nên làm hằng hay đầu vào đều được.
- Prompt nền tảng không phải ép Agent gọi `transform` ở việc vặt.
- Code do LLM viết được kiểm bằng dữ liệu, không bằng lời hứa.

### Tiêu cực / chi phí phải trả

- Lý lẽ gốc của WP-C yếu đi: có một loại code chưa từng chạy trong lượt chat nào.
- Tập câu hỏi thêm một loại mà LLM **thêm** được phần tử — cùng loại rủi ro với
  `dieu_kien`.
- Mỗi lần kiểm tốn một tiến trình con của sandbox.

### Rủi ro cần theo dõi

- **Nhánh chưa đi không kiểm được.** Trace chỉ có một phía của điều kiện. Code đúng
  với lần chạy mẫu có thể sai ở phía kia.
- **`transform` thành cái sọt** (R4 của WP-C), giờ có thêm một người viết. Hàng rào cũ
  vẫn đứng: trần 4 KiB, không I/O, không gọi tool.
- **Người dùng không đọc code.** Đoạn văn mô tả điều kiện do code dựng từ lựa chọn
  (*"số tin nhắn lớn hơn 5"*) phải đứng cạnh code, không thay nó.

## Điều kiện xem xét lại

- Code do LLM viết bị người dùng sửa ở quá một phần tư số phiên có nó.
- Model bắt đầu tự gọi `system.transform` cho điều kiện đơn giản — khi đó đường này
  lùi về làm dự phòng.
- Xuất hiện nhu cầu LLM viết code cho thứ khác ngoài phép tính và phép so phục vụ một
  quyết định. Đó là mở rộng phạm vi, cần ADR riêng.

## Phương án đã cân nhắc và loại bỏ

- **Ép Agent gọi `transform` bằng prompt.** Đã thử bốn lần, không đổi được hành vi, và
  bắt mọi lượt chat trả giá cho việc biên dịch.
- **Mẹo `exists` trên chỉ số.** Ngưỡng đóng cứng; hai con số phải đi cùng nhau mà
  không gì canh.
- **Thêm toán tử đếm vào `condition`.** Giải đúng một ca; ca kế tiếp — tổng, lớn nhất,
  lọc rồi đếm — lại cần toán tử mới. WP-C đã loại hướng này với cùng lý do.
- **Tool tự trả sẵn con số** (`message_count`). Sửa từng tool cho từng phép tính.
