# ADR 0005 — Tham số đường dẫn trong tài liệu tool, có kiểm soát

- **Status:** Accepted
- **Date:** 2026-09-08
- **Deciders:** ai
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §6.2, §38

## Bối cảnh

`ProviderRequest.path` cho tới nay là một chuỗi hằng. Docstring của
`HttpConnector._build` nói thẳng lý do:

> Địa chỉ đích đến từ tài liệu đã commit, không từ input người gọi. Đó là ranh
> giới ngăn tool trở thành cổng SSRF (§38): không tham số nào của người gọi lọt
> được vào URL.

Slack sống được với ràng buộc đó vì mọi endpoint của họ là một đường dẫn cố
định và mọi định danh đi trong thân request: `/chat.postMessage` nhận `channel`
trong body.

Notion thì không. Định danh nằm trong đường dẫn ở hầu hết endpoint đọc dữ liệu:

```
GET  /v1/pages/{page_id}
GET  /v1/blocks/{block_id}/children
POST /v1/databases/{database_id}/query
```

Không có ba endpoint đó thì connector Notion chỉ liệt kê được id chứ không đọc
được gì — tức là không dùng được. Và Notion không phải ngoại lệ: REST theo lối
tài nguyên là quy ước phổ biến hơn hẳn lối RPC của Slack, nên mọi provider sau
này gần như chắc chắn cũng cần điều này.

Ba đường đi đã cân nhắc:

1. Giữ nguyên ràng buộc, chỉ nhận provider có endpoint tĩnh.
2. Cho `path` nhận placeholder, thay bằng input của người gọi.
3. Khai một `path_template` riêng cùng một bảng ánh xạ tham số tường minh.

## Quyết định

Chọn (2), kèm hai lớp chặn.

`request.path` được phép chứa placeholder dạng `{ten_truong}`. Tên trong ngoặc
phải là một property của `input_schema`. Lúc gọi, driver thay từng placeholder
bằng giá trị tương ứng **đã percent-encode với `quote(value, safe="")`**, và
loại những khoá đó khỏi query string hay thân request.

Hai lớp chặn, và chúng chặn hai thứ khác nhau:

**Lớp một — lúc chạy, là ranh giới bảo mật thật.** `safe=""` mã hoá *mọi* ký tự
không phải chữ-số, `/` thành `%2F` và `.` giữ nguyên nhưng không còn ghép được
thành `..` vượt cấp vì không có `/` nào để vượt. Giá trị của người gọi vì thế
không thoát được khỏi đúng một segment: host đến từ `base_url` của connector,
khung đường dẫn đến từ tài liệu đã commit, và không giá trị nào đổi được hai
thứ đó. Đây là điều §38 thực sự đòi hỏi.

**Lớp hai — lúc nạp tài liệu, là ràng buộc thiết kế.** Mỗi placeholder phải ứng
với một property `required`, `type: "string"`, có `pattern`, và pattern ấy phải
từ chối một tập chuỗi thù địch cố định (`../x`, `a/b`, `a?b`, `a#b`, `%2F`,
`http://…`). Test đọc tài liệu và kiểm điều đó, nên một tool khai
`{page_id}` với pattern `^.*$` không nạp được.

Lớp hai không phải thứ giữ cho hệ thống an toàn — lớp một mới là. Nó tồn tại vì
một placeholder không có pattern hẹp cũng là một tool tồi: LLM sẽ gửi tên trang
thay vì id, và lỗi hiện ra ở phía provider thay vì ở tầng validate của mình.

## Hệ quả

### Tích cực

- Connector Notion đọc được trang, khối và database — phần lớn giá trị của nó.
- Mọi provider REST theo tài nguyên dùng được, không cần ADR thứ hai.
- Ranh giới SSRF được phát biểu lại thành một câu kiểm chứng được — "người gọi
  đổi được nội dung của đúng một segment, không đổi được cấu trúc" — thay vì
  "người gọi không chạm vào URL", vốn là một câu mà hệ thống sắp không giữ nổi.

### Tiêu cực / chi phí phải trả

- Docstring `_build` và §38 phải được đọc lại theo nghĩa mới. Câu cũ ngắn hơn và
  dễ kiểm hơn.
- Thêm một chỗ để sai: quên `quote`, hay `quote` với `safe="/"`, đều mở lại
  đúng lỗ vừa đóng. Một test đẩy `../../` qua tham số và soi URL cuối cùng là
  thứ giữ điều này, không phải code review.
- `path_params` là trạng thái suy ra từ `path`. Suy một lần lúc nạp tài liệu và
  cất vào `ProviderRequest` thay vì parse mỗi lời gọi; đổi lại, hai trường phải
  luôn khớp nhau.

### Rủi ro cần theo dõi

- Provider chuẩn hoá `%2F` ngược lại thành `/` trước khi định tuyến. Không phải
  lý thuyết suông — vài web server có tuỳ chọn ấy. Rủi ro còn lại vẫn hẹp: đích
  đến vẫn nằm trong `base_url`, kẻ gọi cùng lắm chạm được một endpoint khác của
  **cùng** provider mà kết nối này vốn đã có quyền.
- Cám dỗ nới lỏng lớp hai cho một tool "chỉ lần này thôi".

## Điều kiện xem xét lại

- Một provider cần định danh ở phần **host** chứ không phải path
  (`https://{workspace}.example.com/api`). Đó là một quyết định khác hẳn — nó
  đụng tới `base_url` — và phải có ADR riêng, không được nong ADR này ra.
- Một tool cần placeholder không phải chuỗi hẹp (đường dẫn nhiều segment, hay
  giá trị người dùng tự nhập). Lúc đó (3) — bảng ánh xạ tường minh — quay lại
  bàn.

## Phương án đã cân nhắc và loại bỏ

**(1) Giữ ràng buộc cũ, chỉ nhận provider có endpoint tĩnh.** Rẻ nhất và an
toàn nhất, nhưng nó không phải một quyết định kiến trúc mà là một giới hạn về
provider được hỗ trợ. Nó loại Notion, và loại phần lớn REST API còn lại.

**(3) `path_template` riêng kèm bảng ánh xạ tham số.**
`{"path_template": "/pages/{id}", "path_params": {"id": "page_id"}}` cho phép
tên trong URL khác tên trong input. Bảng ánh xạ ấy là một mức gián tiếp mà chưa
tool nào cần: bắt tên placeholder **là** tên property giữ tài liệu đọc thẳng, và
biến "placeholder không có property tương ứng" thành một lỗi nạp catalog thay vì
một ô trống trong bảng. Quay lại phương án này khi có tool thật cần tên khác
nhau.
