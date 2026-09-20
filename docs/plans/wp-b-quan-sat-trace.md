# WP-B GĐ 0 — Sáu trace thật nói IR phải mang được những gì

- **Ngày chạy:** 2026-09-17
- **Liên quan:** [`wp-b-workflow-ir-va-generic-executor.md`](wp-b-workflow-ir-va-generic-executor.md) Q1;
  ADR-0006, ADR-0008

Đây là bước Q1 của WP-B: chạy sáu kịch bản thật qua Agent rồi đọc trace, trước
khi viết một dòng nào của IR v1. `packages/schemas/README.md` đòi đúng việc này —
*"chốt IR quá sớm, trước khi có dữ liệu thật từ Agent, gần như chắc chắn dẫn tới
một contract sai mà lại đã bị đóng băng."*

---

## 0. Cách chạy

Tài khoản thật: `usr_7732672e…` / org `org_4d9d8596…` / workspace `ws_28cc4e0f…`.
Agent `agt_4862d065…` ("Notion-Slack Agent", `claude-sonnet-5`, đủ 12 tool).
Connection Slack `conn_69af1972…` (workspace, đủ bốn scope) và Notion
`conn_0619c77b…` (private).

Mỗi kịch bản là một hội thoại mới, chốt connection cho cả hai provider, gửi đúng
một tin nhắn, đọc hết SSE, rồi tải trace kèm payload từng bước qua ba endpoint
đọc của domain `trace`.

**Dev DB không dùng lại được.** Nó có 16.679 user, 1.025 trace, 4.439 connection
— gần như toàn bộ là dữ liệu do test suite sinh ra. Trace cũ mang `channel:
"C123"` và `text: "xin chào"`. Chỉ có hai connection thật (Slack "TuanNghia",
Notion "Nghĩa Tuấn's Notion"), nên sáu kịch bản phải chạy mới hoàn toàn.

---

## 1. Bảng tổng

| Kịch bản | step | tool call | literal | từ câu gốc | từ node trước | do model đặt ra |
|---|---:|---:|---:|---:|---:|---:|
| 1 — Slack một bước ghi | 5 | 1 | 2 | 2 | 0 | 0 |
| 2 — Slack đọc rồi ghi | 8 | 2 | 2 | 1 | 1 | 0 |
| 3 — Notion đọc sâu | 19 | 7 | 7 | 1 | 6 | 0 |
| 4 — bắc cầu hai provider | 8 | 2 | 26 | 4 | 0 | **22** |
| 5 — rẽ nhánh | 8 | 2 | 4 | 3 | 0 | 1 |
| 6 — hỏng giữa chừng | 5 | 1 | 1 | 1 | 0 | 0 |
| **Tổng** | **53** | **15** | **42** | **29 %** | **17 %** | **55 %** |

*Caveat của phép đo:* bộ so khớp chỉ đối chiếu chuỗi. Giá trị số không bao giờ
khớp được với câu gốc, nên `limit: 10` của kịch bản 4 — vốn đến thẳng từ chữ
*"10 tin nhắn gần nhất"* — bị xếp nhầm vào cột cuối. Bài học cho compiler: **so
khớp literal với câu gốc phải ép kiểu**, không so nguyên dạng.

Ngay cả khi trừ đi chỗ nhầm đó, cột cuối vẫn áp đảo — và toàn bộ trọng lượng của
nó nằm ở đúng một bước.

---

## 2. Bảy phát hiện

### P1 — Payload nằm ở bước Gateway, không ở bước của Agent

Mỗi lời gọi tool để lại **hai** bước `tool_call`: bước của `AgentRuntime` (gốc
cây) và bước của Gateway lồng dưới nó qua `parent_step_id`. Chỉ bước thứ hai
mang payload.

```
model_call      parent=None       in=available
tool_call       parent=None       tool=slack.list_channels   in=-          ← Agent
tool_call       parent=stp_60b8…  tool=slack.list_channels   in=available  ← Gateway
```

Compiler phải đi xuống tầng con để lấy giá trị, và lên tầng cha để lấy thứ tự.
Không phải lỗi — nhưng nếu Phase 5 đọc nhầm tầng thì nó thấy một trace rỗng
literal và sẽ kết luận sai rằng không có gì để tổng quát hoá.

### P2 — Cạnh dữ liệu có thật, và một nửa trong số đó trỏ vào chỉ số mảng

Kịch bản 2, model tìm kênh tên *test* rồi đăng vào đó:

```
n2  slack.post_message
      .channel   C0BTGNJ63TK   ← slack.list_channels#channels.4.id
```

Ràng buộc đúng về mặt giá trị, **sai hoàn toàn về mặt ý nghĩa**. Ý định là *"kênh
có `name == test"`*; thứ ghi lại được là *"phần tử thứ 4"*. Lần chạy sau Slack
đổi thứ tự thì workflow đăng nhầm kênh — âm thầm, không lỗi.

Kịch bản 3 lặp lại y hệt với `results.0.id`.

**Hệ quả cho Q8:** binding `from_node` chỉ có `path` là **không đủ**. Cần một
trong ba, và phải chọn ở IR v1:

1. thêm dạng chọn theo điều kiện — `{"from_node": {"node": "n1", "where": {"name": {"from_input": "ten_kenh"}}, "path": ["id"]}}`;
2. để chỉ số, nhưng validator cảnh báo và compiler mặc định hỏi lại;
3. coi đây là bước ngôn ngữ, đẩy sang node `llm`.

Đề xuất: **(1) cho trường hợp một chiều lọc bằng nhau**, vì nó phủ được cả hai
lần xuất hiện ở đây và không mở ra một ngôn ngữ biểu thức. Chỉ số thô vẫn cho
phép, nhưng validator đánh dấu.

### P3 — Fan-out xuất hiện ngay ở kịch bản thứ ba

Kịch bản 3, model gọi `notion.get_page` **bốn lần**, mỗi lần cho một phần tử của
`get_block_children#results`:

```
n4 .page_id ← get_block_children#results.0.id
n5 .page_id ← get_block_children#results.1.id
n6 .page_id ← get_block_children#results.2.id
n7 .page_id ← get_block_children#results.3.id
```

Compiler tất định sẽ trải phẳng thành bốn node — đúng cho lần chạy này, sai cho
mọi lần sau, vì lần sau trang AWS có thể có sáu con.

**Đây là node `loop`, và kế hoạch WP-B đang hoãn nó** (Q2). Ba kịch bản đọc là đã
gặp. Hoặc `loop` vào v1, hoặc compiler phải biết nói *"đoạn này chưa biểu diễn
được"* thay vì trải phẳng.

Chưa đổi Q2 ngay: cần biết đây là đặc thù của Notion (cấu trúc cây) hay là hình
dạng phổ biến. Nhưng phải quyết trước khi chốt IR v1.

### P4 — 22 trên 26 literal của một bước là do model viết ra

Kịch bản 4, `notion.create_page`:

```
.title            [WP-B] Tổng hợp kênh test              ← câu gốc
.parent_page_id   31eaeca4-…                             ← câu gốc
.blocks.0.type    heading_2                              ← model
.blocks.0.text    Tóm tắt 10 tin nhắn gần nhất - kênh…   ← model
.blocks.1.text    [WP-B] Kịch bản 2 — đọc rồi ghi        ← model
… 11 block, 22 literal
```

Không literal nào trong `blocks` khớp được với output của
`slack.get_channel_history`. Kể cả những dòng *trông như* chép lại cũng đã bị
viết lại — tin nhắn Slack thật là `[WP-B] kịch bản 2` (chữ k thường), trong
trang Notion thành `[WP-B] Kịch bản 2`.

**Đây là quyết định 6 của ADR-0008, quan sát được trong thực tế.** Compiler tất
định đứng trước 22 literal không nguồn chỉ có hai lựa chọn, và cả hai đều hỏng:
đông cứng thành hằng (workflow luôn tạo ra đúng trang của hôm nay), hoặc bỏ
trống (workflow không chạy được).

Chỗ đúng là một node `llm`: *"tóm tắt `messages` thành mảng block"*, với
`output_schema` khớp `notion.create_page.blocks`.

**Xác nhận: node `llm` phải có trong IR v1.** Q4 đã sửa theo hướng này, và đây là
số liệu đứng sau nó.

### P5 — Có literal không đến từ đâu cả

Kịch bản 5, `slack.get_channel_history.limit = 200`. Người dùng không nói 200;
model tự chọn giá trị lớn nhất mà schema cho phép để chắc chắn đếm đủ.

Nó không phải hằng số người dùng muốn, không phải workflow input, không phải
output của node trước. Nó là **lựa chọn kỹ thuật của model**.

Tập lựa chọn ở chặng 3 của ADR-0008 vì vậy cần **bốn** giá trị, không phải ba:
`const` · `workflow_input` · `from_node` · **`mac_dinh_cua_tool`** — nghĩa là bỏ
hẳn tham số đó ra khỏi IR và để giá trị mặc định trong tài liệu tool quyết định.

### P6 — Một tool call hỏng không làm trace hỏng

Kịch bản 6 gọi `slack.get_channel_history` với kênh không tồn tại:

```
trace status: succeeded   error_category: None
  model_call      succeeded
  tool_call       failed     slack.get_channel_history   err=ValidationError
  tool_call       failed     slack.get_channel_history   err=ValidationError
  model_call      succeeded
  final_response  succeeded
```

Lượt vẫn `succeeded` vì model nhận lỗi rồi trả lời người dùng tử tế. Ý định ban
đầu của kịch bản 6 — *"một lượt vỡ giữa chừng"* — **không tái hiện được bằng một
tool lỗi**; muốn có lượt vỡ thật thì phải cắt kết nối model hoặc chạm trần
`max_tool_calls_per_turn`.

Việc cần làm ở Phase 5: compiler phải **loại bước `failed` ra khỏi workflow**.
Đó là ngõ cụt mà model đã thoát ra, không phải một bước của quy trình. Không có
luật đó thì mọi workflow biên dịch từ một lượt có thử-và-sai sẽ mang theo cả cái
sai.

### P7 — Hai tool cùng nói về một thứ mà trả về hai hình dạng

`notion.search` trả `results[]` gồm `id · object · url · created_time ·
last_edited_time · archived` — **không có `title`**. `notion.get_page` trả `id ·
url · title · archived · parent_type · created_time · last_edited_time` — **có
`title`**.

Nên *"tìm trang tên AWS"* không làm được bằng một lời gọi `search`: tên trang chỉ
nằm trong slug của `url`. Ở kịch bản 3 model đã lách bằng cách đọc slug rồi gọi
`get_page` để xác nhận.

Đây không phải vấn đề của IR mà là **thiếu sót của `output_mapping` trong tài
liệu tool Notion**. Nên sửa trước khi Phase 5 chạy, vì nếu không thì mọi workflow
tìm trang theo tên đều phải đi qua một node `llm` đọc URL — một node `llm` sinh
ra bởi một chỗ thiếu dữ liệu chứ không phải bởi một việc cần ngôn ngữ.

---

## 3. Việc phải làm trước khi viết `ir.schema.json`

| # | Việc | Từ |
|---|---|---|
| 1 | Binding `where` cho `from_node`, chỉ so bằng — **đã đưa vào Q8** | P2 |
| 2 | `loop` vào v1 — **đã chốt**, xem Q13 của plan WP-B | P3 |
| 3 | Node `llm` vào v1 — **đã chốt ở Q4** | P4 |
| 4 | Thêm lựa chọn thứ tư `mac_dinh_cua_tool` vào chặng 3 của ADR-0008 | P5 |
| 5 | Luật compiler: bỏ bước `failed` | P6 |
| 6 | `notion.search` trả `title` — **đã làm**, minor bump lên 1.1 | P7 |
| 7 | So khớp literal với câu gốc phải ép kiểu | §1 |

Mục 6 nằm ngoài WP-B — nó là một sửa đổi tài liệu tool, làm được ngay và độc lập.

## 4. Dọn dẹp

Bốn tin nhắn `[WP-B]` trong kênh Slack `#test` và trang Notion *"[WP-B] Tổng hợp
kênh test"* dưới trang AWS là rác của lần chạy này — xoá tay khi không cần đối
chiếu nữa. Sáu file trace JSON nằm trong thư mục scratchpad của phiên, không
commit.
