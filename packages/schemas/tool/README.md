# `tool/` — định nghĩa Tool và Connector

Nguồn chân lý cho §6.1 (Connector) và §6.2 (Tool). Thư mục này là **nơi duy
nhất** một tool được định nghĩa; phía Python là bản sinh ra, không phải bản
song song.

```
tool/
├── tool.schema.json          meta-schema của một tài liệu tool
├── connector.schema.json     meta-schema của một tài liệu connector
├── build_catalog.py          sinh catalog Python
└── slack/
    ├── connector.json
    └── tools/
        ├── list_channels.v1.json
        ├── post_message.v1.json
        ├── list_users.v1.json
        └── get_channel_history.v1.json
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

## Chiếu output, và vì sao nó hẹp

`output_mapping` liệt kê từng trường đi ra và chỗ lấy nó. Phần còn lại của phản
hồi bị bỏ.

Đó là một quyết định bảo mật, không phải tiện lợi: `users.list` của Slack trả
về email, số điện thoại và múi giờ của từng người. Không trường nào trong số đó
được chiếu ra — §33 yêu cầu *"sensitive user data should be minimized"*, và
cách rẻ nhất để tuân thủ là để mặc định là **bỏ**, không phải **cho qua**.
Cùng lập luận khiến `oauth_client._redact` tồn tại.

Hệ quả kèm theo: một trường mới mà provider thêm vào sẽ không tự động rò ra.
