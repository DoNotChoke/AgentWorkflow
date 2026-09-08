"use client";

import { type FormEvent, useEffect, useState } from "react";
import { Button, Dialog, ErrorNote } from "@/components/ui";
import type { Agent } from "@/lib/api";
import { useArchiveAgent } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

/**
 * Lưu trữ ("xoá mềm") một agent.
 *
 * Không bắt gõ lại tên như xoá workspace: lưu trữ đảo ngược được — hội thoại
 * cũ vẫn đọc lại được, và agent hiện lại được ở khu "Archived". Một câu xác
 * nhận là đủ. Backend vẫn là nơi chặn thật (người tạo hoặc quản trị workspace).
 */
export function ArchiveAgentDialog({
  agent,
  onClose,
  onArchived,
}: {
  agent: Agent;
  onClose: () => void;
  onArchived: () => void;
}) {
  const { organization, workspace } = useTenant();
  const archive = useArchiveAgent(organization.id, workspace?.id ?? null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !archive.isPending) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, archive.isPending]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await archive.mutateAsync(agent.id);
      onArchived();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <Dialog
      labelledBy="archive-agent-title"
      onDismiss={archive.isPending ? undefined : onClose}
      className="max-w-[440px]"
    >
      <form onSubmit={submit} className="flex flex-col gap-5 p-7">
        <div className="flex items-start gap-3.5">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-[9px] bg-danger-wash">
            <TrashIcon />
          </span>
          <div className="flex flex-col gap-1">
            <h2 id="archive-agent-title" className="text-[17px] font-semibold tracking-tight">
              Archive {agent.name}?
            </h2>
            <p className="text-sm leading-relaxed text-ink-3">
              It leaves the list and can’t start new conversations. Past conversations stay
              readable, and you can restore it any time from Archived.
            </p>
          </div>
        </div>

        <ErrorNote error={error} />

        <div className="flex items-center justify-end gap-2.5">
          <Button type="button" variant="secondary" onClick={onClose} disabled={archive.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="danger" disabled={archive.isPending}>
            {archive.isPending ? "Archiving…" : "Archive agent"}
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
