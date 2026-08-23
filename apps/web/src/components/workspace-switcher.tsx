"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { NewWorkspaceDialog } from "@/components/new-workspace-dialog";
import { useTenant } from "@/lib/tenant";

/**
 * Đổi workspace, và là nơi tạo workspace mới.
 *
 * Nút tạo nằm ở đây chứ không chỉ trong Settings: lúc người ta muốn thêm một
 * workspace là lúc họ đang đứng ở cái đang mở. Đổi tên và xoá thì ngược lại —
 * hiếm, và cần nhìn thấy toàn cảnh — nên chúng ở Settings, tới qua chính menu
 * này.
 */
export function WorkspaceSwitcher() {
  const router = useRouter();
  const { organization, workspaces, workspace, selectWorkspace } = useTenant();
  const [open, setOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={root} className="relative px-3 pt-3 pb-1.5">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((it) => !it)}
        className={`flex h-9 w-full items-center gap-2 rounded-md bg-surface px-3 text-left transition-colors ${
          open ? "border-[1.5px] border-accent" : "border border-line hover:border-line-strong"
        }`}
      >
        <span className="font-mono text-[13px] text-ink-4">#</span>
        <span className="grow truncate text-sm font-medium">
          {workspace?.name ?? "No workspaces"}
        </span>
        <Chevron direction={open ? "up" : "down"} />
      </button>

      {open ? (
        <div
          role="menu"
          className="absolute top-[52px] right-3 left-3 z-40 overflow-hidden rounded-[9px] border border-line-strong bg-surface shadow-lg"
        >
          <div className="px-3 pt-2.5 pb-1">
            <span className="text-[10.5px] font-semibold tracking-wider text-ink-4 uppercase">
              Workspaces in {organization.name}
            </span>
          </div>

          <div className="flex flex-col gap-px px-1.5 pb-1.5">
            {workspaces.length === 0 ? (
              <span className="px-2 py-1.5 text-[13px] text-ink-4">Nothing here yet.</span>
            ) : null}
            {workspaces.map((it) => {
              const active = it.id === workspace?.id;
              return (
                <button
                  key={it.id}
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    selectWorkspace(it.id);
                    setOpen(false);
                  }}
                  className={`flex items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors ${
                    active ? "bg-accent-wash" : "hover:bg-sunken"
                  }`}
                >
                  <span className={`font-mono text-[13px] ${active ? "text-ink-4" : "text-line-strong"}`}>
                    #
                  </span>
                  <span className={`grow truncate text-[13px] ${active ? "font-medium" : "text-ink-2"}`}>
                    {it.name}
                  </span>
                  {active ? <TickIcon /> : null}
                </button>
              );
            })}
          </div>

          <div className="h-px bg-line-soft" />

          <div className="flex flex-col gap-px p-1.5">
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                setCreating(true);
              }}
              className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-sunken"
            >
              <PlusIcon />
              <span className="text-[13px] font-medium text-accent">New workspace</span>
            </button>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                router.push("/settings/workspaces");
              }}
              className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-sunken"
            >
              <GearIcon />
              <span className="text-[13px] text-ink-2">Manage workspaces</span>
            </button>
          </div>

          <p className="border-t border-line-soft bg-canvas px-3 py-2 text-[11.5px] text-ink-4">
            Only workspaces you can reach are listed.
          </p>
        </div>
      ) : null}

      {creating ? (
        <NewWorkspaceDialog
          onClose={() => setCreating(false)}
          onCreated={(workspaceId) => {
            setCreating(false);
            selectWorkspace(workspaceId);
          }}
        />
      ) : null}
    </div>
  );
}

// ─── Icon, vẽ inline theo lưới 16px ─────────────────────────────────────────

function Chevron({ direction }: { direction: "up" | "down" }) {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="text-ink-4"
      aria-hidden
    >
      <path d={direction === "up" ? "M4 10l4-4 4 4" : "M4 6l4 4 4-4"} />
    </svg>
  );
}

function TickIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="text-accent"
      aria-hidden
    >
      <path d="M3.4 8.4l3 3 6.2-6.4" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      className="text-accent"
      aria-hidden
    >
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="text-ink-3"
      aria-hidden
    >
      <circle cx="8" cy="8" r="2.4" />
      <path d="M8 1.9v1.6M8 12.5v1.6M1.9 8h1.6M12.5 8h1.6M3.7 3.7l1.1 1.1M11.2 11.2l1.1 1.1M12.3 3.7l-1.1 1.1M4.8 11.2l-1.1 1.1" />
    </svg>
  );
}
