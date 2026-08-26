# ADR 0002 — Ranh giới credential nằm ở `entrypoints/wiring`, không ở Integration Plane

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** TuanNghia (solo developer)
- **Liên quan:** `AgentWorkflow_SYSTEM_DESIGN.md` §2.4, §6.3, §21, §30; ADR-0001 mục
  "Rủi ro cần theo dõi"

## Bối cảnh

ADR-0001 gộp Control Plane và AI Plane vào cùng một tiến trình, và ghi rằng ranh giới
mạng bị xoá phải được thay bằng ranh giới module:

> Một contract import-linter (`forbidden`) cấm mọi module ngoài
> `domains.integration.infrastructure` import credential store hoặc module crypto.

Contract 4 trong `pyproject.toml` được viết theo đúng câu đó: nó liệt kê 11 domain vào
`source_modules` và cố ý **bỏ `integration` ra ngoài**, để Integration Gateway có thể
import `connection.infrastructure.credential_store`.

Khi bắt đầu Phase 2, ngoại lệ đó hoá ra không dùng được.

**Contract 1 (`independence`) cấm mọi cặp domain import nhau, theo cả hai chiều.**
`app.domains.integration` và `app.domains.connection` đều nằm trong danh sách của nó. Vì
vậy `integration.infrastructure` **không thể** import `credential_store` — không phải vì
contract 4 cấm, mà vì contract 1 đã cấm trước đó.

Ngoại lệ mà contract 4 cấp là một ngoại lệ chết. Nó chưa từng được dùng, và không thể
được dùng.

Hệ quả nghiêm trọng hơn: **contract 4 không canh gì ở phía Integration Plane.** Nó liệt
kê những domain vốn dĩ đã bị contract 1 chặn, còn module thật sự chạm được credential thì
nằm ngoài danh sách.

Module đó là `app.entrypoints.wiring`. `app.entrypoints` không có mặt trong
`source_modules` của contract 4, và điều đó là cần thiết: `wiring` là nơi duy nhất biết cả
hai domain, nên nó là nơi duy nhất dựng được `CredentialResolver`. Đây chính là hình mà
ADR-0001 đã chọn cho `TenantGuard` và `UserDirectory`; credential đi theo cùng con đường.

Nói cách khác: ranh giới credential luôn nằm ở `wiring`, chỉ là ADR-0001 chưa nhìn ra.

## Quyết định

**1. Ranh giới credential là `app.entrypoints.wiring`.** Đó là module duy nhất trong hệ
thống dựng được `CredentialResolver`, và là nơi adapter `CredentialProvider` của
Integration Plane được nối vào.

**2. `app.domains.integration` bị cấm chạm credential, như mọi domain khác.** Cả ba tầng
được thêm vào `source_modules` của contract 4. Integration Gateway nhận token **qua tham
số**, không qua `import`. Contract 4 được đổi tên thành *"Credential không rò ra ngoài
domain Connection"* cho khớp sự thật: danh sách giờ là mọi domain trừ `connection`, và
`connection` vắng mặt vì nó sở hữu credential store.

**3. Thêm contract 6 — hàng rào quanh Agent Runtime.**

```toml
[[tool.importlinter.contracts]]
name = "Agent Runtime không có đường nào tới credential"
type = "forbidden"
source_modules = ["app.entrypoints.agent"]
forbidden_modules = [
    "app.core.crypto",
    "app.domains.connection.infrastructure.credential_store",
    "app.domains.connection.application.service",
    "app.entrypoints.wiring",
]
```

Contract 4 canh các *domain*; contract 6 canh *tiến trình*. `allow_indirect_imports` cố ý
để mặc định (`false`), nên nó bắt cả chuỗi gián tiếp — `agent → wiring → credential_store`
là đúng cái sai mà Phase 3 dễ mắc nhất.

## Hệ quả

### Tích cực

- §2.4 có một cơ chế bảo vệ **thật sự chạy**, thay cho một ngoại lệ chết.
- Ranh giới hẹp hơn ADR-0001 đề xuất: chỉ `connection.infrastructure` và
  `entrypoints.wiring` chạm được credential, thay vì cả một tầng
  `integration.infrastructure`.
- **Agent Runtime buộc phải gọi Integration Gateway qua HTTP**, không gọi trong tiến
  trình. Đây là ràng buộc kiến trúc có ích tự thân: nó giữ đúng §2.4 và làm cho việc tách
  Agent Runtime thành service riêng về sau chỉ là chuyện triển khai, không phải viết lại.
- `wiring` được phép import thẳng và có kiểu đầy đủ: `CredentialResolver` nằm ở tầng
  `application` của `connection`, mà contract 5 chỉ cấm entrypoint import tầng `domain`.
  Không cần `# type: ignore[attr-defined]` như đường `IdentityUserDirectory` phải dùng.

### Tiêu cực / chi phí phải trả

- Integration Plane phải khai lại một `ProviderCredential` và một `CredentialProvider`
  Protocol trùng hình với `ResolvedCredential` / `CredentialResolver.resolve`. Hai kiểu
  cùng hình dạng ở hai chỗ. Đây là cái giá cố định của contract 1, và đã trả cùng cái giá
  đó cho `TenantGuard`.
- Endpoint invoke nội bộ trở thành thành phần **bắt buộc**, không phải tiện ích để thử.
  Nó phải tồn tại trước Phase 3.
- Mỗi lời gọi tool từ Agent Runtime đi qua một chặng HTTP nội bộ. Ở quy mô hiện tại chi
  phí này không đáng kể; xem "Điều kiện xem xét lại".

### Rủi ro cần theo dõi

- **`wiring.py` phình ra.** Nó đang là nơi nối 4 cặp domain và giờ giữ cả đường
  credential. Nếu nó vượt quá mức đọc được trong một lần, tách thành `wiring/` với một
  module cho mỗi cặp — **không** phải bằng cách chuyển adapter vào trong domain, vì làm
  vậy là phá contract 1.
- **Ai đó nới contract 6 để "cho tiện".** Một lần thêm `allow_indirect_imports = true` vào
  contract này là đủ để mở lại chính lỗ hổng nó vá. Contract 5 có nới, và ghi rõ lý do tại
  chỗ; đừng chép lý do đó sang đây.
- **Contract xanh mà không cắn.** Contract 6 hiện qua vì `entrypoints/agent.py` chưa import
  gì liên quan. Nó đã được kiểm chứng bằng cách cố ý thêm một import bị cấm và xác nhận
  contract gãy, cho cả import trực tiếp lẫn chuỗi ba bước
  `agent → connection.infrastructure.http → credential_store → core.crypto`. Lặp lại phép
  thử đó nếu có ai sửa danh sách module.

## Điều kiện xem xét lại

- Chặng HTTP nội bộ giữa Agent Runtime và Integration Gateway chiếm phần đáng kể trong p99
  của một lượt agent, **và** đã đo được rằng phần đó là overhead vận chuyển chứ không phải
  thời gian chờ provider. Khi đó phương án là tách tiến trình thật (lúc ấy chặng mạng dù
  sao cũng phải có), không phải gộp lại để gọi trong tiến trình.
- Integration Plane phát sinh nhu cầu ghi credential (ví dụ tự cấp lại token theo giao
  thức mà `connection` không mô hình hoá được). Khi đó phải mở rộng `CredentialProvider`
  Protocol, **không** phải cho `integration` import credential store.
- Có người thứ hai tham gia toàn thời gian, và ranh giới sở hữu độc lập trở nên có giá trị
  thật — tín hiệu này đã có trong ADR-0001 và áp dụng như nhau ở đây.

## Phương án đã cân nhắc và loại bỏ

**Giữ nguyên contract 4 như ADR-0001 mô tả.** Loại bỏ vì ngoại lệ đó không dùng được, và
để nguyên thì contract mang tính bảo mật nhất của repo lại không canh đúng module cần canh
— một hàng rào trông như đang đứng nhưng không chắn gì.

**Bỏ `integration` khỏi contract 1 để nó import thẳng `connection`.** Loại bỏ vì contract 1
là §18 và là điều kiện để tách service về sau. Đổi một ranh giới kiến trúc lấy chút tiện
lợi trong wiring là cái giá sai.

**Cho Agent Runtime gọi Gateway trong tiến trình, chỉ dựa vào quy ước code review.** Loại
bỏ vì đây đúng là thứ mà ADR-0001 đã kết luận là không đủ: khi ranh giới mạng biến mất,
thứ thay thế phải chạy được trong CI, không phải là một điều khoản trong tài liệu.
