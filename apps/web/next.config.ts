import type { NextConfig } from "next";

/**
 * Backend Control Plane. Chỉ dùng ở phía server của Next, không lộ ra trình
 * duyệt — xem `rewrites` bên dưới.
 */
const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

/**
 * Agent Runtime — tiến trình riêng (`entrypoints/agent.py`, cổng 8001 mặc
 * định), KHÔNG phải Control Plane, dù router của nó cũng dùng tiền tố
 * `/v1/...`. Vì hai tiến trình dùng chung namespace `/v1/`, các route thuộc
 * Agent Runtime phải khớp TRƯỚC rule tổng quát bên dưới (Next khớp rewrite
 * theo thứ tự, rule đầu thắng) — nếu không request sẽ rơi nhầm vào Control
 * Plane.
 */
const AGENT_ORIGIN = process.env.AGENT_ORIGIN ?? "http://localhost:8001";

const nextConfig: NextConfig = {
  /**
   * Trình duyệt chỉ nói chuyện với origin của Next; Next chuyển tiếp sang API.
   *
   * Nhờ vậy không cần CORS: không preflight, không `credentials: "include"`,
   * và cookie refresh hoạt động như một ứng dụng một-origin bình thường.
   *
   * Đường dẫn phải giữ nguyên tiền tố `/v1`. Cookie refresh mang
   * `Path=/v1/auth`, nên nếu proxy đặt dưới tiền tố khác (`/api/v1/auth`),
   * trình duyệt sẽ lưu cookie ở một path không bao giờ khớp và phiên đăng nhập
   * chết ngay sau lần refresh đầu tiên — triệu chứng rất khó đọc.
   */
  async rewrites() {
    return [
      { source: "/v1/agents", destination: `${AGENT_ORIGIN}/v1/agents` },
      { source: "/v1/agents/:path*", destination: `${AGENT_ORIGIN}/v1/agents/:path*` },
      // Ngoại lệ: `Conversation` là tài nguyên của domain `agent`, nhưng
      // Execution Trace của nó do domain `trace` sở hữu và router chỉ-đọc của
      // trace mount trên Control Plane (`GET /v1/conversations/:id/traces`,
      // xem trace/infrastructure/http.py). Rule này phải đứng TRƯỚC rule
      // conversations tổng quát bên dưới, nếu không request rơi nhầm vào Agent
      // Runtime (cổng 8001) — nơi không có route đó.
      {
        source: "/v1/conversations/:conversationId/traces",
        destination: `${API_ORIGIN}/v1/conversations/:conversationId/traces`,
      },
      { source: "/v1/conversations/:path*", destination: `${AGENT_ORIGIN}/v1/conversations/:path*` },
      // Workflow Compiler chạy trong Agent Runtime, cạnh Model Gateway (ADR-0015).
      {
        source: "/v1/compile-sessions/:path*",
        destination: `${AGENT_ORIGIN}/v1/compile-sessions/:path*`,
      },
      { source: "/v1/:path*", destination: `${API_ORIGIN}/v1/:path*` },
    ];
  },
};

export default nextConfig;
