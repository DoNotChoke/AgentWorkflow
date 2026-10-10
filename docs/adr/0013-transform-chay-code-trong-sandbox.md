# ADR 0013 — `transform` chạy code trong sandbox, và sandbox không chạy ở Control Plane

- **Status:** Accepted
- **Date:** 2026-10-10
- **Deciders:** TuanNghia
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.4, §11.2, §39, §40, §47 (điều 19);
  ADR-0002, ADR-0006, ADR-0008, ADR-0009, ADR-0016;
  `docs/plans/wp-c-transform-sandbox.md`

Quyết định này được làm qua ba commit của WP-C (`e8a0435`, `858e752`, `19b46dd`). ADR viết
sau, khi cả ba mặt của nó — tool của Agent, node của IR, và tầng kiểm của compiler — đã
chạy.

## Bối cảnh

Model đếm sai và chép sai. GĐ 0 đo được: ở kịch bản 4, 22 trên 26 giá trị của một bước là
do model viết ra, và nó tự ý viết hoa lại một tin nhắn khi chép từ Slack sang Notion. Đếm,
cộng, so sánh, đổi hình dạng dữ liệu là việc máy làm đúng hơn, rẻ hơn và lặp lại được.

Có hai đường cho việc đó trong một workflow. Thêm dần toán tử vào IR — `count`, `join`,
`format`, `filter` — là một danh sách không có điểm dừng, mỗi lần thêm là sửa schema,
validator và executor. Đường kia là cho một node chạy code.

§47 điều 19 nói code tuỳ ý không nên là cơ chế biểu thức *mặc định*. §11.2 nói điều kiện
để nó được có mặt: chạy tất định, có giới hạn thực thi, truy cập an toàn vào state, không
file system, không mạng. Và ADR-0008 chốt rằng model không bao giờ phát ra IR.

## Quyết định

**IR có một node `transform` chạy một đoạn Python nhỏ, trong một sandbox chỉ có ở Agent
Runtime và Temporal worker.**

1. **Code là một giá trị, không phải cấu trúc.** Nó không đặt `tool_id`, không dựng cạnh,
   không quyết định bước nào chạy. Với code Agent đã chạy trong lần làm mẫu, compiler bê
   nguyên — cùng hạng với `tool_id` đã ghi trong trace. Code do model biên dịch viết thì
   theo ADR-0016. Cả hai không mâu thuẫn với ADR-0008: thứ được dựng tất định vẫn là IR.
2. **Sandbox không bao giờ chạy trong Control Plane.** Đó là tiến trình duy nhất giải mã
   được credential (ADR-0002); một lần thoát sandbox ở đó là một lần lộ token của mọi
   tenant. Sandbox nằm ở `app/core/sandbox/`, một bản cho cả hai tiến trình dùng nó, và
   **contract 8** cấm `app.entrypoints.http` nạp nó. Hệ quả: `system.transform` là tool
   dựng sẵn của Agent Runtime, không phải một tool trong catalog đi qua Gateway.
3. **Hai lớp, cần cả hai.**
   - *Duyệt AST* (`core/codeguard.py`) chặn thứ code viết ra được: `import`, mọi tên bắt
     đầu bằng `_`, `eval`/`exec`/`open`, truy cập thuộc tính động, `while`, `class`,
     `try`. Builtin là một danh sách đóng. Lớp này chỉ đọc, nên Control Plane dùng được
     nó lúc publish.
   - *Tiến trình con có trần* chặn thứ code làm được khi đã chạy: 2 giây, 256 MiB, output
     1 MiB, code 4 KiB. Dữ liệu vào ra là JSON qua stdin và stdout.
4. **Hợp đồng của một đoạn code.** Hàm thuần: vào từ `args`, ra bằng `return`. Không viết
   cứng giá trị nghiệp vụ trong code — ngưỡng và id đi vào qua `args`, vì một hằng nằm
   trong chuỗi code thì compiler không hỏi về nó được. Khai `output_schema`, và kết quả
   thật được kiểm với nó trước khi chảy sang node sau.
5. **Kiểm bằng chính lần chạy mẫu.** Trace giữ input và output thật (ADR-0006), nên trước
   khi một workflow được dựng, code được chạy lại trên dữ liệu đã ghi và kết quả phải khớp.
6. **Phân vai với `loop`.** Lặp để đổi hình dạng dữ liệu là việc của `transform`; lặp để
   gọi ra ngoài là việc của `loop`. Code không gọi được tool.

## Hệ quả

### Tích cực

- Một node phủ mọi phép đếm, lọc và đổi hình dạng, thay cho một danh sách toán tử lớn dần
  trong IR.
- Bước tính toán của workflow tất định: chạy lại cho cùng kết quả, và không tốn token.
- Điều kiện rẽ nhánh có vế trái là một con số do code tính, không phải một nhận định của
  model.

### Tiêu cực / chi phí phải trả

- Validator không nhìn được vào trong code. `m['text']` có thật hay không chỉ biết khi
  chạy; bù lại là `output_schema` lúc chạy và lần chạy lại trên trace lúc biên dịch.
- Mỗi lần chạy khởi động một tiến trình con, tốn vài chục mili giây.
- Ngữ nghĩa của sandbox thành một mặt tương thích: workflow đã publish là bất biến và có
  thể chạy nhiều tháng sau, nên đổi builtin được phép hay đổi trần là đổi hành vi của
  workflow cũ.

### Rủi ro cần theo dõi

- **Thoát sandbox.** Hậu quả đã bị giới hạn: hai tiến trình chạy nó không có đường tới
  credential của connection. Nhưng worker giữ API key của provider model (ADR-0011) và dữ
  liệu của tenant khác đi qua cùng tiến trình. Có một bộ test tấn công riêng: từng đường
  thoát đã biết phải bị chặn.
- **Windows không có trần bộ nhớ.** `resource.setrlimit` chỉ có trên Unix; máy dev chỉ có
  trần thời gian. Sandbox khai ra chế độ nó đang chạy, để thiếu một tầng không trông giống
  như đủ.
- **`transform` thành cái sọt.** Nếu code là cửa thoát vạn năng, workflow dần thành vài
  lời gọi tool cộng một đống code, và phân tích tĩnh của §39 mất chỗ bám. Ba hàng rào:
  trần 4 KiB, code không gọi được tool hay I/O, và cảnh báo `hang_viet_cung`.

## Điều kiện xem xét lại

- Có một đường thoát sandbox thật, không phải trên giấy: khi đó cân nhắc cô lập ở mức hệ
  điều hành (container hoặc microVM cho mỗi lần chạy) thay cho tiến trình con.
- Hơn một phần ba số node của các workflow đã publish là `transform`, hoặc code chạm trần
  4 KiB thường xuyên: đó là dấu hiệu thiếu một node hay một toán tử, không phải dấu hiệu
  cần nới trần.
- Chi phí khởi động tiến trình con thành phần lớn thời gian của một vòng lặp đo được: khi
  đó dùng một pool tiến trình.
- Cần đổi ngữ nghĩa của sandbox theo cách làm workflow cũ cho kết quả khác: khi đó sandbox
  phải có version và IR phải ghim nó (§40).

## Phương án đã cân nhắc và loại bỏ

1. **Thêm toán tử vào IR.** Kiểm tĩnh được trọn vẹn, đổi lại mỗi nhu cầu mới là một lần
   sửa schema, validator và executor, và danh sách không có điểm dừng.
2. **Để model làm phép tính trong node `llm`.** Không cần sandbox, đổi lại đúng thứ GĐ 0
   đã đo: đếm sai, chép sai, không lặp lại được, và tốn token ở mỗi lần chạy.
3. **`system.transform` là một tool trong catalog, chạy ở Gateway.** Rẻ nhất về code, và
   đặt code do model viết vào đúng tiến trình giữ credential.
4. **Một ngôn ngữ biểu thức riêng** (kiểu JSONata hay CEL). An toàn theo thiết kế, đổi lại
   model viết nó kém hơn Python nhiều, và nó vẫn là một ngôn ngữ phải nuôi.
