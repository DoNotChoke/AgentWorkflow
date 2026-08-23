import type { OrgRole, WorkspaceRole } from "@/lib/api";

/**
 * Vai trò, và các quy tắc quanh chúng.
 *
 * Mọi hàm ở đây là **bản sao** của luật trong `tenant/domain/entities.py`. Chúng
 * quyết định cái gì hiện ra và cái gì bị khoá, chứ không quyết định cái gì được
 * phép — backend mới là nơi enforce (§47.16). Giữ hai bên khớp nhau chỉ để người
 * dùng không bấm vào thứ chắc chắn sẽ bị từ chối.
 */

export const ORG_ROLES: { value: OrgRole; label: string; description: string }[] = [
  {
    value: "owner",
    label: "Owner",
    description: "Everything an admin can do, plus making other people owners.",
  },
  {
    value: "admin",
    label: "Admin",
    description:
      "Runs workspaces and members, and reaches every workspace without being granted one. Cannot touch owners or other admins.",
  },
  {
    value: "member",
    label: "Member",
    description: "Reaches only the workspaces they are granted, one by one.",
  },
];

export const WORKSPACE_ROLES: { value: WorkspaceRole; label: string }[] = [
  { value: "admin", label: "Admin" },
  { value: "editor", label: "Editor" },
  { value: "viewer", label: "Viewer" },
];

/**
 * Quyền mặc định khi bấm "Grant access".
 *
 * Cấp quyền là một cú bấm, không hỏi thêm — nên nó phải là mức thấp nhất. Nâng
 * lên là việc tiếp theo, ngay tại chỗ, và là một quyết định có ý thức.
 */
export const DEFAULT_WORKSPACE_ROLE: WorkspaceRole = "viewer";

export function orgRoleLabel(role: OrgRole): string {
  return ORG_ROLES.find((it) => it.value === role)?.label ?? role;
}

export function workspaceRoleLabel(role: WorkspaceRole): string {
  return WORKSPACE_ROLES.find((it) => it.value === role)?.label ?? role;
}

/** `can_manage_members` — owner và admin quản lý được thành viên. */
export function canManageMembers(role: OrgRole): boolean {
  return role === "owner" || role === "admin";
}

/**
 * `resolve_workspace_role` — owner và admin với tới mọi workspace mà không cần
 * bản ghi nào. Vì thế danh sách workspace của họ luôn rỗng, và giao diện phải
 * đọc điều đó từ vai trò chứ không từ danh sách.
 */
export function reachesEveryWorkspace(role: OrgRole): boolean {
  return role === "owner" || role === "admin";
}

/** `ensure_can_assign_org_role`. */
export function canAssignOrgRole({
  actorRole,
  targetCurrentRole,
  newRole,
}: {
  actorRole: OrgRole;
  targetCurrentRole: OrgRole | null;
  newRole: OrgRole;
}): boolean {
  if (!canManageMembers(actorRole)) return false;
  if (actorRole === "owner") return true;
  if (newRole === "owner") return false;
  return targetCurrentRole === null || targetCurrentRole === "member";
}

/** `ensure_not_last_owner` — organization không bao giờ được hết OWNER. */
export function isLastOwner({
  role,
  ownerCount,
}: {
  role: OrgRole;
  ownerCount: number;
}): boolean {
  return role === "owner" && ownerCount <= 1;
}
