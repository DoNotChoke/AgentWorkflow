"use client";

import { type FormEvent, useEffect, useState } from "react";
import { Button, Dialog, ErrorNote } from "@/components/ui";
import type { Workspace } from "@/lib/api";
import { useDeleteWorkspace } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

/**
 * Xoá một workspace.
 *
 * Thao tác không hoàn tác được duy nhất trong sản phẩm, nên nó bắt gõ lại tên:
 * một cú bấm nhầm không đủ để mất cả lịch sử run. Backend vẫn là nơi chặn thật
 * — kể cả workspace cuối cùng — ở đây chỉ là không dẫn người dùng vào chỗ chắc
 * chắn bị từ chối.
 */
export function DeleteWorkspaceDialog({
  workspace,
  onClose,
  onDeleted,
}: {
  workspace: Workspace;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const { organization, workspaces } = useTenant();
  const [typed, setTyped] = useState("");
  const [error, setError] = useState<unknown>(null);

  const remove = useDeleteWorkspace(organization.id);
  const isLast = workspaces.length <= 1;
  const matches = typed.trim() === workspace.name;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !remove.isPending) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, remove.isPending]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await remove.mutateAsync(workspace.id);
      onDeleted();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <Dialog
      labelledBy="delete-workspace-title"
      onDismiss={remove.isPending ? undefined : onClose}
      className="max-w-[460px]"
    >
      <form onSubmit={submit} className="flex flex-col gap-5 p-7">
        <div className="flex items-start gap-3.5">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-[9px] bg-danger-wash">
            <TrashIcon />
          </span>
          <div className="flex flex-col gap-1">
            <h2 id="delete-workspace-title" className="text-[17px] font-semibold tracking-tight">
              Delete {workspace.name}?
            </h2>
            <p className="text-sm leading-relaxed text-ink-3">
              This cannot be undone. Its connections, workflows and run history go with it.
            </p>
          </div>
        </div>

        {isLast ? (
          <p className="rounded-md border border-warn-line bg-warn-wash px-3.5 py-2.5 text-[13px] leading-relaxed text-ink-2">
            This is the only workspace in {organization.name}. Create another one before deleting
            it — an organisation always keeps at least one.
          </p>
        ) : (
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] text-ink-2">
              Type <span className="font-mono font-semibold">{workspace.name}</span> to confirm
            </span>
            <input
              autoFocus
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              className="h-11 rounded-control border border-line-strong bg-surface px-3.5 font-mono text-[15px] outline-none focus:border-accent"
            />
          </label>
        )}

        <ErrorNote error={error} />

        <div className="flex items-center justify-end gap-2.5">
          <Button type="button" variant="secondary" onClick={onClose} disabled={remove.isPending}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="danger"
            disabled={remove.isPending || isLast || !matches}
          >
            {remove.isPending ? "Deleting…" : "Delete workspace"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function TrashIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 16 16"
      fill="none"
      stroke="var(--color-danger)"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M2.5 4.5h11M6 4.5V3a1 1 0 011-1h2a1 1 0 011 1v1.5M4 4.5l.6 8.2a1 1 0 001 .8h4.8a1 1 0 001-.8l.6-8.2" />
    </svg>
  );
}
