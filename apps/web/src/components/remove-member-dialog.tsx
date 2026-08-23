"use client";

import { type FormEvent, useEffect, useState } from "react";
import { Button, Dialog, ErrorNote } from "@/components/ui";
import type { Member } from "@/lib/api";
import { useRemoveMember } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

/**
 * Bỏ một người khỏi organization, hoặc tự rời đi.
 *
 * Khác `DeleteWorkspaceDialog` ở chỗ không bắt gõ lại tên: thao tác này hoàn
 * tác được — thêm lại người đó là xong, dữ liệu họ đã tạo vẫn nằm trong
 * workspace. Cái mất là quyền, và câu hỏi chỉ cần nói đúng điều đó.
 */
export function RemoveMemberDialog({
  member,
  isSelf,
  onClose,
  onRemoved,
}: {
  member: Member;
  isSelf: boolean;
  onClose: () => void;
  onRemoved: () => void;
}) {
  const { organization } = useTenant();
  const [error, setError] = useState<unknown>(null);

  const remove = useRemoveMember(organization.id);
  const who = member.display_name ?? member.email ?? member.user_id;

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
      await remove.mutateAsync(member.user_id);
      onRemoved();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <Dialog
      labelledBy="remove-member-title"
      onDismiss={remove.isPending ? undefined : onClose}
      className="max-w-[460px]"
    >
      <form onSubmit={submit} className="flex flex-col gap-5 p-7">
        <div className="flex items-start gap-3.5">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-[9px] bg-danger-wash">
            <PersonOffIcon />
          </span>
          <div className="flex flex-col gap-1">
            <h2 id="remove-member-title" className="text-[17px] font-semibold tracking-tight">
              {isSelf ? `Leave ${organization.name}?` : `Remove ${who}?`}
            </h2>
            <p className="text-sm leading-relaxed text-ink-3">
              {isSelf
                ? `You lose your seat in ${organization.name} and every workspace in it. Someone still inside has to add you back.`
                : `They lose their seat in ${organization.name} and every workspace they were granted. Their account and the work they created stay where they are.`}
            </p>
          </div>
        </div>

        <ErrorNote error={error} />

        <div className="flex items-center justify-end gap-2.5">
          <Button type="button" variant="secondary" onClick={onClose} disabled={remove.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="danger" disabled={remove.isPending}>
            {remove.isPending
              ? isSelf
                ? "Leaving…"
                : "Removing…"
              : isSelf
                ? "Leave organisation"
                : "Remove member"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function PersonOffIcon() {
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
      <circle cx="6.5" cy="5" r="2.6" />
      <path d="M1.8 13.4a4.7 4.7 0 019.4 0M11.5 6.6h3" />
    </svg>
  );
}
