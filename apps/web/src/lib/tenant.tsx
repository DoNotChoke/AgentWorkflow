"use client";

import { createContext, useContext } from "react";
import type { Organization, Workspace } from "@/lib/api";

/**
 * Tenant đang mở, chia sẻ cho mọi màn hình bên trong vỏ ứng dụng.
 *
 * Trước đây vỏ nhận tenant qua props, nên chỉ nó biết đang ở organization nào —
 * một trang bên trong muốn gọi API theo đúng tenant thì không có đường nào lấy.
 * Lựa chọn tenant là trạng thái của cả khung ứng dụng chứ không của riêng vỏ,
 * nên nó nằm ở context.
 *
 * `organization` không bao giờ null: layout chặn trước, chưa phân giải xong thì
 * chưa dựng tới đây.
 */
export type TenantContextValue = {
  organizations: Organization[];
  organization: Organization;
  workspaces: Workspace[];
  workspace: Workspace | null;
  selectOrganization: (organizationId: string) => void;
  selectWorkspace: (workspaceId: string) => void;
};

const TenantContext = createContext<TenantContextValue | null>(null);

export function TenantProvider({
  value,
  children,
}: {
  value: TenantContextValue;
  children: React.ReactNode;
}) {
  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>;
}

export function useTenant(): TenantContextValue {
  const value = useContext(TenantContext);
  if (value === null) throw new Error("useTenant phải nằm bên trong <TenantProvider>.");
  return value;
}
