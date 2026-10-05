"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  type Agent,
  apiRequest,
  type Authorization,
  type CompileSession,
  type ChatMessage,
  type Connection,
  type ConnectionAudit,
  type ConnectionPolicy,
  type ConnectionPolicySettings,
  type Conversation,
  type ConversationConnection,
  type Member,
  type Organization,
  type OrgRole,
  type Provider,
  type SharingPolicy,
  type SlugAvailability,
  type ToolSummary,
  type Workflow,
  type WorkflowDraft,
  type Workspace,
  type WorkspaceRole,
} from "@/lib/api";
import { useSession } from "@/lib/session";

/**
 * Hook đọc/ghi dữ liệu tenant.
 *
 * Mọi hook đều tự lấy access token từ phiên và tự tắt khi chưa đăng nhập, để
 * không có màn hình nào bắn request ẩn danh rồi nhận 401 trong lúc phiên đang
 * được khôi phục.
 */

export const queryKeys = {
  organizations: ["organizations"] as const,
  workspaces: (organizationId: string | null) => ["workspaces", organizationId] as const,
  members: (organizationId: string | null) => ["members", organizationId] as const,
  providers: ["connection-providers"] as const,
  connections: (organizationId: string | null, workspaceId: string | null) =>
    ["connections", organizationId, workspaceId] as const,
  connectionAudit: (organizationId: string | null, workspaceId: string | null) =>
    ["connection-audit", organizationId, workspaceId] as const,
  connectionPolicy: (organizationId: string | null) =>
    ["connection-policy", organizationId] as const,
  tools: ["tools"] as const,
  agents: (organizationId: string | null, workspaceId: string | null) =>
    ["agents", organizationId, workspaceId] as const,
  conversations: (organizationId: string | null, agentId: string | null) =>
    ["conversations", organizationId, agentId] as const,
  messages: (organizationId: string | null, conversationId: string | null) =>
    ["messages", organizationId, conversationId] as const,
  conversationConnections: (organizationId: string | null, conversationId: string | null) =>
    ["conversation-connections", organizationId, conversationId] as const,
  workflows: (organizationId: string | null, workspaceId: string | null) =>
    ["workflows", organizationId, workspaceId] as const,
  workflow: (organizationId: string | null, workflowId: string | null) =>
    ["workflow", organizationId, workflowId] as const,
  workflowDraft: (organizationId: string | null, workflowId: string | null) =>
    ["workflow-draft", organizationId, workflowId] as const,
  compileSession: (organizationId: string | null, sessionId: string | null) =>
    ["compile-session", organizationId, sessionId] as const,
};

/** Header chọn tenant cho request hiện tại — bỏ trống thì backend lấy org đầu tiên. */
function tenantHeaders(organizationId?: string | null): Record<string, string> {
  return organizationId ? { "x-organization-id": organizationId } : {};
}

export function useOrganizations() {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.organizations,
    enabled: status === "authenticated",
    queryFn: () => apiRequest<Organization[]>("/v1/organizations", { accessToken }),
  });
}

/**
 * Tạo một organization nữa cho người đang đăng nhập.
 *
 * Ba lời gọi chứ không phải một: backend sinh slug tạm từ tên khi tạo, nên phải
 * `PATCH` đè địa chỉ người dùng chọn rồi mới chốt thiết lập — chốt xong slug
 * đóng băng vĩnh viễn nên thứ tự này là bắt buộc.
 *
 * Chuỗi này không nguyên tử. Hỏng ở bước hai hoặc ba thì organization vẫn tồn
 * tại ở trạng thái chưa thiết lập xong, và wizard `/onboarding` là nơi dọn nốt
 * — nó tìm đúng organization còn dở chứ không phải cái đầu danh sách.
 *
 * Workspace `Default` do backend cấp kèm, không phải việc của màn hình này.
 */
export function useCreateOrganization() {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { name: string; slug: string }) => {
      const created = await apiRequest<Organization>("/v1/organizations", {
        method: "POST",
        accessToken,
        body: { name: input.name },
      });
      const headers = tenantHeaders(created.id);
      await apiRequest<Organization>("/v1/organizations/current", {
        method: "PATCH",
        accessToken,
        headers,
        body: { name: input.name, slug: input.slug },
      });
      return apiRequest<Organization>("/v1/organizations/current/complete-onboarding", {
        method: "POST",
        accessToken,
        headers,
      });
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.organizations }),
  });
}

export function useWorkspaces(organizationId: string | null) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.workspaces(organizationId),
    enabled: status === "authenticated" && organizationId !== null,
    queryFn: () =>
      apiRequest<Workspace[]>("/v1/workspaces", {
        accessToken,
        headers: tenantHeaders(organizationId),
      }),
  });
}

export function useMembers(organizationId: string | null) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.members(organizationId),
    enabled: status === "authenticated" && organizationId !== null,
    queryFn: () =>
      apiRequest<Member[]>("/v1/organizations/current/members", {
        accessToken,
        headers: tenantHeaders(organizationId),
      }),
  });
}

/**
 * Thêm một người **đã có tài khoản** vào organization, theo email.
 *
 * Không phải lời mời: backend tra email trong bảng người dùng và từ chối nếu
 * không thấy ai. Không có thư nào được gửi đi, và cũng không có endpoint nào
 * tra trước một email có tài khoản hay chưa — biết được điều đó là biết ai đã
 * đăng ký, nên câu trả lời chỉ tới sau khi thêm.
 */
export function useAddMember(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { email: string; role: OrgRole }) =>
      apiRequest<Member>("/v1/organizations/current/members", {
        method: "POST",
        accessToken,
        headers: tenantHeaders(organizationId),
        body: input,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.members(organizationId) }),
  });
}

export function useChangeMemberRole(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { userId: string; role: OrgRole }) =>
      apiRequest<void>(`/v1/organizations/current/members/${input.userId}`, {
        method: "PATCH",
        accessToken,
        headers: tenantHeaders(organizationId),
        body: { role: input.role },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.members(organizationId) }),
  });
}

/**
 * Bỏ một người khỏi organization — hoặc chính mình.
 *
 * Tự rời đi là đường hợp lệ, và nó đổi luôn danh sách organization của người
 * đang đăng nhập. Nên phải làm mới cả hai: không làm mới `organizations` thì vỏ
 * ứng dụng vẫn đứng ở một tenant mà mọi request tiếp theo đều nhận 403.
 */
export function useRemoveMember(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      apiRequest<void>(`/v1/organizations/current/members/${userId}`, {
        method: "DELETE",
        accessToken,
        headers: tenantHeaders(organizationId),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: queryKeys.members(organizationId) });
      await queryClient.invalidateQueries({ queryKey: queryKeys.organizations });
    },
  });
}

/**
 * Cấp hoặc đổi quyền của một người trên một workspace.
 *
 * Một lời gọi cho cả hai việc: backend tự phân biệt cấp mới với đổi vai trò,
 * nên giao diện không phải nhớ người này đã có quyền hay chưa.
 */
export function useSetWorkspaceMember(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { workspaceId: string; userId: string; role: WorkspaceRole }) =>
      apiRequest<void>(`/v1/workspaces/${input.workspaceId}/members`, {
        method: "PUT",
        accessToken,
        headers: tenantHeaders(organizationId),
        body: { user_id: input.userId, role: input.role },
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.members(organizationId) }),
  });
}

export function useRemoveWorkspaceMember(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { workspaceId: string; userId: string }) =>
      apiRequest<void>(`/v1/workspaces/${input.workspaceId}/members/${input.userId}`, {
        method: "DELETE",
        accessToken,
        headers: tenantHeaders(organizationId),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.members(organizationId) }),
  });
}

// ─── Onboarding bước 1 ──────────────────────────────────────────────────────

/**
 * Kiểm một địa chỉ còn trống chưa.
 *
 * Địa chỉ sai cú pháp làm backend trả 422, và đó là lỗi *hợp lệ* ở đây — màn
 * hình cần phân biệt "gõ sai" với "đã có người dùng" nên không retry, không
 * coi là sự cố.
 */
export function useSlugAvailability(slug: string) {
  const { accessToken, status } = useSession();
  const cleaned = slug.trim().toLowerCase();
  return useQuery({
    queryKey: ["slug-available", cleaned],
    enabled: status === "authenticated" && cleaned.length >= 3,
    retry: false,
    queryFn: () =>
      apiRequest<SlugAvailability>(
        `/v1/organizations/slug-available?slug=${encodeURIComponent(cleaned)}`,
        { accessToken },
      ),
  });
}

export function useUpdateOrganization(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; slug?: string }) =>
      apiRequest<Organization>("/v1/organizations/current", {
        method: "PATCH",
        accessToken,
        headers: tenantHeaders(organizationId),
        body: input,
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.organizations }),
  });
}

export function useCompleteOnboarding(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiRequest<Organization>("/v1/organizations/current/complete-onboarding", {
        method: "POST",
        accessToken,
        headers: tenantHeaders(organizationId),
      }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.organizations }),
  });
}

export function useRenameWorkspace(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { workspaceId: string; name: string; slug?: string }) =>
      apiRequest<Workspace>(`/v1/workspaces/${input.workspaceId}`, {
        method: "PATCH",
        accessToken,
        headers: tenantHeaders(organizationId),
        body: { name: input.name, slug: input.slug },
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces(organizationId) }),
  });
}

/**
 * Tạo workspace trong organization đang mở.
 *
 * Bỏ trống `slug` thì backend tự sinh địa chỉ từ tên; truyền vào thì địa chỉ đó
 * được giữ nguyên hoặc bị từ chối, không bao giờ bị thêm hậu tố sau lưng.
 */
export function useCreateWorkspace(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; slug?: string }) =>
      apiRequest<Workspace>("/v1/workspaces", {
        method: "POST",
        accessToken,
        headers: tenantHeaders(organizationId),
        body: input,
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces(organizationId) }),
  });
}

export function useDeleteWorkspace(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (workspaceId: string) =>
      apiRequest<void>(`/v1/workspaces/${workspaceId}`, {
        method: "DELETE",
        accessToken,
        headers: tenantHeaders(organizationId),
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: queryKeys.workspaces(organizationId) }),
  });
}

// ─── Connection ──────────────────────────────────────────────────────────────

/**
 * Hook cho Connection.
 *
 * Khác các hook tenant ở một chỗ: kết nối thuộc về **workspace**, không phải
 * organization, nên mọi lời gọi phải kèm cả hai header. Bỏ `x-workspace-id` thì
 * backend tự chọn workspace đầu tiên người gọi thấy — đúng cho một màn hình
 * chung, nhưng sai ở đây, vì màn hình này đang nói về một workspace cụ thể.
 */

function workspaceHeaders(
  organizationId: string | null,
  workspaceId: string | null,
): Record<string, string> {
  const headers: Record<string, string> = {};
  if (organizationId) headers["x-organization-id"] = organizationId;
  if (workspaceId) headers["x-workspace-id"] = workspaceId;
  return headers;
}

export function useProviders() {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.providers,
    enabled: status === "authenticated",
    // Danh mục provider là cấu hình của bản triển khai, không đổi theo phiên.
    staleTime: 5 * 60 * 1000,
    queryFn: () => apiRequest<Provider[]>("/v1/connections/providers", { accessToken }),
  });
}

export function useConnections(organizationId: string | null, workspaceId: string | null) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.connections(organizationId, workspaceId),
    enabled: status === "authenticated" && organizationId !== null && workspaceId !== null,
    queryFn: () =>
      apiRequest<Connection[]>("/v1/connections", {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

export function useConnectionPolicy(organizationId: string | null) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.connectionPolicy(organizationId),
    enabled: status === "authenticated" && organizationId !== null,
    queryFn: () =>
      apiRequest<ConnectionPolicySettings>("/v1/connections/settings", {
        accessToken,
        headers: tenantHeaders(organizationId),
      }),
  });
}

/**
 * Danh sách mọi kết nối trong workspace, kể cả kết nối riêng của người khác.
 *
 * Chỉ quản trị viên organization gọi được — backend trả 403 cho người khác, nên
 * nơi gọi phải tự tắt hook khi biết mình không đủ quyền thay vì để màn hình
 * hiện một lỗi mà người dùng chẳng làm gì được.
 */
export function useConnectionAudit(
  organizationId: string | null,
  workspaceId: string | null,
  enabled: boolean,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.connectionAudit(organizationId, workspaceId),
    enabled:
      enabled && status === "authenticated" && organizationId !== null && workspaceId !== null,
    queryFn: () =>
      apiRequest<ConnectionAudit[]>("/v1/connections/audit", {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

/**
 * Bước một của vòng OAuth: xin backend dựng URL uỷ quyền.
 *
 * Không `invalidateQueries` — chưa có gì đổi. Kết nối chỉ ra đời ở bước hai,
 * khi provider gọi ngược lại callback, và lúc đó trình duyệt đã rời khỏi trang
 * này rồi. Danh sách được làm mới khi người dùng quay lại (xem `useConnectionCallbackToast`).
 */
export function useStartAuthorization(
  organizationId: string | null,
  workspaceId: string | null,
) {
  const { accessToken } = useSession();
  return useMutation({
    mutationFn: (input: { provider: string; sharing: SharingPolicy; connectionId?: string }) =>
      apiRequest<Authorization>(`/v1/connections/${input.provider}/authorize`, {
        method: "POST",
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
        body: {
          sharing: input.sharing,
          ...(input.connectionId ? { reconnect_connection_id: input.connectionId } : {}),
        },
      }),
  });
}

/** Tạm dừng, bật lại, đổi tên, thu hồi — bốn thao tác cùng một hình. */
function useConnectionMutation<TInput>(
  organizationId: string | null,
  workspaceId: string | null,
  request: (
    input: TInput,
    call: (path: string, init: { method: "POST" | "PATCH" | "DELETE"; body?: unknown }) => Promise<Connection>,
  ) => Promise<Connection>,
) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: TInput) =>
      request(input, (path, init) =>
        apiRequest<Connection>(path, {
          method: init.method,
          accessToken,
          headers: workspaceHeaders(organizationId, workspaceId),
          body: init.body,
        }),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: queryKeys.connections(organizationId, workspaceId),
      });
      queryClient.invalidateQueries({
        queryKey: queryKeys.connectionAudit(organizationId, workspaceId),
      });
    },
  });
}

export function useDisableConnection(organizationId: string | null, workspaceId: string | null) {
  return useConnectionMutation<string>(organizationId, workspaceId, (id, call) =>
    call(`/v1/connections/${id}/disable`, { method: "POST" }),
  );
}

export function useEnableConnection(organizationId: string | null, workspaceId: string | null) {
  return useConnectionMutation<string>(organizationId, workspaceId, (id, call) =>
    call(`/v1/connections/${id}/enable`, { method: "POST" }),
  );
}

export function useRevokeConnection(organizationId: string | null, workspaceId: string | null) {
  return useConnectionMutation<string>(organizationId, workspaceId, (id, call) =>
    call(`/v1/connections/${id}`, { method: "DELETE" }),
  );
}

export function useRenameConnection(organizationId: string | null, workspaceId: string | null) {
  return useConnectionMutation<{ id: string; displayName: string }>(
    organizationId,
    workspaceId,
    (input, call) =>
      call(`/v1/connections/${input.id}`, {
        method: "PATCH",
        body: { display_name: input.displayName },
      }),
  );
}

/**
 * Đổi chính sách kết nối của organization.
 *
 * Siết sang `managed` sẽ tạm dừng mọi kết nối riêng đang sống, nên danh sách
 * kết nối cũng phải được làm mới — không chỉ chính sách.
 */
export function useSetConnectionPolicy(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (policy: ConnectionPolicy) =>
      apiRequest<ConnectionPolicySettings>("/v1/connections/settings", {
        method: "PATCH",
        accessToken,
        headers: tenantHeaders(organizationId),
        body: { connection_policy: policy },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.connectionPolicy(organizationId) });
      queryClient.invalidateQueries({ queryKey: ["connections"] });
      queryClient.invalidateQueries({ queryKey: ["connection-audit"] });
    },
  });
}

// ─── Agent Runtime — lựa chọn connection (ADR-0004) ──────────────────────────
//
// Endpoint thuộc Agent Runtime (`entrypoints/agent.py`), không phải Control
// Plane — tiến trình riêng, cổng riêng, nhưng cùng đi qua rewrite của Next
// (xem `next.config.ts`) nên gọi giống hệt mọi endpoint khác ở file này.
// Không có query nào đọc lại lựa chọn đã lưu — nơi dùng hook này tự giữ trạng
// thái "đang dùng connection nào" từ sự kiện SSE của chính lượt chat, không
// phải từ một lần fetch riêng.

/** Chốt (hoặc đổi) connection dùng cho một provider trong một conversation. */
export function useSetConversationConnection(
  organizationId: string | null,
  workspaceId: string | null,
) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { conversationId: string; provider: string; connectionId: string }) =>
      apiRequest<void>(`/v1/conversations/${input.conversationId}/connections`, {
        method: "POST",
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
        body: { provider: input.provider, connection_id: input.connectionId },
      }),
    onSuccess: (_data, input) =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.conversationConnections(organizationId, input.conversationId),
      }),
  });
}

/** Lựa chọn connection đã chốt cho một conversation — dựng dải "Using" của màn chat. */
export function useConversationConnections(
  organizationId: string | null,
  workspaceId: string | null,
  conversationId: string | null,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.conversationConnections(organizationId, conversationId),
    enabled: status === "authenticated" && organizationId !== null && conversationId !== null,
    queryFn: () =>
      apiRequest<ConversationConnection[]>(
        `/v1/conversations/${conversationId}/connections`,
        { accessToken, headers: workspaceHeaders(organizationId, workspaceId) },
      ),
  });
}

// ─── Agent Runtime — agent, hội thoại, tin nhắn ────────────────────────────
//
// Agent và Conversation đều thuộc **workspace**, nên mọi lời gọi kèm cả hai
// header — cùng lý do như Connection. Việc GỬI tin nhắn không nằm ở đây: nó là
// một luồng SSE, xem `useAgentTurn` trong `lib/agent-chat.ts`.

/** Danh mục tool cho màn hình tạo Agent (`GET /v1/tools`, Control Plane). */
export function useTools() {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.tools,
    enabled: status === "authenticated",
    staleTime: 5 * 60 * 1000,
    queryFn: () => apiRequest<ToolSummary[]>("/v1/tools", { accessToken }),
  });
}

export function useAgents(organizationId: string | null, workspaceId: string | null) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.agents(organizationId, workspaceId),
    enabled: status === "authenticated" && organizationId !== null && workspaceId !== null,
    queryFn: () =>
      apiRequest<Agent[]>("/v1/agents", {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

export function useCreateAgent(organizationId: string | null, workspaceId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      name: string;
      system_prompt: string;
      model: string;
      enabled_tool_ids: string[];
      max_tool_calls_per_turn: number;
    }) =>
      apiRequest<Agent>("/v1/agents", {
        method: "POST",
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
        body: input,
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.agents(organizationId, workspaceId),
      }),
  });
}

/**
 * Lưu trữ một agent — người tạo hoặc `ws:admin`. Hội thoại cũ vẫn đọc được;
 * chỉ không mở được hội thoại mới. Đảo ngược được bằng `useUnarchiveAgent`.
 */
export function useArchiveAgent(organizationId: string | null, workspaceId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (agentId: string) =>
      apiRequest<Agent>(`/v1/agents/${agentId}/archive`, {
        method: "POST",
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.agents(organizationId, workspaceId),
      }),
  });
}

/** Đưa một agent đã lưu trữ trở lại danh sách đang hoạt động. */
export function useUnarchiveAgent(organizationId: string | null, workspaceId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (agentId: string) =>
      apiRequest<Agent>(`/v1/agents/${agentId}/unarchive`, {
        method: "POST",
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.agents(organizationId, workspaceId),
      }),
  });
}

export function useConversations(
  organizationId: string | null,
  workspaceId: string | null,
  agentId: string | null,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.conversations(organizationId, agentId),
    enabled: status === "authenticated" && organizationId !== null && agentId !== null,
    queryFn: () =>
      apiRequest<Conversation[]>(`/v1/agents/${agentId}/conversations`, {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

export function useCreateConversation(
  organizationId: string | null,
  workspaceId: string | null,
) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (agentId: string) =>
      apiRequest<Conversation>(`/v1/agents/${agentId}/conversations`, {
        method: "POST",
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
    onSuccess: (_data, agentId) =>
      queryClient.invalidateQueries({
        queryKey: queryKeys.conversations(organizationId, agentId),
      }),
  });
}

export function useConversationMessages(
  organizationId: string | null,
  workspaceId: string | null,
  conversationId: string | null,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.messages(organizationId, conversationId),
    enabled: status === "authenticated" && organizationId !== null && conversationId !== null,
    queryFn: () =>
      apiRequest<ChatMessage[]>(`/v1/conversations/${conversationId}/messages`, {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

// ─── Workflow ───────────────────────────────────────────────────────────────

export function useWorkflows(organizationId: string | null, workspaceId: string | null) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.workflows(organizationId, workspaceId),
    enabled: status === "authenticated" && organizationId !== null && workspaceId !== null,
    queryFn: () =>
      apiRequest<Workflow[]>("/v1/workflows", {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

export function useWorkflow(
  organizationId: string | null,
  workspaceId: string | null,
  workflowId: string | null,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.workflow(organizationId, workflowId),
    enabled: status === "authenticated" && workspaceId !== null && workflowId !== null,
    queryFn: () =>
      apiRequest<Workflow>(`/v1/workflows/${workflowId}`, {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

export function useWorkflowDraft(
  organizationId: string | null,
  workspaceId: string | null,
  workflowId: string | null,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.workflowDraft(organizationId, workflowId),
    enabled: status === "authenticated" && workspaceId !== null && workflowId !== null,
    queryFn: () =>
      apiRequest<WorkflowDraft>(`/v1/workflows/${workflowId}/draft`, {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}

/**
 * Phiên biên dịch đã sinh ra một draft. Chỉ người mở phiên đọc được nó; người khác
 * nhận 403, và màn draft khi đó bỏ mục "Left out" chứ không báo lỗi.
 */
export function useCompileSessionById(
  organizationId: string | null,
  workspaceId: string | null,
  sessionId: string | null,
) {
  const { accessToken, status } = useSession();
  return useQuery({
    queryKey: queryKeys.compileSession(organizationId, sessionId),
    enabled: status === "authenticated" && workspaceId !== null && sessionId !== null,
    retry: false,
    queryFn: () =>
      apiRequest<CompileSession>(`/v1/compile-sessions/${sessionId}`, {
        accessToken,
        headers: workspaceHeaders(organizationId, workspaceId),
      }),
  });
}
