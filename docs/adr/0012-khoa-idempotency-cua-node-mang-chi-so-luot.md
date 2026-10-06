# ADR 0012 — Khoá idempotency của node mang cả chỉ số lượt của vòng lặp

- **Status:** Accepted
- **Date:** 2026-10-06
- **Deciders:** TuanNghia
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §14, §23; ADR-0007, ADR-0010; Q10 và Q13
  của `docs/plans/wp-b-workflow-ir-va-generic-executor.md`

## Bối cảnh

Temporal có thể chạy lại một activity: worker chết giữa chừng, timeout, lỗi tạm thời. Với
một tool ghi ra ngoài, chạy lại là đăng tin lần hai. Sổ idempotency của Gateway (ADR-0007)
chặn điều đó khi hai lời gọi mang cùng một khoá.

Khoá vì vậy phải **giống nhau** qua mọi lần thử lại của cùng một việc, và **khác nhau**
giữa hai việc khác nhau. Trong thân một vòng lặp, cùng một node chạy nhiều lượt: nếu khoá
chỉ gồm lần chạy và id node, lượt 2 của `post_message` nhận lại kết quả phát lại của lượt
1 thay vì đăng tin, và sổ báo `REPLAY` hoàn toàn đúng luật.

## Quyết định

**Khoá là `idm_{execution_id}:{node_path}:{tool_id}`**, sinh bởi
`core/ids.idempotency_key`.

- `node_path` là `NodePath` ở dạng chuỗi: `dang` ở tầng ngoài, `moi_cau[2].dang` ở lượt 2
  (đếm từ 0) của vòng lặp `moi_cau`.
- Khoá được tính trong workflow code, từ những thứ tất định, nên giống nhau qua mọi lần
  Temporal thử lại activity. Nó **không** chứa số lần thử.
- Chỉ tool ghi ra ngoài mới mang khoá. Tool đọc không để lại hàng nào trong sổ.
- Định dạng này đóng băng từ hàng đầu tiên vào sổ: đổi nó là làm mọi lần chạy đang dở
  mất khả năng nhận ra việc mình đã làm.

Khi sổ không biết một lời gọi đã tới provider hay chưa (`OutcomeUnknown`), activity
**không** được thử lại: lỗi đó đi sang Temporal với `non_retryable`. Lần chạy dừng ở
node đó, và người dùng quyết có chạy lại hay không. Thà dừng còn hơn đăng trùng.

Khi một lời gọi khác đang giữ cùng khoá (`InvocationInProgress`), activity đợi đúng
`retry_after_seconds` Gateway trả về rồi gọi lại y hệt.

## Hệ quả

### Tích cực

- Thử lại một activity không bao giờ đăng tin lần hai.
- Ba lượt của một vòng lặp là ba hàng sổ khác nhau, không phải một hàng và hai lần phát
  lại.

### Tiêu cực / chi phí phải trả

- Khoá gắn với `execution_id`: **chạy lại cả workflow là một lần chạy mới**, với khoá
  mới, và sẽ làm lại mọi việc đã xong ở lần trước. Idempotency ở đây là trong một lần
  chạy, không phải giữa các lần chạy.
- Một sự cố giữa lúc gọi provider để lại lần chạy ở trạng thái hỏng mà tin có thể đã lên.

### Rủi ro cần theo dõi

- Đổi cấu trúc thân vòng lặp giữa hai version không ảnh hưởng (mỗi lần chạy ghim một
  version), nhưng đổi định dạng `NodePath` thì có.

## Điều kiện xem xét lại

- Cần chạy tiếp một lần chạy hỏng từ node đã dừng: khi đó khoá phải sống qua thao tác
  *chạy tiếp*, không chỉ qua thử lại của Temporal.
- Có vòng lặp lồng nhau hoặc nhánh song song: `NodePath` phải mang thêm cấp.

## Phương án đã cân nhắc và loại bỏ

1. **Khoá gồm lần chạy và id node.** Sai với mọi node trong thân vòng lặp.
2. **Khoá mang số lần thử.** Mỗi lần thử một khoá thì sổ không còn chặn được gì.
3. **Băm nội dung input làm khoá.** Hai lượt gửi cùng một nội dung (một lời nhắc lặp
   lại) sẽ bị coi là một.
4. **Tự gọi lại khi `OutcomeUnknown`.** Đó chính là thứ ADR-0007 sinh ra để chặn.
