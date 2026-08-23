"use client";

import { useState } from "react";
import { AddMemberDialog } from "@/components/add-member-dialog";
import { RemoveMemberDialog } from "@/components/remove-member-dialog";
import { ErrorNote, Spinner } from "@/components/ui";
import type { Member, OrgRole, Workspace, WorkspaceRole } from "@/lib/api";
import { initials } from "@/lib/name";
import {
  useChangeMemberRole,
  useMembers,
  useRemoveWorkspaceMember,
  useSetWorkspaceMember,
} from "@/lib/queries";
import {
  canAssignOrgRole,
  canManageMembers,
  DEFAULT_WORKSPACE_ROLE,
  isLastOwner,
  ORG_ROLES,
  orgRoleLabel,
  reachesEveryWorkspace,
  WORKSPACE_ROLES,
} from "@/lib/roles";
import { useSession } from "@/lib/session";
import { useTenant } from "@/lib/tenant";

/**
 * Thành viên của organization đang mở.
 *
 * Bảng bên trái để nhìn toàn cảnh và đổi vai trò tại chỗ; panel bên phải cho
 * đúng một người, vì quyền workspace là danh sách chứ không phải một ô.
 *
 * Mọi thứ khoá được ở đây đều là bản sao của luật trong `tenant/domain`, chỉ để
 * không dẫn người dùng vào chỗ chắc chắn bị từ chối. Nơi chặn thật là backend.
 */
export default function MembersSettingsPage() {
  const { organization } = useTenant();
  const { user } = useSession();
  const members = useMembers(organization.id);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [search, setSearch] = useState("");

  const canManage = canManageMembers(organization.role);
  const all = members.data ?? [];
  const ownerCount = all.filter((it) => it.role === "owner").length;
  const selected = all.find((it) => it.user_id === selectedId) ?? null;

  const query = search.trim().toLowerCase();
  const visible = query
    ? all.filter((member) =>
        [member.display_name, member.email].some((field) =>
          field?.toLowerCase().includes(query),
        ),
      )
    : all;

  if (members.isPending) return <Spinner label="Loading members…" />;
  if (members.isError) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="w-full max-w-md">
          <ErrorNote error={members.error} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 grow flex-col">
        <header className="flex h-16 shrink-0 items-center border-b border-line px-7">
          <span className="text-base font-semibold">Members</span>
          <span className="ml-2 text-sm text-ink-4">{all.length}</span>
          <div className="grow" />
          <label className="flex h-9 w-[220px] items-center gap-2 rounded-control border border-line-strong bg-surface px-3">
            <SearchIcon />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search members"
              aria-label="Search members"
              className="min-w-0 grow text-sm outline-none placeholder:text-ink-4"
            />
          </label>
          {canManage ? (
            <button
              type="button"
              onClick={() => setAdding(true)}
              className="ml-2 flex h-9 items-center gap-2 rounded-control bg-ink px-3 text-sm font-medium text-white transition-colors hover:bg-ink-2"
            >
              <PlusIcon />
              Add member
            </button>
          ) : null}
        </header>

        <div className="flex flex-col gap-4 overflow-auto p-7">
          <div className="overflow-hidden rounded-[9px] border border-line">
            <div className="grid grid-cols-[minmax(0,2.2fr)_140px_minmax(0,1.4fr)_110px_44px] items-center gap-4 border-b border-line bg-canvas px-4 py-2.5 text-[11.5px] font-semibold tracking-wide text-ink-4 uppercase">
              <span>Member</span>
              <span>Org role</span>
              <span>Workspaces</span>
              <span>Added</span>
              <span />
            </div>

            {visible.map((member, index) => (
              <MemberRow
                key={member.user_id}
                member={member}
                isSelf={member.user_id === user?.id}
                selected={member.user_id === selectedId}
                last={index === visible.length - 1}
                ownerCount={ownerCount}
                onOpen={() => setSelectedId(member.user_id)}
              />
            ))}

            {visible.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-ink-4">
                No member matches “{search.trim()}”.
              </p>
            ) : null}
          </div>

          {/*
           * Cảnh báo chỉ có nghĩa với chính người OWNER duy nhất đó — người
           * khác không làm gì được với thông tin này ngoài việc lo lắng.
           */}
          {organization.role === "owner" && ownerCount === 1 ? (
            <p className="flex items-start gap-2.5 rounded-lg border border-warn-line bg-warn-wash px-3.5 py-3 text-[13px] leading-relaxed text-ink-2">
              <WarnIcon />
              <span>
                You are the only <strong className="font-medium text-ink">Owner</strong>. Assign a
                second owner before you change your own role or leave — an organisation without an
                owner cannot be recovered.
              </span>
            </p>
          ) : null}
        </div>
      </div>

      {selected ? (
        <MemberPanel
          key={selected.user_id}
          member={selected}
          isSelf={selected.user_id === user?.id}
          ownerCount={ownerCount}
          onClose={() => setSelectedId(null)}
          onRemoved={() => setSelectedId(null)}
        />
      ) : null}

      {adding ? (
        <AddMemberDialog
          onClose={() => setAdding(false)}
          onAdded={(userId) => {
            setAdding(false);
            // Mở panel ngay: người vừa thêm chưa có workspace nào, và panel là
            // chỗ duy nhất sửa được điều đó.
            setSelectedId(userId);
          }}
        />
      ) : null}
    </div>
  );
}

// ─── Một dòng ───────────────────────────────────────────────────────────────

function MemberRow({
  member,
  isSelf,
  selected,
  last,
  ownerCount,
  onOpen,
}: {
  member: Member;
  isSelf: boolean;
  selected: boolean;
  last: boolean;
  ownerCount: number;
  onOpen: () => void;
}) {
  const { organization } = useTenant();
  const changeRole = useChangeMemberRole(organization.id);
  const [error, setError] = useState<unknown>(null);

  const name = member.display_name ?? "Account not found";
  const subtitle = member.email ?? member.user_id;

  const lockedAsLastOwner = isLastOwner({ role: member.role, ownerCount });
  const mayEdit =
    canAssignOrgRole({
      actorRole: organization.role,
      targetCurrentRole: member.role,
      newRole: member.role,
    }) && !lockedAsLastOwner;

  return (
    <div
      className={`grid grid-cols-[minmax(0,2.2fr)_140px_minmax(0,1.4fr)_110px_44px] items-center gap-4 px-4 py-3 transition-colors ${
        last ? "" : "border-b border-line-soft"
      } ${selected ? "bg-accent-wash" : ""}`}
    >
      <button
        type="button"
        onClick={onOpen}
        className="flex min-w-0 items-center gap-2.5 text-left"
      >
        <Avatar name={member.display_name ?? subtitle} dark={isSelf} />
        <span className="flex min-w-0 flex-col">
          <span className="flex items-center gap-1.5">
            <span
              className={`truncate text-[13.5px] font-medium ${
                member.display_name ? "" : "text-ink-4 italic"
              }`}
            >
              {name}
            </span>
            {isSelf ? (
              <span className="rounded bg-line-soft px-1.5 py-px text-[10.5px] font-semibold text-ink-3">
                YOU
              </span>
            ) : null}
          </span>
          <span className="truncate text-[12.5px] text-ink-4">{subtitle}</span>
        </span>
      </button>

      <div className="flex flex-col gap-1">
        {mayEdit ? (
          <select
            value={member.role}
            disabled={changeRole.isPending}
            aria-label={`Org role for ${name}`}
            onChange={async (event) => {
              setError(null);
              try {
                await changeRole.mutateAsync({
                  userId: member.user_id,
                  role: event.target.value as OrgRole,
                });
              } catch (caught) {
                setError(caught);
              }
            }}
            className="h-7 rounded-md border border-line-strong bg-surface px-2 text-[12.5px] outline-none focus:border-accent disabled:opacity-50"
          >
            {ORG_ROLES.map((option) => (
              <option
                key={option.value}
                value={option.value}
                disabled={
                  !canAssignOrgRole({
                    actorRole: organization.role,
                    targetCurrentRole: member.role,
                    newRole: option.value,
                  })
                }
              >
                {option.label}
              </option>
            ))}
          </select>
        ) : (
          <span
            title={
              lockedAsLastOwner
                ? "The last owner cannot be demoted. Assign a second owner first."
                : "Only an owner can change this."
            }
            className="flex h-7 items-center rounded-md border border-line bg-canvas px-2 text-[12.5px] text-ink-4"
          >
            {orgRoleLabel(member.role)}
          </span>
        )}
        {error ? <span className="text-[11.5px] text-danger">Could not change role.</span> : null}
      </div>

      <WorkspacesCell member={member} />

      <span className="text-[13px] text-ink-3">{formatDate(member.created_at)}</span>

      <button
        type="button"
        onClick={onOpen}
        aria-label={`Open ${name}`}
        className="flex justify-center text-ink-4 transition-colors hover:text-ink-2"
      >
        <ChevronRightIcon />
      </button>
    </div>
  );
}

/**
 * Cột workspace.
 *
 * `member.workspaces` chỉ chứa quyền được cấp tường minh, nên nó rỗng với cả
 * owner/admin (họ với tới mọi nơi) lẫn người chưa được cấp gì (họ không với tới
 * đâu cả). Hai trạng thái trái ngược nhau mà cùng một dữ liệu — phân biệt bằng
 * vai trò, không bằng độ dài danh sách.
 */
function WorkspacesCell({ member }: { member: Member }) {
  if (reachesEveryWorkspace(member.role)) {
    return <span className="truncate text-[13px] text-ink-3">All workspaces</span>;
  }
  if (member.workspaces.length === 0) {
    return <span className="truncate text-[13px] text-warn">No access yet</span>;
  }
  return (
    <span
      className="truncate text-[13px] text-ink-3"
      title={member.workspaces.map((it) => it.name).join(", ")}
    >
      {member.workspaces.map((it) => it.name).join(", ")}
    </span>
  );
}

// ─── Panel ──────────────────────────────────────────────────────────────────

function MemberPanel({
  member,
  isSelf,
  ownerCount,
  onClose,
  onRemoved,
}: {
  member: Member;
  isSelf: boolean;
  ownerCount: number;
  onClose: () => void;
  onRemoved: () => void;
}) {
  const { organization, workspaces } = useTenant();
  const [removing, setRemoving] = useState(false);

  const name = member.display_name ?? "Account not found";
  const canManage = canManageMembers(organization.role);
  const lockedAsLastOwner = isLastOwner({ role: member.role, ownerCount });

  // Tự rời đi luôn được phép, kể cả khi chỉ là member thường — trừ khi mình là
  // OWNER cuối cùng.
  const mayRemove =
    !lockedAsLastOwner &&
    (isSelf ||
      canAssignOrgRole({
        actorRole: organization.role,
        targetCurrentRole: member.role,
        newRole: member.role,
      }));

  return (
    <aside className="flex w-[348px] shrink-0 flex-col border-l border-line bg-canvas">
      <header className="flex h-16 shrink-0 items-center border-b border-line px-4">
        <span className="text-[13px] font-semibold">Member</span>
        <div className="grow" />
        <button
          type="button"
          onClick={onClose}
          aria-label="Close panel"
          className="text-ink-4 hover:text-ink-2"
        >
          <CloseIcon />
        </button>
      </header>

      <div className="flex grow flex-col gap-5 overflow-auto p-4">
        <div className="flex items-center gap-3">
          <Avatar name={member.display_name ?? member.email ?? member.user_id} size={40} />
          <div className="flex min-w-0 flex-col">
            <span className={`truncate font-semibold ${member.display_name ? "" : "text-ink-4"}`}>
              {name}
            </span>
            <span className="truncate text-[12.5px] text-ink-3">
              {member.email ?? "No profile for this account"}
            </span>
          </div>
        </div>

        <dl className="grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-2 text-[12.5px]">
          <dt className="text-ink-4">User</dt>
          <dd className="truncate font-mono text-xs">{member.user_id}</dd>
          <dt className="text-ink-4">Org role</dt>
          <dd>{orgRoleLabel(member.role)}</dd>
          <dt className="text-ink-4">Added</dt>
          <dd>{formatDate(member.created_at)}</dd>
        </dl>

        <div className="flex flex-col gap-2.5">
          <span className="text-[11px] font-semibold tracking-wider text-ink-4 uppercase">
            Workspace access
          </span>

          {reachesEveryWorkspace(member.role) ? (
            <p className="rounded-lg border border-line bg-surface px-3 py-2.5 text-xs leading-relaxed text-ink-3">
              {orgRoleLabel(member.role)}s reach every workspace in {organization.name} without
              being granted access individually.
            </p>
          ) : (
            <>
              {workspaces.map((workspace) => (
                <WorkspaceAccessRow
                  key={workspace.id}
                  workspace={workspace}
                  member={member}
                  editable={canManage}
                />
              ))}
              {member.workspaces.length === 0 ? (
                <p className="text-xs leading-relaxed text-ink-4">
                  Until one of these is granted, {member.display_name ?? "this member"} signs in to
                  an empty {organization.name}.
                </p>
              ) : null}
            </>
          )}
        </div>
      </div>

      {mayRemove ? (
        <div className="border-t border-line p-4">
          <button
            type="button"
            onClick={() => setRemoving(true)}
            className="h-9 w-full rounded-control border border-danger-line bg-surface text-[13px] font-medium text-danger transition-colors hover:bg-danger-wash"
          >
            {isSelf ? "Leave organisation" : "Remove from organisation"}
          </button>
        </div>
      ) : null}

      {removing ? (
        <RemoveMemberDialog
          member={member}
          isSelf={isSelf}
          onClose={() => setRemoving(false)}
          onRemoved={() => {
            setRemoving(false);
            onRemoved();
          }}
        />
      ) : null}
    </aside>
  );
}

/** Một workspace và quyền của người đang xem trên nó — đã cấp hoặc chưa. */
function WorkspaceAccessRow({
  workspace,
  member,
  editable,
}: {
  workspace: Workspace;
  member: Member;
  editable: boolean;
}) {
  const { organization } = useTenant();
  const grant = useSetWorkspaceMember(organization.id);
  const revoke = useRemoveWorkspaceMember(organization.id);
  const [error, setError] = useState<unknown>(null);

  const granted = member.workspaces.find((it) => it.workspace_id === workspace.id) ?? null;
  const busy = grant.isPending || revoke.isPending;

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div
        className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 ${
          granted ? "border border-line bg-surface" : "border border-dashed border-line-strong bg-surface"
        }`}
      >
        <span className={`font-mono text-[13px] ${granted ? "text-ink-4" : "text-line-strong"}`}>
          #
        </span>
        <span
          className={`min-w-0 grow truncate text-[13.5px] ${
            granted ? "font-medium" : "text-ink-4"
          }`}
        >
          {workspace.name}
        </span>

        {granted ? (
          <>
            {editable ? (
              <select
                value={granted.role}
                disabled={busy}
                aria-label={`Role on ${workspace.name}`}
                onChange={(event) =>
                  run(() =>
                    grant.mutateAsync({
                      workspaceId: workspace.id,
                      userId: member.user_id,
                      role: event.target.value as WorkspaceRole,
                    }),
                  )
                }
                className="h-6.5 rounded-md border border-line-strong bg-surface px-1.5 text-[12.5px] outline-none focus:border-accent disabled:opacity-50"
              >
                {WORKSPACE_ROLES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            ) : (
              <span className="text-[12.5px] text-ink-3">{granted.role}</span>
            )}
            {editable ? (
              <button
                type="button"
                disabled={busy}
                aria-label={`Revoke access to ${workspace.name}`}
                onClick={() =>
                  run(() =>
                    revoke.mutateAsync({ workspaceId: workspace.id, userId: member.user_id }),
                  )
                }
                className="text-ink-4 transition-colors hover:text-danger disabled:opacity-50"
              >
                <CloseIcon />
              </button>
            ) : null}
          </>
        ) : editable ? (
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              run(() =>
                grant.mutateAsync({
                  workspaceId: workspace.id,
                  userId: member.user_id,
                  role: DEFAULT_WORKSPACE_ROLE,
                }),
              )
            }
            className="shrink-0 text-[12.5px] font-medium text-accent transition-colors hover:text-accent-ink disabled:opacity-50"
          >
            {busy ? "Granting…" : "Grant access"}
          </button>
        ) : (
          <span className="text-[12.5px] text-ink-4">No access</span>
        )}
      </div>
      <ErrorNote error={error} />
    </div>
  );
}

// ─── Mảnh nhỏ ───────────────────────────────────────────────────────────────

function Avatar({ name, size = 30, dark = false }: { name: string; size?: number; dark?: boolean }) {
  return (
    <span
      style={{ width: size, height: size, fontSize: size >= 40 ? 13 : 11 }}
      className={`flex shrink-0 items-center justify-center rounded-full font-semibold ${
        dark ? "bg-ink text-white" : "bg-[#d9d9d4] text-[#4a4a46]"
      }`}
      aria-hidden
    >
      {initials(name)}
    </span>
  );
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

function SearchIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      className="shrink-0 text-ink-4"
      aria-hidden
    >
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.5 10.5L14 14" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      aria-hidden
    >
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      aria-hidden
    >
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M6 3l5 5-5 5" />
    </svg>
  );
}

function WarnIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 16 16"
      fill="none"
      stroke="var(--color-warn)"
      strokeWidth="1.5"
      strokeLinecap="round"
      className="mt-0.5 shrink-0"
      aria-hidden
    >
      <circle cx="8" cy="8" r="6" />
      <path d="M8 5.2v3.4M8 10.6v.2" />
    </svg>
  );
}
