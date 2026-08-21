# apps/web — frontend

Chưa được scaffold ở Phase 0. Cố tình.

`create-next-app` sinh ra hàng trăm file, và việc tạo chúng ở Phase 0 sẽ làm
loãng phần thực sự cần review của phase này (ranh giới backend, hạ tầng, ADR).
Frontend chưa có gì để gọi cho tới Phase 1.

## Khi bắt đầu Phase 1

```bash
cd apps
npx create-next-app@latest web --typescript --app --eslint --tailwind --src-dir
```

Sau đó thêm:

```bash
cd web
npm install @xyflow/react        # workflow editor (§4.1) — cần từ Phase 6
```

## Trách nhiệm (§4.1)

- Agent chat UI
- quản lý connection
- workflow editor (React Flow)
- workflow execution viewer
- execution trace viewer
- quản lý schedule
- quản trị tenant/workspace
- trang billing và usage
- luồng phê duyệt của con người
- luồng thiết lập provider

## Hai ràng buộc cần nhớ

**SSE trước, WebSocket sau.** Tiến trình execution là luồng một chiều từ server
về client, SSE là đủ (§4.1, §25). Chỉ dùng WebSocket ở chỗ thực sự cần hai
chiều. Client theo dõi execution qua Execution Service, **không** truy vấn
worker trực tiếp (§25).

**Ẩn nút không phải là phân quyền.** Mọi quyết định phân quyền phải được thực
thi ở server (§20, §47.16). Việc frontend ẩn một chức năng chỉ là trải nghiệm
người dùng, không phải cơ chế bảo mật.
