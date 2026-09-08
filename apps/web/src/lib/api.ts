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
  | "MissingConnection"
  | "ConnectionChoiceRequired"
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

/** Vai trò trong organization, xếp từ nhiều quyền nhất xuống ít nhất. */
export type OrgRole = "owner" | "admin" | "member";

export type Organization = {
  id: string;
  name: string;
  slug: string;
  role: OrgRole;
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

export type WorkspaceRole = "admin" | "editor" | "viewer";

export type WorkspaceAccess = {
  workspace_id: string;
  name: string;
  slug: string;
  role: WorkspaceRole;
};

export type Member = {
  user_id: string;
  role: OrgRole;
  created_at: string;
  /** Trống khi membership trỏ tới một tài khoản không còn tra được. */
  email: string | null;
  display_name: string | null;
  /**
   * Chỉ quyền được cấp tường minh, và chỉ trên workspace người gọi thấy.
   *
   * Rỗng với owner và admin: họ với tới mọi workspace nhờ vai trò organization
   * chứ không qua bản ghi nào. Giao diện suy ra "All workspaces" từ `role`.
   */
  workspaces: WorkspaceAccess[];
};

export type WorkspaceMember = {
  user_id: string;
  role: WorkspaceRole;
  created_at: string;
  email: string | null;
  display_name: string | null;
};

export type SlugAvailability = { slug: string; available: boolean };

// ─── Connection ──────────────────────────────────────────────────────────────

/**
 * Phạm vi dùng của một kết nối.
 *
 * `workspace` — tài khoản chung, cả workspace thấy và dùng được.
 * `private`   — tài khoản riêng; người khác **không thấy** nó trong danh sách.
 */
export type SharingPolicy = "workspace" | "private";

/** Sáu trạng thái backend trả về. */
export type ConnectionStatus =
  | "active"
  | "expired"
  | "revoked"
  | "missing_scope"
  | "reconnect_required"
  | "disabled";

/** Org cho phép thêm loại tài khoản nào. */
export type ConnectionPolicy = "open" | "managed";

export type Provider = {
  id: string;
  label: string;
  default_scopes: string[];
  supports_refresh: boolean;
  /** `false` nghĩa là bản triển khai này chưa có client id/secret cho provider. */
  configured: boolean;
  notes: string;
};

export type Connection = {
  id: string;
  provider: string;
  display_name: string;
  status: ConnectionStatus;
  sharing: SharingPolicy;
  /** Với `private` là người duy nhất dùng được; với `workspace` là người đã cài. */
  owner_user_id: string;
  granted_scopes: string[];
  external_account_id: string | null;
  external_account_label: string | null;
  connected_by: string;
  created_at: string;
  updated_at: string;
  expires_at: string | null;
  last_error: string | null;
};

/**
 * Kết nối nhìn từ màn hình audit của quản trị viên.
 *
 * Cố ý thiếu tên hiển thị, scope và danh tính tài khoản: quản trị viên cần biết
 * *có* kết nối để thu hồi, không cần biết nó trỏ tới đâu.
 */
export type ConnectionAudit = {
  id: string;
  provider: string;
  sharing: SharingPolicy;
  owner_user_id: string;
  status: ConnectionStatus;
  created_at: string;
};

export type ConnectionPolicySettings = {
  connection_policy: ConnectionPolicy;
  /** Số kết nối riêng vừa bị tạm dừng do siết chính sách. */
  disabled_private_connections: number;
};

export type Authorization = {
  authorize_url: string;
  expires_in_seconds: number;
};

// ─── Agent — lựa chọn connection (ADR-0004) ──────────────────────────────────

/**
 * Một ứng viên connection cho một provider, khi có từ hai trở lên và Agent
 * Runtime chưa biết dùng cái nào — đúng hình dạng `details.candidates` của
 * lỗi `ConnectionChoiceRequired` (`error_category` ở SSE `tool_call_completed`).
 */
export type ConnectionCandidate = {
  connection_id: string;
  display_name: string;
  sharing: SharingPolicy;
  external_account_label: string | null;
};

/** Một lựa chọn connection đã chốt cho một conversation (`GET /v1/conversations/{id}/connections`). */
export type ConversationConnection = {
  provider: string;
  connection_id: string;
};

// ─── Agent Runtime — agent, hội thoại, tin nhắn ─────────────────────────────
//
// Các endpoint này thuộc tiến trình Agent Runtime (`entrypoints/agent.py`,
// cổng riêng), nhưng cùng đi qua rewrite của Next như mọi endpoint khác —
// xem `next.config.ts`.

export type Agent = {
  id: string;
  name: string;
  system_prompt: string;
  model: string;
  enabled_tool_ids: string[];
  max_tool_calls_per_turn: number;
  created_by: string;
  created_at: string;
  updated_at: string;
  /** Agent đã lưu trữ: hội thoại cũ vẫn đọc được, không mở hội thoại mới được. */
  archived_at: string | null;
};

export type Conversation = {
  id: string;
  agent_id: string;
  user_id: string;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
};

/** Một lời gọi tool model đã quyết định, gắn trên một `ChatMessage` vai trò assistant. */
export type ChatToolCall = {
  call_id: string;
  tool_id: string;
  input: Record<string, unknown>;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls: ChatToolCall[];
  /** Có trên message vai trò `tool` — trỏ về `call_id` đã sinh ra nó. */
  tool_call_id: string | null;
  created_at: string;
};

/** Một tool trong danh mục, cho màn hình tạo Agent chọn (`GET /v1/tools`). */
export type ToolSummary = {
  tool_id: string;
  version: string;
  provider: string;
  description: string;
  /** `read` hoặc `write` — tool `write` gây side effect ra bên ngoài. */
  side_effect: string;
  required_scopes: string[];
  input_schema: Record<string, unknown>;
};

// ─── Luồng sự kiện SSE của một lượt chat ────────────────────────────────────
//
// `POST /v1/conversations/{id}/messages` trả về một luồng SSE. Mỗi khối là một
// `{ event, data }` — bốn loại dưới đây (xem `_sse_event` ở entrypoints/agent.py).

export type AgentSseEvent =
  | { event: "tool_call_started"; data: { tool_id: string } }
  | {
      event: "tool_call_completed";
      data: {
        tool_id: string;
        is_error?: boolean;
        error_category?: ErrorCategory;
        error_details?: { provider?: string; candidates?: ConnectionCandidate[] };
      };
    }
  | { event: "final"; data: { text: string } }
  | { event: "error"; data: { message: string } };
