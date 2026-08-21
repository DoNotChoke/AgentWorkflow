import type { NextConfig } from "next";

/**
 * Backend Control Plane. Chỉ dùng ở phía server của Next, không lộ ra trình
 * duyệt — xem `rewrites` bên dưới.
 */
const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

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
    return [{ source: "/v1/:path*", destination: `${API_ORIGIN}/v1/:path*` }];
  },
};

export default nextConfig;
