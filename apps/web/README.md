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

---

## Chạy

```bash
npm install                      # trong apps/web
npm run dev                      # :3000
```

Frontend gọi API qua chính origin của nó: `next.config.ts` chuyển tiếp
`/v1/*` sang backend. Nhờ vậy không cần CORS, và cookie refresh hoạt động
như một ứng dụng một-origin.

Backend chạy ở cổng khác thì đặt biến môi trường:

```bash
API_ORIGIN=http://localhost:8010 npm run dev
```

**Đường dẫn proxy phải giữ nguyên tiền tố `/v1`.** Cookie refresh mang
`Path=/v1/auth`; đặt proxy dưới tiền tố khác thì trình duyệt lưu cookie ở
một path không bao giờ khớp, và phiên chết ngay sau lần refresh đầu tiên.
