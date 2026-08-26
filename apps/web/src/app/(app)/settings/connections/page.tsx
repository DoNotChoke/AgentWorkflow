"use client";

import { useState } from "react";
import { ProviderTile } from "@/components/provider-icon";
import { Button, ErrorNote, Spinner } from "@/components/ui";
import type { ConnectionAudit, ConnectionPolicy } from "@/lib/api";
import {
  useConnectionAudit,
  useConnectionPolicy,
  useConnections,
  useMembers,
  useRevokeConnection,
  useSetConnectionPolicy,
} from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

/**
 * Settings → Connections: chính sách của organization, và mọi tài khoản.
 *
 * Hai tab vì chúng trả lời hai câu khác nhau. "Policy" quyết định người dùng
 * được thêm loại tài khoản nào; "All accounts" là câu trả lời cho hệ quả mà
 * chính sách mở tạo ra — nếu không ai thấy tài khoản riêng của người khác thì
 * ai gỡ nó khi người đó nghỉ việc.
 */
export default function ConnectionSettingsPage() {
  const { organization } = useTenant();
  const [tab, setTab] = useState<"policy" | "accounts">("policy");

  const isOwner = organization.role === "owner";
  const isAdmin = organization.role === "owner" || organization.role === "admin";

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center gap-4 border-b border-line px-8">
        <span className="text-base font-semibold">Connections</span>
        <nav className="ml-1 flex items-center gap-1">
          <TabButton active={tab === "policy"} onClick={() => setTab("policy")}>
            Policy
          </TabButton>
          {isAdmin ? (
            <TabButton active={tab === "accounts"} onClick={() => setTab("accounts")}>
              All accounts
            </TabButton>
          ) : null}
        </nav>
      </header>

      <div className="overflow-y-auto p-8">
        {tab === "policy" ? <PolicyTab canEdit={isOwner} /> : <AccountsTab enabled={isAdmin} />}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-md px-2.5 py-1.5 text-sm transition-colors ${
        active ? "bg-sunken font-medium text-ink" : "text-ink-2 hover:bg-sunken"
      }`}
    >
      {children}
    </button>
  );
}

// ─── Tab 1: chính sách ───────────────────────────────────────────────────────

function PolicyTab({ canEdit }: { canEdit: boolean }) {
  const { organization, workspace } = useTenant();
  const policy = useConnectionPolicy(organization.id);
  const save = useSetConnectionPolicy(organization.id);
  const connections = useConnections(organization.id, workspace?.id ?? null);

  const current = policy.data?.connection_policy ?? null;
  const [choice, setChoice] = useState<ConnectionPolicy | null>(null);
  const selected = choice ?? current;
  const dirty = selected !== null && current !== null && selected !== current;

  // Chỉ đếm được kết nối riêng trong workspace đang mở — backend chưa có
  // endpoint đếm toàn org. Con số vẫn đáng hiện: nó biến một cảnh báo trừu
  // tượng thành một hệ quả cụ thể.
  const privateHere = (connections.data ?? []).filter(
    (row) => row.sharing === "private" && row.status !== "revoked",
  ).length;

  if (policy.isPending) return <Spinner label="Loading policy…" />;

  return (
    <div className="flex max-w-5xl flex-col gap-6">
      <div className="flex flex-col gap-1.5">
        <h2 className="text-xl font-semibold tracking-tight">Who can add accounts</h2>
        <p className="max-w-3xl text-sm leading-relaxed text-ink-3">
          This decides whether people in {organization.name} may connect their own accounts, or
          only use the shared ones an administrator sets up.
        </p>
      </div>

      {/* Hai lựa chọn cạnh nhau, không xếp chồng: đây là một phép so sánh, và
          đọc hai cột song song nhanh hơn đọc một cột rồi cuộn lại. */}
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <PolicyOption
          selected={selected === "open"}
          disabled={!canEdit}
          onSelect={() => setChoice("open")}
          title="Open"
          body="Members connect their own accounts, visible only to them. Administrators still control the shared workspace accounts."
        />
        <PolicyOption
          selected={selected === "managed"}
          disabled={!canEdit}
          onSelect={() => setChoice("managed")}
          title="Managed"
          body="Only shared accounts. Administrators set up every account; members use what is provided and cannot connect their own."
        >
          {selected === "managed" && current === "open" ? (
            <p className="mt-2 rounded-md border border-warn-line bg-warn-wash px-3 py-2.5 text-[13px] leading-relaxed text-ink-2">
              Switching pauses every private account in {organization.name}
              {privateHere > 0 ? ` — ${privateHere} in ${workspace?.name}` : ""}. Their tokens are
              kept, so opening the policy again lets each owner turn theirs back on.
            </p>
          ) : null}
        </PolicyOption>
      </div>

      <ErrorNote error={save.error} />

      {save.data && save.data.disabled_private_connections > 0 ? (
        <p className="rounded-md border border-line bg-ok-wash px-3.5 py-2.5 text-sm text-ok">
          Policy saved. {save.data.disabled_private_connections} private{" "}
          {save.data.disabled_private_connections === 1 ? "account was" : "accounts were"} paused.
        </p>
      ) : null}

      <div className="flex items-center gap-3">
        <Button
          type="button"
          disabled={!dirty || save.isPending}
          onClick={() => selected && save.mutate(selected)}
          className="h-9 shrink-0 px-3.5 text-sm whitespace-nowrap"
        >
          {save.isPending
            ? "Saving…"
            : selected === "managed"
              ? "Switch to managed"
              : "Switch to open"}
        </Button>
        {dirty ? (
          <Button
            type="button"
            variant="secondary"
            onClick={() => setChoice(null)}
            disabled={save.isPending}
            className="h-9 shrink-0 px-3.5 text-sm whitespace-nowrap"
          >
            Cancel
          </Button>
        ) : null}
        <p className="min-w-0 text-[13px] leading-relaxed text-ink-4">
          {canEdit
            ? "Administrators can revoke an account but not change this policy."
            : "Only the organisation owner can change this."}
        </p>
      </div>
    </div>
  );
}

function PolicyOption({
  selected,
  disabled,
  onSelect,
  title,
  body,
  children,
}: {
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
  title: string;
  body: string;
  children?: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={selected}
      className={`flex h-full items-start gap-3.5 rounded-xl border p-5 text-left transition-colors ${
        selected ? "border-accent bg-accent-wash" : "border-line bg-surface"
      } ${disabled ? "cursor-not-allowed opacity-60" : "hover:border-line-strong"}`}
    >
      <span
        aria-hidden
        className={`mt-0.5 h-[18px] w-[18px] shrink-0 rounded-full bg-surface ${
          selected ? "border-[5.5px] border-accent" : "border-[1.5px] border-line-strong"
        }`}
      />
      <span className="flex grow flex-col gap-1.5">
        <span className="text-[15px] font-medium">{title}</span>
        <span className="text-sm leading-relaxed text-ink-3">{body}</span>
        {children}
      </span>
    </button>
  );
}

// ─── Tab 2: mọi tài khoản ────────────────────────────────────────────────────

/**
 * Kết nối riêng hiện ra ở đây, nhưng chỉ ở mức **có tồn tại**.
 *
 * Không tên tài khoản, không scope, không nút dùng — chỉ chủ sở hữu và một nút
 * thu hồi. Đó là đủ để dọn khi có người nghỉ việc, và không hơn.
 */
function AccountsTab({ enabled }: { enabled: boolean }) {
  const { organization, workspace } = useTenant();
  const workspaceId = workspace?.id ?? null;
  const audit = useConnectionAudit(organization.id, workspaceId, enabled);
  const members = useMembers(organization.id);
  const revoke = useRevokeConnection(organization.id, workspaceId);

  const nameOf = (userId: string) =>
    members.data?.find((member) => member.user_id === userId)?.display_name ??
    members.data?.find((member) => member.user_id === userId)?.email ??
    userId;

  if (!enabled) return <Spinner label="Administrators only." />;
  if (audit.isPending) return <Spinner label="Loading accounts…" />;

  const rows = audit.data ?? [];

  return (
    // Không giới hạn bề rộng: đây là một bảng sáu cột, và ép nó vào nửa màn
    // hình chỉ làm mọi ô bị cắt cụt mà không đổi lại được gì.
    <div className="flex flex-col gap-5">
      <div className="flex items-start gap-3 rounded-lg border border-line bg-canvas p-4">
        <IconLock />
        <p className="max-w-4xl text-sm leading-relaxed text-ink-3">
          Private accounts appear here so you can revoke one when someone leaves. You cannot see
          which address a private account points to, open it, or use it — the owner is the only
          person who can.
        </p>
      </div>

      <ErrorNote error={audit.error} />

      <div className="overflow-hidden rounded-[9px] border border-line">
        <div className={`${AUDIT_GRID} border-b border-line bg-canvas px-4 py-3`}>
          <span />
          <HeadCell>Account</HeadCell>
          <HeadCell>Owner</HeadCell>
          <HeadCell>Who can use</HeadCell>
          <HeadCell>Status</HeadCell>
          <span />
        </div>

        {rows.map((row, index) => (
          <AuditRow
            key={row.id}
            row={row}
            owner={nameOf(row.owner_user_id)}
            busy={revoke.isPending}
            last={index === rows.length - 1}
            onRevoke={() => revoke.mutate(row.id)}
          />
        ))}

        {rows.length === 0 ? (
          <p className="px-4 py-8 text-sm text-ink-4">
            No account connected in {workspace?.name} yet.
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** Một khai báo cột duy nhất cho cả hàng tiêu đề lẫn hàng dữ liệu. */
const AUDIT_GRID =
  "grid grid-cols-[46px_minmax(0,1.6fr)_minmax(0,1.2fr)_150px_130px_104px] items-center gap-4";

function HeadCell({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[11.5px] font-semibold tracking-wide text-ink-4 uppercase">
      {children}
    </span>
  );
}

function AuditRow({
  row,
  owner,
  busy,
  last,
  onRevoke,
}: {
  row: ConnectionAudit;
  owner: string;
  busy: boolean;
  last: boolean;
  onRevoke: () => void;
}) {
  const isPrivate = row.sharing === "private";
  return (
    <div
      className={`${AUDIT_GRID} px-4 py-3.5 ${last ? "" : "border-b border-line-soft"}`}
    >
      <ProviderTile provider={row.provider} box={30} />

      {isPrivate ? (
        <span className="flex items-center gap-1.5 text-[13.5px] text-ink-4 italic">
          <IconLock />
          Private account
        </span>
      ) : (
        <span className="text-[13.5px] font-medium capitalize">{row.provider}</span>
      )}

      <span className="truncate text-[13.5px]">{owner}</span>

      <span
        className={`w-fit rounded px-2 py-0.5 text-[11.5px] font-medium ${
          isPrivate
            ? "border border-accent-line bg-accent-wash text-accent"
            : "bg-sunken text-ink-3"
        }`}
      >
        {isPrivate ? "Owner only" : "Workspace"}
      </span>

      <span className="text-[13px] text-ink-3 capitalize">{row.status.replace("_", " ")}</span>

      {row.status === "revoked" ? (
        <span className="text-[13px] text-ink-4">Revoked</span>
      ) : (
        <Button
          type="button"
          variant="secondary"
          disabled={busy}
          onClick={onRevoke}
          className="h-8 border-danger-line px-3 text-xs text-danger"
        >
          Revoke
        </Button>
      )}
    </div>
  );
}

function IconLock() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 16 16"
      fill="none"
      className="mt-0.5 shrink-0 text-accent"
      aria-hidden
    >
      <rect
        x="3.5"
        y="7"
        width="9"
        height="6.5"
        rx="1.5"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <path
        d="M5.75 7V5a2.25 2.25 0 0 1 4.5 0v2"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}
