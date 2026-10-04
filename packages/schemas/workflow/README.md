# `workflow/` — Workflow IR

Nguồn chân lý cho §11. Kế hoạch và các quyết định:
[`docs/plans/wp-b-workflow-ir-va-generic-executor.md`](../../../docs/plans/wp-b-workflow-ir-va-generic-executor.md),
số liệu thiết kế: [`wp-b-quan-sat-trace.md`](../../../docs/plans/wp-b-quan-sat-trace.md).

```
workflow/
├── ir.v2.schema.json     hình dạng của IR v2 — major hiện hành
├── ir.v1.schema.json     IR v1, đóng băng: version đã publish vẫn phải đọc được
├── build_ir_schema.py    sinh bản Python của schema
└── examples/             sáu kịch bản GĐ 0, viết tay thành IR (kb5 ở 2.1, dùng `transform`)
```

## Sinh code

```bash
python packages/schemas/workflow/build_ir_schema.py           # ghi ra
python packages/schemas/workflow/build_ir_schema.py --check   # chỉ báo lệch
```

Đích: `services/api/app/domains/workflow/domain/generated/ir_v{1,2}.py` — **phải được
commit**. Chỉ sinh *dữ liệu* (chính cái schema); kiểu viết tay ở
`workflow/domain/ir.py`. TypeScript cho editor sinh ở Phase 6.

## Ba tầng kiểm, mỗi tầng một câu hỏi

| Tầng | Ở đâu | Trả lời |
|---|---|---|
| Hình dạng | `ir.v2.schema.json` qua `jsonschema` | Tài liệu có đúng cú pháp không |
| Tài liệu | `workflow/domain/validation.py` | Đồ thị có chạy được không — một trigger, không chu trình, binding trỏ vào node chắc chắn đã chạy trước |
| Catalog | `workflow/application/validation.py` | Có khớp tool thật không — input đủ và đúng kiểu, đường `from_node` có trong output |

Schema **để node loại lạ đi qua** (`unknownNode`): editor và compiler phải mở
được một tài liệu có node mà định dạng này chưa biết. `parse` mới là chỗ từ chối,
với thông báo nói đúng loại nào.

## Sáu node

`trigger` · `tool` · `condition` · `llm` · `loop` · `transform`. `parallel`, `wait`,
`human_approval`, `subworkflow` hoãn — điều kiện mở khoá ở Q2 của plan.

Thân `loop` là một dãy thẳng chỉ gồm `tool`, `llm` và `transform`: không lồng loop,
không rẽ nhánh (Q13).

`transform` — node duy nhất chạy code, trong sandbox — **có từ 2.1**
([WP-C](../../../docs/plans/wp-c-transform-sandbox.md)). `code` là thân của một hàm nhận
`args` và `return` một giá trị JSON; output của node là chính giá trị đó, nên node sau
đọc nó bằng `{"from_node": {"node": "dem", "path": []}}`.

```json
{ "id": "dem", "type": "transform",
  "code": "return len(args['messages'])",
  "args": { "messages": { "from_node": { "node": "lich_su", "path": ["messages"] } } },
  "output_schema": { "type": "integer" } }
```

Một tài liệu khai `2.0` mà chứa `transform` bị `parse` từ chối: trình đọc cũ tin vào
con số đó. Lúc publish, code chỉ được **đọc** — qua vòng duyệt AST, trần 4096 byte,
và một cảnh báo `hang_viet_cung` khi thấy ngưỡng hay chuỗi dài nằm ngay trong code
thay vì đi qua `args`. Executor chưa chạy được node này (commit 4 của WP-C).

## Năm dạng binding

```json
{"const": "C123"}
{"from_input": "ten_kenh"}
{"from_node": {"node": "kenh", "path": ["channels"], "where": {"name": {"from_input": "ten_kenh"}}, "pick": ["id"]}}
{"from_item": ["id"]}
{"from_connection": {"node": "dang", "field": "external_account_label"}}
```

Dữ liệu, không phải ngôn ngữ: không nội suy chuỗi, không số học, không hàm.
Segment toàn chữ số trong `path` là chỉ số mảng — hợp lệ, nhưng validator cảnh
báo `chi_so_tho`, vì thứ tự provider trả về không phải một hằng số.

## Connection

IR không mang connection nào. Mỗi lần chạy chọn connection cho từng node `tool`
([ADR-0014](../../../docs/adr/0014-connection-gan-theo-lan-chay.md));
`from_connection` đọc metadata của connection mà node được trỏ tới dùng ở lần chạy đó.

## Quy tắc version

Như README cha: `schema_version` là version của **định dạng**. Thêm trường tuỳ
chọn là minor; bỏ trường hay đổi nghĩa là major, và major cũ phải còn diễn giải
được (§41).

**v1 → v2** bỏ `connections` và đổi `from_connection` từ `provider` sang `node`.
`parse` vẫn nhận v1 và nâng lên v2 (`upgrade_v1` trong `workflow/domain/ir.py`).
