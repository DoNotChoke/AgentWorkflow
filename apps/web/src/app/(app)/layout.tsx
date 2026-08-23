"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Shell } from "@/components/shell";
import { ErrorNote, Spinner } from "@/components/ui";
import { useOrganizations, useWorkspaces } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { TenantProvider } from "@/lib/tenant";

/**
 * Cửa vào ứng dụng: xác thực, chọn tenant, rồi mới dựng shell.
 *
 * Guard chạy ở client vì phiên nằm trong cookie httpOnly — server component
 * không đọc được access token, và middleware thì cũng chỉ thấy cookie thô chứ
 * không biết membership.
 *
 * Đây **không phải** lớp bảo mật. Backend mới là nơi enforce (§47.16); ở đây
 * chỉ là điều hướng cho đúng, tránh hiện màn hình trống rồi mới nhận 401.
 */
export default function AppLayout({ children }: LayoutProps<"/">) {
  const router = useRouter();
  const { status } = useSession();
  const organizations = useOrganizations();

  const [organizationId, setOrganizationId] = useState<string | null>(null);
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);

  const current =
    organizations.data?.find((org) => org.id === organizationId) ?? organizations.data?.[0] ?? null;

  const workspaces = useWorkspaces(current?.id ?? null);
  const currentWorkspace =
    workspaces.data?.find((workspace) => workspace.id === workspaceId) ??
    workspaces.data?.[0] ??
    null;

  useEffect(() => {
    if (status === "anonymous") router.replace("/sign-in");
  }, [status, router]);

  // Chưa đặt tên organization thì đưa vào wizard trước — cờ này do backend
  // trả về, không phải trạng thái client tự đoán.
  useEffect(() => {
    if (current && !current.onboarded) router.replace("/onboarding");
  }, [current, router]);

  if (status !== "authenticated" || organizations.isPending) return <Spinner label="Loading…" />;

  if (organizations.isError) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="w-full max-w-md">
          <ErrorNote error={organizations.error} />
        </div>
      </div>
    );
  }

  if (!current) return <Spinner label="No organisation found." />;
  if (!current.onboarded) return <Spinner label="Taking you to setup…" />;

  return (
    <TenantProvider
      value={{
        organizations: organizations.data ?? [],
        organization: current,
        workspaces: workspaces.data ?? [],
        workspace: currentWorkspace,
        selectOrganization: (id) => {
          setOrganizationId(id);
          // Workspace thuộc về organization cũ — giữ lại sẽ trỏ sang tenant khác.
          setWorkspaceId(null);
        },
        selectWorkspace: setWorkspaceId,
      }}
    >
      <Shell>{children}</Shell>
    </TenantProvider>
  );
}
