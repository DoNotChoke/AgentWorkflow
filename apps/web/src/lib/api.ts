/**
 * Client gọi Control Plane API.
 *
 * Mọi lời gọi đi qua origin của chính Next rồi được `rewrites` chuyển tiếp
 * (xem `next.config.ts`), nên đây là request cùng origin: cookie tự động đi
 * kèm, không cần `credentials: "include"`, không đụng tới CORS.
 */

/** Nhóm lỗi backend trả về ở `error.category` (§37). */
export type ErrorCategory =
  | "AuthenticationError"
  | "AuthorizationError"
  | "ConnectionExpired"
  | "MissingScope"
  | "ValidationError"
  | "RateLimited"
  | "ProviderUnavailable"
  | "ProviderPermanentError"
  | "Timeout"
  | "RetryExhausted"
  | "ApprovalRejected"
  | "WorkflowDefinitionError"
  | "WorkflowExecutionError"
  | "InternalError";

type ErrorBody = {
  error?: {
    category?: ErrorCategory;
    message?: string;
    details?: Record<string, unknown>;
    retry_after_seconds?: number;
  };
};

export class ApiError extends Error {
  readonly status: number;
  readonly category: ErrorCategory;
  readonly details: Record<string, unknown>;
  readonly retryAfterSeconds?: number;

  constructor(status: number, body: ErrorBody) {
    // Backend luôn kèm thông điệp đọc được; chuỗi dự phòng chỉ dùng khi
    // response không phải hình dạng lỗi mà ta biết (ví dụ proxy chen vào).
    super(body.error?.message ?? `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.category = body.error?.category ?? "InternalError";
    this.details = body.error?.details ?? {};
    this.retryAfterSeconds = body.error?.retry_after_seconds;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }
}

type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  /** Access token ngắn hạn. Không có thì request đi ẩn danh. */
  accessToken?: string | null;
  /** Header phụ, chủ yếu là `x-organization-id` / `x-workspace-id` chọn tenant. */
  headers?: Record<string, string>;
  signal?: AbortSignal;
};

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, accessToken, headers: extraHeaders, signal } = options;

  const headers: Record<string, string> = { ...extraHeaders };
  if (body !== undefined) headers["content-type"] = "application/json";
  if (accessToken) headers.authorization = `Bearer ${accessToken}`;

  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (response.status === 204) return undefined as T;

  const text = await response.text();
  const payload: unknown = text ? JSON.parse(text) : {};

  if (!response.ok) throw new ApiError(response.status, payload as ErrorBody);
  return payload as T;
}

// ─── Hình dạng dữ liệu, khớp với response model của backend ─────────────────

export type User = {
  id: string;
  email: string;
  display_name: string;
  status: string;
  email_verified: boolean;
  created_at: string;
};

export type Session = {
  user: User;
  access_token: string;
  token_type: string;
  access_token_expires_at: string;
  refresh_token_expires_at: string;
};

export type Organization = {
  id: string;
  name: string;
  slug: string;
  role: "owner" | "admin" | "member";
  created_at: string;
  /** Chưa xong thì người dùng phải đi qua wizard thiết lập trước. */
  onboarded: boolean;
};

export type Workspace = {
  id: string;
  organization_id: string;
  name: string;
  slug: string;
  created_at: string;
};

export type Member = {
  user_id: string;
  role: "owner" | "admin" | "member";
  created_at: string;
};

export type SlugAvailability = { slug: string; available: boolean };
