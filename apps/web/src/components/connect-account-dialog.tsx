"use client";

import { useEffect, useState } from "react";
import { ProviderTile } from "@/components/provider-icon";
import { Button, Dialog, ErrorNote } from "@/components/ui";
import type { Provider, SharingPolicy } from "@/lib/api";
import { useStartAuthorization } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

/**
 * Bước một của vòng OAuth: chọn phạm vi, rồi đi sang provider.
 *
 * Phạm vi được quyết ở đây và chỉ ở đây. Backend khoá nó lại sau lần uỷ quyền
 * đầu tiên — kết nối lại không đổi được `sharing` — nên hộp thoại nói thẳng
 * điều đó thay vì để người dùng phát hiện sau.
 *
 * Mặc định là `private`, khớp với mặc định của backend: chọn nhầm về phía riêng
 * tư thì chẳng ai mất gì, chọn nhầm về phía dùng chung là đem tài khoản cá nhân
 * chia cho cả workspace.
 */
export function ConnectAccountDialog({
  provider,
  canShareWithWorkspace,
  allowPrivate,
  reconnect,
  onClose,
}: {
  provider: Provider;
  /** `ws:admin` — chỉ họ mới tạo được tài khoản dùng chung. */
  canShareWithWorkspace: boolean;
  /** Org đang ở chế độ `open` hay không. */
  allowPrivate: boolean;
  /** Uỷ quyền lại một kết nối đã có: phạm vi giữ nguyên, không cho chọn. */
  reconnect?: { id: string; sharing: SharingPolicy };
  onClose: () => void;
}) {
  const { organization, workspace } = useTenant();
  const start = useStartAuthorization(organization.id, workspace?.id ?? null);
  const [error, setError] = useState<unknown>(null);

  // Mặc định rơi về thứ người này thực sự tạo được: trong org `managed` thì
  // riêng tư không phải lựa chọn, và người không phải quản trị viên workspace
  // thì dùng chung cũng không.
  const [sharing, setSharing] = useState<SharingPolicy>(
    reconnect?.sharing ?? (allowPrivate ? "private" : "workspace"),
  );

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !start.isPending) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, start.isPending]);

  async function go() {
    setError(null);
    try {
      const authorization = await start.mutateAsync({
        provider: provider.id,
        sharing,
        connectionId: reconnect?.id,
      });
      // Rời hẳn trang: đây là chuyển hướng sang tên miền của provider, không
      // phải điều hướng trong ứng dụng.
      window.location.href = authorization.authorize_url;
    } catch (caught) {
      setError(caught);
    }
  }

  const locked = reconnect !== undefined;

  return (
    <Dialog
      labelledBy="connect-account-title"
      onDismiss={start.isPending ? undefined : onClose}
      className="max-w-[540px]"
    >
      <div className="flex items-center gap-3 border-b border-line-soft p-6 pb-5">
        <ProviderTile provider={provider.id} box={36} />
        <div className="flex flex-col">
          <h2 id="connect-account-title" className="text-lg font-semibold tracking-tight">
            {locked ? `Reconnect ${provider.label}` : `Connect ${provider.label}`}
          </h2>
          <span className="text-[13px] text-ink-3">{workspace?.name ?? "This workspace"}</span>
        </div>
      </div>

      <div className="flex flex-col gap-4 p-6">
        {locked ? (
          <p className="text-sm leading-relaxed text-ink-3">
            {sharing === "private"
              ? "This stays your own account — reconnecting cannot turn it into a shared one."
              : "This stays a workspace account."}
          </p>
        ) : (
          <>
            <span className="text-[11px] font-semibold tracking-wider text-ink-4 uppercase">
              Who can use this account
            </span>
            <div className="flex flex-col gap-2.5">
              <ShareOption
                selected={sharing === "private"}
                disabled={!allowPrivate}
                onSelect={() => setSharing("private")}
                title="Only me"
                body="Nobody else here sees this account or knows it exists. It is used only when you are the one asking."
                note={
                  allowPrivate
                    ? undefined
                    : `${organization.name} allows shared accounts only.`
                }
              />
              <ShareOption
                selected={sharing === "workspace"}
                disabled={!canShareWithWorkspace}
                onSelect={() => setSharing("workspace")}
                title={`Everyone in ${workspace?.name ?? "this workspace"}`}
                body="A shared account for the workspace. Anyone here can use it in agent runs and in published workflows."
                note={
                  canShareWithWorkspace ? undefined : "Needs workspace admin — you do not have it."
                }
              />
            </div>
          </>
        )}

        <div className="flex flex-col gap-2">
          <span className="text-[11px] font-semibold tracking-wider text-ink-4 uppercase">
            {provider.label} will be asked for
          </span>
          <div className="flex flex-wrap gap-1.5">
            {provider.default_scopes.map((scope) => (
              <span
                key={scope}
                className="rounded border border-line bg-surface px-2 py-0.5 font-mono text-[11.5px] text-ink-2"
              >
                {scope}
              </span>
            ))}
          </div>
        </div>

        {!locked ? (
          <p className="text-[13px] text-ink-4">
            Sharing is fixed once connected. To change it later, disconnect and connect again.
          </p>
        ) : null}

        <ErrorNote error={error} />
      </div>

      <div className="flex items-center gap-3 border-t border-line-soft bg-canvas p-6 py-4">
        <p className="min-w-0 grow text-xs leading-relaxed text-ink-4">
          You approve this on {provider.label}. The token is stored encrypted and never shown to
          the agent.
        </p>
        <Button
          type="button"
          variant="secondary"
          onClick={onClose}
          disabled={start.isPending}
          className="h-9 shrink-0 px-3.5 text-sm whitespace-nowrap"
        >
          Cancel
        </Button>
        <Button
          type="button"
          onClick={go}
          disabled={start.isPending || (!allowPrivate && !canShareWithWorkspace)}
          className="h-9 shrink-0 px-3.5 text-sm whitespace-nowrap"
        >
          {start.isPending ? "Opening…" : `Continue to ${provider.label}`}
        </Button>
      </div>
    </Dialog>
  );
}

function ShareOption({
  selected,
  disabled,
  onSelect,
  title,
  body,
  note,
}: {
  selected: boolean;
  disabled: boolean;
  onSelect: () => void;
  title: string;
  body: string;
  note?: string;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      disabled={disabled}
      aria-pressed={selected}
      className={`flex items-start gap-3 rounded-lg border p-3.5 text-left transition-colors ${
        selected
          ? "border-accent bg-accent-wash"
          : "border-line bg-surface hover:border-line-strong"
      } ${disabled ? "cursor-not-allowed opacity-55 hover:border-line" : ""}`}
    >
      <span
        aria-hidden
        className={`mt-0.5 h-4 w-4 shrink-0 rounded-full bg-surface ${
          selected ? "border-[5px] border-accent" : "border-[1.5px] border-line-strong"
        }`}
      />
      <span className="flex flex-col gap-1">
        <span className="text-sm font-medium">{title}</span>
        <span className="text-[13px] leading-relaxed text-ink-3">{body}</span>
        {note ? <span className="text-xs text-ink-4">{note}</span> : null}
      </span>
    </button>
  );
}
