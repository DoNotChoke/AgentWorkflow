"use client";

import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { ConnectAccountDialog } from "@/components/connect-account-dialog";
import { ProviderTile } from "@/components/provider-icon";
import { Button, ErrorNote, Spinner } from "@/components/ui";
import type { Connection, ConnectionStatus, Provider, SharingPolicy } from "@/lib/api";
import { CONNECTION_STATUS_LABEL, connectionStatusTone } from "@/lib/connections";
import {
  useConnectionPolicy,
  useConnections,
  useDisableConnection,
  useEnableConnection,
  useProviders,
  useRevokeConnection,
} from "@/lib/queries";
import { reachesEveryWorkspace } from "@/lib/roles";
import { useSession } from "@/lib/session";
import { useTenant } from "@/lib/tenant";

/**
 * Tài khoản provider của workspace đang mở.
 *
 * Hai mục, không phải một danh sách: tài khoản dùng chung và tài khoản riêng
 * trả lời hai câu hỏi khác nhau ("workspace này chạy được gì" và "tôi đã nối gì
 * vào đây"), và trộn chúng lại thì không ai đọc ra được cái nào là cái nào.
 *
 * Kết nối riêng của người khác **không** xuất hiện ở đây dưới dạng mờ hay khoá:
 * backend lọc chúng ngay trong câu truy vấn, nên chúng vắng mặt hoàn toàn.
 */
export default function ConnectionsPage() {
  const { organization, workspace } = useTenant();
  const { user } = useSession();
  const params = useSearchParams();

  const providers = useProviders();
  const policy = useConnectionPolicy(organization.id);
  const connections = useConnections(organization.id, workspace?.id ?? null);

  const [connecting, setConnecting] = useState<Provider | null>(null);
  const [reconnecting, setReconnecting] = useState<Connection | null>(null);

  // Quyền workspace không có trong danh sách workspace, nên suy từ vai trò org
  // — đúng như `resolve_workspace_role` ở backend.
  const canShareWithWorkspace = reachesEveryWorkspace(organization.role);
  const allowPrivate = policy.data?.connection_policy !== "managed";

  const { shared, mine } = useMemo(() => {
    const rows = connections.data ?? [];
    return {
      shared: rows.filter((row) => row.sharing === "workspace"),
      mine: rows.filter((row) => row.sharing === "private"),
    };
  }, [connections.data]);

  const callback = params.get("error") ?? (params.get("status") === "connected" ? "ok" : null);

  if (!workspace) return <Spinner label="No workspace in this organisation." />;

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center border-b border-line px-8">
        <span className="text-base font-semibold">Connections</span>
        <div className="grow" />
        {allowPrivate || canShareWithWorkspace ? (
          <Button
            type="button"
            onClick={() => setConnecting(providers.data?.find((it) => it.configured) ?? null)}
            disabled={!providers.data?.some((it) => it.configured)}
            className="h-9 px-3.5 text-sm"
          >
            Add account
          </Button>
        ) : null}
      </header>

      <div className="flex flex-col gap-6 overflow-y-auto p-8">
        {callback ? <CallbackNote code={callback} /> : null}

        <p className="max-w-2xl text-sm leading-relaxed text-ink-3">
          Accounts live in the <strong className="font-medium text-ink">{workspace.name}</strong>{" "}
          workspace. Tokens are stored encrypted and never reach the agent — it only ever receives
          a reference.
        </p>

        {!allowPrivate ? <ManagedNote organization={organization.name} /> : null}

        <ErrorNote error={connections.error} />

        {connections.isPending ? (
          <span className="text-sm text-ink-4">Loading accounts…</span>
        ) : (
          <>
            <Section
              title="Workspace accounts"
              hint={`Anyone in ${workspace.name} can use these`}
              empty="No shared account yet."
            >
              {shared.map((row) => (
                <ConnectionRow
                  key={row.id}
                  connection={row}
                  isOwner={row.owner_user_id === user?.id}
                  canManage={canShareWithWorkspace}
                  onReconnect={() => setReconnecting(row)}
                />
              ))}
            </Section>

            {allowPrivate || mine.length > 0 ? (
              <Section
                title="Your accounts"
                hint="Nobody else in the workspace sees these"
                empty="You have not connected an account of your own."
              >
                {mine.map((row) => (
                  <ConnectionRow
                    key={row.id}
                    connection={row}
                    isOwner
                    canManage
                    onReconnect={() => setReconnecting(row)}
                  />
                ))}
              </Section>
            ) : null}

            {allowPrivate || canShareWithWorkspace ? (
              <AddSection providers={providers.data ?? []} onPick={setConnecting} />
            ) : null}
          </>
        )}
      </div>

      {connecting ? (
        <ConnectAccountDialog
          provider={connecting}
          canShareWithWorkspace={canShareWithWorkspace}
          allowPrivate={allowPrivate}
          onClose={() => setConnecting(null)}
        />
      ) : null}

      {reconnecting && providers.data ? (
        <ConnectAccountDialog
          provider={
            providers.data.find((it) => it.id === reconnecting.provider) ?? providers.data[0]
          }
          canShareWithWorkspace={canShareWithWorkspace}
          allowPrivate={allowPrivate}
          reconnect={{ id: reconnecting.id, sharing: reconnecting.sharing }}
          onClose={() => setReconnecting(null)}
        />
      ) : null}
    </div>
  );
}

/**
 * Kết quả của vòng OAuth, đọc từ query string mà callback chuyển hướng về.
 *
 * Backend chỉ trả về phân loại lỗi (§37), không trả thông điệp — thông điệp có
 * thể mang chi tiết của provider, và query string thì đi thẳng vào log.
 */
function CallbackNote({ code }: { code: string }) {
  if (code === "ok") {
    return (
      <p className="rounded-md border border-line bg-ok-wash px-3.5 py-2.5 text-sm text-ok">
        Account connected.
      </p>
    );
  }
  const message =
    code === "access_denied"
      ? "You cancelled before approving. Nothing was connected."
      : code === "invalid_callback"
        ? "That connection link was incomplete. Start again from this page."
        : "The connection did not complete. Start again from this page.";
  return (
    <p className="rounded-md border border-warn-line bg-warn-wash px-3.5 py-2.5 text-sm text-warn">
      {message}
    </p>
  );
}

function ManagedNote({ organization }: { organization: string }) {
  return (
    <div className="flex items-start gap-3 rounded-lg border border-warn-line bg-warn-wash p-3.5">
      <IconShield />
      <div className="flex flex-col gap-0.5">
        <span className="text-sm font-medium">{organization} allows shared accounts only</span>
        <span className="max-w-xl text-[13px] leading-relaxed text-ink-3">
          An administrator sets up the accounts this workspace can use. You cannot connect your own
          account in this organisation.
        </span>
      </div>
    </div>
  );
}

function Section({
  title,
  hint,
  empty,
  children,
}: {
  title: string;
  hint: string;
  empty: string;
  children: React.ReactNode[];
}) {
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-baseline gap-2.5">
        <span className="text-[11px] font-semibold tracking-wider text-ink-4 uppercase">
          {title}
        </span>
        <span className="text-[13px] text-ink-4">{hint}</span>
      </div>
      {children.length > 0 ? (
        <div className="flex flex-col gap-2">{children}</div>
      ) : (
        <p className="rounded-lg border border-dashed border-line-strong px-4 py-3.5 text-[13px] text-ink-4">
          {empty}
        </p>
      )}
    </section>
  );
}

function ConnectionRow({
  connection,
  isOwner,
  canManage,
  onReconnect,
}: {
  connection: Connection;
  isOwner: boolean;
  canManage: boolean;
  onReconnect: () => void;
}) {
  const { organization, workspace } = useTenant();
  const workspaceId = workspace?.id ?? null;
  const disable = useDisableConnection(organization.id, workspaceId);
  const enable = useEnableConnection(organization.id, workspaceId);
  const revoke = useRevokeConnection(organization.id, workspaceId);

  const busy = disable.isPending || enable.isPending || revoke.isPending;
  const broken =
    connection.status === "expired" ||
    connection.status === "reconnect_required" ||
    connection.status === "missing_scope";

  return (
    <div className="flex items-center gap-3.5 rounded-lg border border-line bg-surface px-4 py-3.5">
      <ProviderTile provider={connection.provider} />
      <div className="flex min-w-0 grow flex-col">
        <span className="truncate text-sm font-medium">{connection.display_name}</span>
        <span className="truncate text-[13px] text-ink-3">
          {[
            connection.external_account_label,
            `${connection.granted_scopes.length} scopes`,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </div>

      <SharingBadge sharing={connection.sharing} isOwner={isOwner} />
      <StatusBadge status={connection.status} />

      {broken && (isOwner || canManage) ? (
        <Button
          type="button"
          variant="secondary"
          onClick={onReconnect}
          className="h-7 shrink-0 px-2.5 text-xs"
        >
          Reconnect
        </Button>
      ) : null}

      {connection.status !== "revoked" && (isOwner || canManage) ? (
        <>
          <Button
            type="button"
            variant="secondary"
            disabled={busy}
            onClick={() =>
              connection.status === "disabled"
                ? enable.mutate(connection.id)
                : disable.mutate(connection.id)
            }
            className="h-7 shrink-0 px-2.5 text-xs"
          >
            {connection.status === "disabled" ? "Resume" : "Pause"}
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={busy}
            onClick={() => revoke.mutate(connection.id)}
            className="h-7 shrink-0 border-danger-line px-2.5 text-xs text-danger"
          >
            Disconnect
          </Button>
        </>
      ) : null}
    </div>
  );
}

function SharingBadge({ sharing, isOwner }: { sharing: SharingPolicy; isOwner: boolean }) {
  if (sharing === "workspace") {
    return (
      <span className="flex shrink-0 items-center gap-1.5 rounded border border-transparent bg-sunken px-2 py-0.5 text-[11.5px] font-medium text-ink-3">
        Workspace
      </span>
    );
  }
  return (
    <span className="flex shrink-0 items-center gap-1.5 rounded border border-accent-line bg-accent-wash px-2 py-0.5 text-[11.5px] font-medium text-accent">
      <IconLock />
      {isOwner ? "Only you" : "Private"}
    </span>
  );
}

function StatusBadge({ status }: { status: ConnectionStatus }) {
  const tone = connectionStatusTone(status);
  return (
    <span
      className={`flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ${tone.badge}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${tone.dot}`} />
      {CONNECTION_STATUS_LABEL[status]}
    </span>
  );
}

function AddSection({
  providers,
  onPick,
}: {
  providers: Provider[];
  onPick: (provider: Provider) => void;
}) {
  if (providers.length === 0) return null;
  return (
    // Đường kẻ ngang, không phải thêm một khoảng trắng nữa: phía trên là những
    // tài khoản đã có, phía dưới là chỗ thêm cái mới. Hai việc khác nhau, và
    // `gap-6` giữa các mục ở trên không đủ để nói ra điều đó.
    <section className="mt-1 flex flex-col gap-3 border-t border-line pt-7">
      <div className="flex items-baseline gap-2.5">
        <span className="text-[11px] font-semibold tracking-wider text-ink-4 uppercase">
          Add an account
        </span>
        <span className="text-[13px] text-ink-4">Sign in to the provider to connect it</span>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {providers.map((provider) => (
          <button
            key={provider.id}
            type="button"
            disabled={!provider.configured}
            onClick={() => onPick(provider)}
            title={provider.configured ? provider.notes : undefined}
            className="flex items-center gap-3 rounded-lg border border-line bg-surface px-3.5 py-3 text-left transition-colors enabled:hover:border-line-strong disabled:opacity-55"
          >
            <ProviderTile provider={provider.id} box={30} />
            <span className="grow text-sm font-medium">{provider.label}</span>
            <span className="text-[13px] font-medium text-accent">
              {provider.configured ? "Connect" : "Not set up"}
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

function IconLock() {
  return (
    <svg width="11" height="11" viewBox="0 0 16 16" fill="none" aria-hidden>
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

function IconShield() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 16 16"
      fill="none"
      className="mt-0.5 shrink-0 text-warn"
      aria-hidden
    >
      <path
        d="M8 1.8l5 1.9v4.1c0 3.1-2.1 5.4-5 6.4-2.9-1-5-3.3-5-6.4V3.7z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path
        d="M5.9 8.1l1.5 1.5 2.8-3"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
