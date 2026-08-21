"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  apiRequest,
  type Member,
  type Organization,
  type SlugAvailability,
  type Workspace,
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

export function useCreateWorkspace(organizationId: string | null) {
  const { accessToken } = useSession();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string }) =>
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
