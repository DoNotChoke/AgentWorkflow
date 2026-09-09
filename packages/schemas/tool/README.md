# `tool/` — định nghĩa Tool và Connector

Nguồn chân lý cho §6.1 (Connector) và §6.2 (Tool). Thư mục này là **nơi duy
nhất** một tool được định nghĩa; phía Python là bản sinh ra, không phải bản
song song.

```
tool/
├── tool.schema.json          meta-schema của một tài liệu tool
├── connector.schema.json     meta-schema của một tài liệu connector
├── build_catalog.py          sinh catalog Python
├── slack/
│   ├── connector.json
│   └── tools/
│       ├── list_channels.v1.json
│       ├── post_message.v1.json
│       ├── list_users.v1.json
│       └── get_channel_history.v1.json
└── notion/
    ├── connector.json
    └── tools/
        ├── search.v1.json
        ├── get_page.v1.json
        ├── get_block_children.v1.json
        ├── query_database.v1.json
        ├── list_users.v1.json
        ├── create_page.v2.json
        ├── append_blocks.v2.json
        └── create_comment.v1.json
```

Tên file mang cả tool lẫn major: `post_message.v1.json`. §41 cho phép hai major
cùng tồn tại, nên `post_message.v2.json` nằm cạnh `v1` chứ không thay thế nó.

## Sinh code

```bash
python packages/schemas/tool/build_catalog.py           # ghi ra
python packages/schemas/tool/build_catalog.py --check   # chỉ báo lệch
```

Đích: `services/api/app/domains/integration/domain/generated/catalog_v1.py`.
File đó **phải được commit** — đúng quy tắc ở README cha.

### Vì sao sinh *dữ liệu* chứ không sinh *kiểu*

`datamodel-codegen` sinh kiểu cho một định dạng tài liệu. Nó không sinh được
bốn tool Slack thành Python, mà đó mới là thứ không viết tay được.

Không sinh dữ liệu thì phải đọc JSON lúc chạy, và đường đó gãy:
`infra/docker/api.Dockerfile` chép `services/api → /app` và
`packages → /app/packages`, nên trong container `packages/` nằm ở `../packages`
so với package `app`, còn khi dev thì ở `../../../packages`. **Độ sâu khác
nhau** — mọi hằng `Path(__file__).parents[N]` đều sai ở một trong hai môi
trường, và sai theo kiểu chỉ lộ ra lúc deploy.

Kiểu thì đã có sẵn ở `integration/domain/{tools,connectors}.py`, viết bằng
dataclass đúng phong cách repo, và `mypy --strict` canh chúng.

## Ba trục version, độc lập nhau

| Trường | Đánh version cho | §40 |
|---|---|---|
| `schema_version` | **định dạng tài liệu** — chính hai meta-schema | ✔ |
| `version` của tool | định nghĩa tool | ✔ |
| `version` của connector | định nghĩa connector | ✔ |
| `input_schema.version` · `output_schema.version` | contract của payload | ✔ |

Đổi `base_url` của Slack là chuyện của connector và không đụng tới version của
tool nào. Thêm một trường tuỳ chọn vào output là minor bump của
`output_schema`. Bỏ một trường là major bump của tool, và bản major cũ **ở lại**
cho tới hết thời hạn tương thích đã công bố (§41).

## Những ràng buộc được test canh

`services/api/tests/test_tool_schema.py`:

- mọi tài liệu hợp lệ theo meta-schema của nó;
- `input_schema.json_schema` và `output_schema.json_schema` **tự chúng** là
  JSON Schema đúng — meta-schema chỉ nói chúng là object, nên một
  `"type": "strng"` gõ sai sẽ lọt qua nếu không có bước này;
- tên file khớp `tool_id` và major;
- `output_mapping` phủ đúng tập trường của `output_schema`, không thừa không thiếu;
- tool có `side_effect` là `write`/`destructive` **bắt buộc** có chiến lược
  idempotency — §2.5 và §15.1, chặn ở tầng tài liệu chứ không phải ở code review;
- `enum` trong meta-schema khớp từng phần tử với `StrEnum` phía Python.

`services/api/tests/test_integration_registry.py`:

- catalog đã commit tương đương ngữ nghĩa với JSON (chống trôi);
- **mọi `required_scopes` nằm trong `default_scopes` của provider.** Contract 1
  cấm `integration` import `connection`, nên không code nào trong ứng dụng nhìn
  được cả hai catalog; thư mục `tests/` nằm ngoài `root_packages` của
  import-linter nên nó nhìn được, và nó phải nhìn. Không có test này thì một
  tool khai scope mà vòng OAuth không bao giờ xin sẽ chỉ lộ ra lúc người dùng
  thật bấm nút.

## Chỗ điền trong `request.path`

`"/blocks/{block_id}/children"` — `{block_id}` là chỗ người gọi điền. Quyết định
và lập luận đầy đủ ở [ADR-0005](../../../docs/adr/0005-tham-so-duong-dan-trong-tai-lieu-tool.md);
phần cần nhớ khi viết tài liệu:

- tên trong ngoặc phải là một property `required`, kiểu chuỗi, **có `pattern`
  hẹp** trong `input_schema`;
- pattern ấy phải từ chối `..`, `a/b`, `a?b`, `a#b`, `%2F`, `http://…` — một
  test đọc tài liệu và thử từng chuỗi đó;
- lúc gọi, giá trị được percent-encode toàn bộ (`/` thành `%2F`) và khoá ấy bị
  loại khỏi query string hay thân request.

## Dựng thân request: `input_mapping`

Hình dạng mà provider đòi và hình dạng mà một LLM viết đúng được là hai thứ
khác nhau. Tạo một trang Notion đòi bốn tầng lồng nhau:

```json
{"parent": {"page_id": "…"},
 "properties": {"title": {"title": [{"text": {"content": "…"}}]}}}
```

Bỏ trống `input_mapping` thì input được gửi thẳng — đủ cho Slack, nơi thân
request phẳng. Khai nó thì `input_schema` giữ được ba trường phẳng mà LLM viết
đúng được, còn cái cây kia nằm đúng chỗ của nó là tài liệu connector.

Bốn luật, hết:

- chuỗi **đúng bằng** `{{ten}}` được thay bằng giá trị của `ten`, giữ nguyên
  kiểu — số vẫn là số. Không nội suy giữa chuỗi;
- tên có thể là một đường: `{{item.type}}` đọc trường `type` của phần tử đang
  lặp. Lạc đường thì coi như vắng mặt;
- `{"for_each": "ten", "template": …}` lặp template trên từng phần tử của mảng
  `ten`, với `{{item}}` là phần tử hiện tại. Một cấp, không lồng nhau — cùng
  giới hạn với `output_mapping.items`;
- placeholder đứng được ở **vị trí khoá**, và còn lại là hằng.

Không có điều kiện, và bốn luật trên không cần tới nó.

Khoá không có giá trị **biến mất** khỏi thân, không thành `null`: `null` nói
với provider "đặt nó rỗng", vắng mặt nói "tôi không nhắc tới nó".

### Khoá động, và vì sao nó thay được một đống nhánh

Vài provider đặt **tên khoá theo chính dữ liệu**. Một khối Notion để nội dung
dưới khoá trùng với `type` của nó:

```json
{"object": "block", "type": "bulleted_list_item",
 "bulleted_list_item": {"rich_text": [{"text": {"content": "…"}}]}}
```

Hình dạng ấy đồng nhất cho mọi loại khối, nên **một** template phủ hết — đoạn
văn, tiêu đề, gạch đầu dòng, danh sách đánh số, việc cần làm, toggle:

```json
{"for_each": "blocks", "template": {
   "object": "block",
   "type": "{{item.type}}",
   "{{item.type}}": {
     "rich_text": [{"text": {"content": "{{item.text}}"}}],
     "checked": "{{item.checked}}"
   }}}
```

`checked` chỉ hiện ở khối `to_do`, nhờ đúng luật "khoá không có giá trị thì
biến mất". Không nhánh nào cả — đây là tra cứu, không phải điều kiện.

Khoá động là thứ duy nhất trong template đổi được **hình dạng** thân request
chứ không chỉ nội dung, nên nó có ba hàng rào:

- lúc chạy, khoá phải là **chuỗi không rỗng**;
- lúc chạy, khoá động **không ghi đè** khoá hằng nào — nếu không, một `type`
  mang giá trị `"object"` sẽ thay được hằng `"object": "block"` mà tài liệu đã
  khai;
- lúc nạp tài liệu, trường dùng làm khoá động **phải khai `enum`**, để tập khoá
  người gọi tạo ra được là một danh sách đóng, đọc bằng mắt ngay trong tài liệu.
  Cùng vai trò với lớp tài liệu của ADR-0005, và `test_khoa_dong_chi_dung_truong_da_bi_gioi_han_bang_enum`
  canh nó.

`test_input_mapping_phu_dung_cac_truong_cua_input_schema` đối chiếu hai chiều —
một `{{ten}}` không có trong schema là chỗ không bao giờ được điền, một property
không template nào đọc tới là tham số LLM gửi đi rồi rơi vào hư không. Trường
nằm trong `request.path` được trừ ra vì nó đã đi vào URL.

## Chiếu output, và vì sao nó hẹp

`output_mapping` liệt kê từng trường đi ra và chỗ lấy nó. Phần còn lại của phản
hồi bị bỏ.

Đó là một quyết định bảo mật, không phải tiện lợi: `users.list` của Slack trả
về email, số điện thoại và múi giờ của từng người. Không trường nào trong số đó
được chiếu ra — §33 yêu cầu *"sensitive user data should be minimized"*, và
cách rẻ nhất để tuân thủ là để mặc định là **bỏ**, không phải **cho qua**.
Cùng lập luận khiến `oauth_client._redact` tồn tại.

Hệ quả kèm theo: một trường mới mà provider thêm vào sẽ không tự động rò ra.

### Hai thứ mà một đường tĩnh không tới được

`*` trong một `path` nhận mọi phần tử của mảng và chiếu tiếp phần đuôi lên từng
cái. Tiêu đề một trang Notion bị cắt thành nhiều đoạn theo định dạng chữ, nên
`["properties", "title", "title", "*", "plain_text"]` cho ra
`["Ghi chú ", "tuần này"]` — lấy đoạn đầu là mất phần còn lại, lấy nguyên mảng
là trả về cả khối annotation và màu.

`any_of` bên trong `items` khai nhiều đường và lấy đường đầu tiên tới nơi. Cần
nó cho union có discriminator: Notion đặt nội dung của một khối dưới khoá
**trùng với `type` của chính khối đó** (`paragraph.rich_text`,
`heading_1.rich_text`, …), nên không đường tĩnh nào tới được, và cũng không
đường nào đúng cho mọi khối.

```json
"text": {
  "any_of": [
    ["paragraph", "rich_text", "*", "plain_text"],
    ["heading_1", "rich_text", "*", "plain_text"]
  ]
}
```

Không đường nào tới nơi thì trường ấy **vắng mặt** khỏi output, không phải rỗng
— một `divider` không mang chữ, và nói thế đúng hơn là nói nó mang chuỗi rỗng.
