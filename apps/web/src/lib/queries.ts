"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiRequest,
  type Member,
  type Organization,
  type OrgRole,
  type SlugAvailability,
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
