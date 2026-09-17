# ADR 0007 — Sổ idempotency có lease, và `unknown` là một trạng thái riêng

- **Status:** Accepted
- **Date:** 2026-09-17
- **Deciders:** TuanNghia (solo developer)
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.5, §6.3, §15.1, §37, §47 (ràng buộc 5, 6);
  ADR-0002

## Bối cảnh

Sổ idempotency (`integration.tool_invocation_idempotency`, migration 0007) có ba
trạng thái: `in_flight`, `succeeded`, `failed`. Đường đi cũ của Gateway là:

```python
# INSERT ... ON CONFLICT DO NOTHING; trả về hàng cũ nếu nó đã `succeeded`
replay = await self._claim(tool, command, key)
if replay is not None:
    return ...
# còn lại — kể cả `in_flight` treo, kể cả `failed` không rõ — thì chạy tiếp
```

Ba chỗ hỏng, và cả ba đều đánh vào đúng §2.5 — *"Retry behavior must never assume
that failure to receive a response means the provider operation did not happen."*

**1. `failed` nói hai điều khác nhau bằng một từ.** Provider trả 400 và provider
không trả gì sau một timeout đều được ghi là `failed`. Điều thứ nhất nghĩa là
chắc chắn không có tác động; điều thứ hai nghĩa là có thể tin nhắn Slack đã được
đăng. Hàng `failed` không phải `replayable`, nên cả hai đều cho lời gọi sau chạy
lại — trường hợp thứ hai là đăng tin nhắn lần thứ hai.

`OutcomeUnknownError` và `Delivery.UNKNOWN` đã tồn tại ở tầng lỗi, và `may_retry`
(commit 7f6aaae) đã dùng chúng để chặn Gateway *tự* gọi provider lần hai. Nhưng
thông tin đó chết ngay khi lời gọi kết thúc: sổ không có ô nào để ghi lại nó, nên
lời gọi *tiếp theo* không biết gì.

**2. Hàng `in_flight` không có hạn.** Gateway chết giữa chừng — tiến trình bị
kill, pod bị evict — thì hàng ở lại `in_flight` vĩnh viễn. Không ai đọc trạng thái
đó cả (code cũ chỉ hỏi `replayable`), nên thực tế nó không khoá gì; nhưng nó cũng
không nói được gì. Cùng một hàng `in_flight`, hai câu chuyện: một lời gọi đang
chạy bình thường, và một lời gọi đã chết sau khi đã gửi request đi.

**3. Sổ nằm trong transaction của request.** `SqlAlchemyIdempotencyStore` nhận
`AsyncSession` của request qua `Depends(get_session)` và chỉ `flush()`. Một lỗi
bất kỳ sau đó — ghi trace hỏng, serialize response hỏng — rollback cả sổ. Slack
đã đăng tin, Postgres thì không còn dấu vết nào nói rằng nó đã đăng.

Cả ba đều là ràng buộc 5 của §47 (*"Side effect ra bên ngoài phải có chiến lược
idempotency"*), và ràng buộc đó là thứ Phase 4 sắp dựa vào: Temporal Activity
retry sẽ gọi lại đúng tool ghi này với đúng khoá đó.

## Quyết định

**1. `unknown` là trạng thái thứ tư, và nó là trạng thái cuối.**

| trạng thái | nghĩa |
|---|---|
| `in_flight` | đang có người chạy |
| `succeeded` | đã xong, có kết quả để phát lại |
| `failed` | **chắc chắn không có tác động** — provider từ chối, hoặc yêu cầu chưa từng rời khỏi máy |
| `unknown` | **có thể đã có tác động, không xác nhận được** |

Nhánh chốt sổ chọn giữa `fail` và `mark_unknown` bằng đúng một điều kiện:
`error.category is ErrorCategory.OUTCOME_UNKNOWN`. Phân loại đó do tầng lỗi làm,
không do sổ đoán lại.

**2. Lease là `claim_token` + `lease_expires_at`, và mọi lần chốt là
compare-and-set.**

`claim_token` (24 byte từ `secrets.token_urlsafe`) là *ai đang giữ quyền chạy*.
`complete`, `fail`, `mark_unknown` đều mang token đó trong mệnh đề `WHERE` và trả
`bool`: `False` nghĩa là người khác đã tiếp quản hàng này trong lúc mình chạy.
`lease_expires_at` là *quá mốc này mà vẫn `in_flight` thì người giữ đã chết*.

Độ dài lease = `tool.timeout.total_seconds + LEASE_MARGIN_SECONDS` (30s). Vế sau
bù cho việc ghi trace và lệch đồng hồ giữa các tiến trình.

**3. Bảng quyết định nằm ở tầng domain, và nó tách theo tool đọc / tool ghi.**

`decide_claim(record, mutates=..., now=...)` — một hàm thuần, không I/O:

| trạng thái hàng đã có | tool đọc | tool ghi |
|---|---|---|
| `succeeded` | `REPLAY` | `REPLAY` |
| `in_flight`, còn lease | `IN_PROGRESS` | `IN_PROGRESS` |
| `in_flight`, hết lease | `TAKE_OVER` | `MARK_UNKNOWN` |
| `failed` | `TAKE_OVER` | `TAKE_OVER` |
| `unknown` | `TAKE_OVER` | `UNKNOWN` |

Hai ô ở cột phải là toàn bộ lý do ADR này tồn tại. **Một tool ghi không bao giờ
tự chạy lại trên một hàng mà kết quả không rõ** — nó chuyển hàng sang `unknown`
rồi ném `OutcomeUnknownError` với lời nhắn *"Đừng gọi lại: hãy kiểm tra kết quả
trước"*. Tool đọc thì tiếp quản và chạy, vì chạy lại một lần đọc không tốn gì.

`lease_expires_at = NULL` (hàng ghi trước migration 0012) đọc thành *đã hết hạn*.
Không có `server_default`: một giá trị mặc định sẽ nói rằng những hàng ấy từng có
một lease.

**4. Sổ commit độc lập với transaction của request.**

`SqlAlchemyIdempotencyStore` nhận một `SessionFactory` (mặc định `session_scope`)
thay vì `AsyncSession` của request. Mỗi thao tác mở transaction riêng và commit
ngay. Hai điều được bảo đảm nhờ đó, và cả hai đều có test:

- request hỏng sau khi provider đã làm xong thì sổ vẫn còn
  (`test_request_loi_sau_khi_slack_da_dang_tin_khong_xoa_so`);
- không có transaction nào mở trong lúc chờ provider vài chục giây.

**5. `InvocationInProgress` là một hạng lỗi mới: HTTP 409, retryable, kèm
`retry_after_seconds`.**

Khi một lời gọi khác đang giữ lease còn hạn, người gọi nhận 409 với số giây chính
là phần lease còn lại. Nó nằm trong `RETRYABLE` vì thử lại đúng là việc nên làm:
khi đó thao tác đã xong và kết quả được phát lại. Đây là hạng lỗi đầu tiên mà câu
trả lời đúng là *đợi rồi gọi y hệt lần nữa*.

**6. Thua tranh thì đọc lại và quyết lại, tối đa một lần.**

`take_over` và `abandon_stale` đều là `UPDATE ... WHERE status = <kỳ vọng>
RETURNING`. Thua nghĩa là hàng đã đổi dưới chân mình, nên quyết định vừa tính
không còn đúng với dữ liệu thật. Vòng lặp đọc lại đúng một lần rồi bỏ cuộc bằng
409 — không lặp cho tới khi thắng.

**7. Không thử lại khi lần thử kế tiếp có thể vượt `timeout.total_seconds`.**

`_past_deadline` chặn việc đó. Lease neo vào mốc ấy, nên một Gateway chạy quá hạn
chốt của chính tool mình đang gọi sẽ bị người khác coi là đã chết trong khi nó vẫn
đang sống — đúng kịch bản sinh ra hai lần gọi provider.

**8. Chốt sổ và ghi trace nằm ngoài khối bắt lỗi của lời gọi provider.**

`_succeeded` được gọi sau `try/except`, không phải bên trong. Provider đã làm xong
là một sự thật; một lỗi khi ghi lại sự thật đó không được biến lời gọi thành thất
bại. Mất lease đúng lúc chốt sổ thì ghi log `idempotency_lease_lost` và **vẫn trả
kết quả về** (`test_mat_lease_khi_chot_so_van_tra_ket_qua`).

Cùng lý do đó, `complete` được phép ghi đè một hàng `unknown` **của chính token
mình** (`WHERE claim_token = ? AND status IN ('in_flight', 'unknown')`): provider
trả thành công là sự thật đúng hơn một lần đánh dấu vì lease lỡ hết hạn.

## Hệ quả

### Tích cực

- §2.5 được thoả bằng dữ liệu bền, không chỉ bằng luồng điều khiển trong RAM.
  Thông tin *"lần trước không rõ"* sống qua đời của tiến trình đã tạo ra nó.
- Gateway chết giữa chừng không khoá vĩnh viễn một khoá idempotency nữa: tool đọc
  tiếp quản được, tool ghi chuyển sang `unknown` — một trạng thái người ta đọc
  được và xử lý được, thay vì một hàng `in_flight` treo mãi.
- Phase 4 có nền để dựa vào. Temporal Activity retry gọi lại cùng một khoá là
  kịch bản mặc định của nó, và ba ô `REPLAY` / `IN_PROGRESS` / `UNKNOWN` trả lời
  đủ cả ba tình huống.
- Bảng quyết định là một hàm thuần ở tầng domain, đọc hết trong mười dòng và có
  test bảng riêng (`test_bang_quyet_dinh`).

### Tiêu cực / chi phí phải trả

- **Thêm round-trip tới Postgres.** Đường thường: 1 `claim` + 1 chốt. Đường tranh
  chấp: thêm tối đa 2 `UPDATE` và 1 `SELECT`. Mỗi cái là một connection từ pool,
  không dùng chung với session của request nữa.
- **`IdempotencyStore` rộng ra từ 3 lên 7 phương thức.** Ba cái mới (`get`,
  `take_over`, `abandon_stale`) tồn tại chỉ để phục vụ bảng quyết định.
- **Người gọi phải biết xử lý 409.** Trước đây mọi lời gọi tool hoặc chạy hoặc
  hỏng; giờ có một câu trả lời thứ ba nghĩa là *chưa biết, hỏi lại sau*.
- **Lease dựa vào đồng hồ của nhiều tiến trình.** `LEASE_MARGIN_SECONDS = 30` che
  cho lệch đồng hồ, và con số đó được chốt khi chưa đo.
- **Một hàng `unknown` là một việc còn dang dở mà hệ thống không tự đóng được.**
  Nó đúng theo nghĩa an toàn, nhưng nó đẩy phán đoán cuối cùng sang cho người.

### Rủi ro cần theo dõi

- **Chưa có đường giải quyết hàng `unknown`.** Không UI, không endpoint, không quy
  trình. Domain `approval` vẫn rỗng. Người dùng nhận được câu *"hãy kiểm tra kết
  quả trước"* mà không có chỗ nào để nói *"tôi kiểm rồi, nó chưa chạy"*.
- **Hàng `unknown` biến mất khi hết TTL của sổ** (`idempotency.ttl_seconds`). Sau
  mốc đó, cùng khoá đó lại chạy được như mới. TTL được chọn cho `succeeded`, chưa
  từng được cân nhắc cho `unknown`.
- **`abandon_stale` đánh dấu `unknown` cho một lời gọi có thể chưa từng gửi gì
  đi.** Gateway chết *trước* khi chạm provider cũng để lại hàng `in_flight` hết
  lease, và tool ghi sẽ đọc nó thành `unknown`. Thiên về an toàn, nhưng nó sinh ra
  báo động giả.
- **Tool ghi không đặt khoá idempotency vẫn chạy được** nếu `idempotency.required`
  là `false` trong tài liệu tool. Cả bộ máy này không che chỗ đó;
  `ensure_key_present` mới là chỗ che, và nó tin vào tài liệu.

## Điều kiện xem xét lại

- Hàng `unknown` xuất hiện đủ thường để thành việc vận hành hằng ngày. Khi đó nó
  cần một đường xử lý thật — nhiều khả năng là domain `approval` của Phase 7,
  không phải thêm trạng thái vào sổ.
- Log `idempotency_lease_lost` hoặc `idempotency_taken_over` xuất hiện trong khi
  không có tiến trình nào thật sự chết. Đó là dấu hiệu lease quá ngắn so với thời
  gian thật của tool — chỉnh `LEASE_MARGIN_SECONDS`, hoặc chỉnh
  `timeout.total_seconds` của tool đó.
- 409 `InvocationInProgress` trở nên phổ biến trong một luồng bình thường. Nghĩa
  là cùng một thao tác đang bị gọi song song thật, và chỗ đáng sửa nằm ở người gọi
  chứ không ở sổ.
- Phase 4 cho thấy Temporal Activity retry cần ngữ nghĩa khác với ba ô `REPLAY` /
  `IN_PROGRESS` / `UNKNOWN`. Bảng quyết định là chỗ sửa, và nó là một hàm thuần nên
  sửa được mà không đụng hạ tầng.

## Phương án đã cân nhắc và loại bỏ

**Giữ ba trạng thái, coi `in_flight` hết hạn là `failed`.** Rẻ nhất, và sai đúng
chỗ nguy hiểm nhất: nó cho tool ghi chạy lại trên một thao tác có thể đã thành
công. Đây chính là câu §2.5 cấm, viết lại bằng code.

**Khoá phân tán bằng Redis.** Nhanh hơn, và `RedisRateLimiter` với
`RedisCircuitBreaker` đã có sẵn ở cạnh đó. Nhưng ràng buộc 6 của §47 nói
PostgreSQL là nguồn chân lý, và một khoá Redis bốc hơi khi Redis restart sẽ mở
đường cho đúng lần gọi thứ hai mà mọi thứ ở đây sinh ra để chặn. Trạng thái của
một tác động ra bên ngoài không được sống ở nơi chấp nhận mất dữ liệu.

**`SELECT ... FOR UPDATE` giữ khoá hàng suốt lời gọi provider.** Đúng về mặt
tương tranh và không cần cột nào mới. Đổi lại là một transaction Postgres mở suốt
một lời gọi mạng có thể kéo 60 giây, nhân với số lời gọi tool đồng thời. Lease là
cách nói *"tôi đang giữ chỗ"* mà không giữ tài nguyên của database.

**Lease tự gia hạn bằng heartbeat.** Xoá được nhu cầu đoán `LEASE_MARGIN_SECONDS`
và chịu được tool chạy lâu bất thường. Cái giá là một task nền chạy song song với
mỗi lời gọi tool, và một lớp lỗi mới: task gia hạn chết trong khi lời gọi còn
sống. Giữ làm phương án lùi nếu con số 30 giây tỏ ra không đủ.

**Cho người gọi chờ (block) thay vì trả 409.** Ẩn được tình trạng tranh chấp khỏi
người gọi. Nhưng nó biến một request HTTP thành một hàng đợi, và với Agent Runtime
thì nó giữ luôn một lượt chat đang stream. Trả 409 kèm `retry_after_seconds` nói
đúng sự thật và trả quyền quyết định cho người gọi.

**Vòng lặp đọc-lại-và-quyết-lại cho tới khi thắng.** Đúng hơn về lý thuyết. Nhưng
dưới tranh chấp thật nó là một vòng lặp không có trần, trong một request HTTP. Một
lần đọc lại xử lý được kịch bản thực tế duy nhất đã thấy — hai lời gọi cùng tiếp
quản một hàng đã chết — và 409 là câu trả lời đúng cho phần còn lại.
