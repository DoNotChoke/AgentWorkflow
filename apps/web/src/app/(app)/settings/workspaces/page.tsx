"use client";

import { type FormEvent, useState } from "react";
import { DeleteWorkspaceDialog } from "@/components/delete-workspace-dialog";
import { NewWorkspaceDialog } from "@/components/new-workspace-dialog";
import { Button, ErrorNote } from "@/components/ui";
import type { Workspace } from "@/lib/api";
import { useRenameWorkspace } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

/**
 * Quản lý workspace của organization đang mở.
 *
 * Bảng bên trái để nhìn toàn cảnh, panel bên phải để sửa đúng một cái. Đổi tên
 * và đổi địa chỉ nằm cạnh nhau nhưng là hai quyết định khác nhau, nên panel nói
 * rõ cái nào an toàn và cái nào phá link người khác đã lưu.
 */
export default function WorkspacesSettingsPage() {
  const { workspaces } = useTenant();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const selected = workspaces.find((it) => it.id === selectedId) ?? null;

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 grow flex-col">
        <header className="flex h-16 shrink-0 items-center border-b border-line px-7">
          <span className="text-base font-semibold">Workspaces</span>
          <span className="ml-2 text-sm text-ink-4">{workspaces.length}</span>
          <div className="grow" />
          <Button className="h-9 px-3 text-sm" onClick={() => setCreating(true)}>
            <PlusIcon />
            New workspace
          </Button>
        </header>

        <div className="flex flex-col gap-4 overflow-auto p-7">
          <p className="max-w-[640px] text-sm leading-relaxed text-ink-3">
            Connections, workflows and runs all live inside a workspace. A member reaches one only
            once they are given a role on it — owners and admins reach every workspace already.
          </p>

          <div className="overflow-hidden rounded-[9px] border border-line">
            <div className="grid grid-cols-[minmax(0,2fr)_minmax(0,1.6fr)_110px] gap-4 border-b border-line bg-canvas px-4 py-2.5 text-[11.5px] font-semibold tracking-wide text-ink-4 uppercase">
              <span>Workspace</span>
              <span>Address</span>
              <span>Created</span>
            </div>

            {workspaces.map((workspace, index) => {
              const active = workspace.id === selectedId;
              return (
                <button
                  key={workspace.id}
                  type="button"
                  onClick={() => setSelectedId(workspace.id)}
                  className={`grid w-full grid-cols-[minmax(0,2fr)_minmax(0,1.6fr)_110px] items-center gap-4 px-4 py-3 text-left transition-colors ${
                    index < workspaces.length - 1 ? "border-b border-line-soft" : ""
                  } ${active ? "bg-accent-wash" : "hover:bg-canvas"}`}
                >
                  <span className="flex min-w-0 items-center gap-2.5">
                    <span className="font-mono text-[13px] text-ink-4">#</span>
                    <span className="truncate text-[13.5px] font-medium">{workspace.name}</span>
                  </span>
                  <span className="truncate font-mono text-[12.5px] text-ink-3">
                    {workspace.slug}
                  </span>
                  <span className="text-[13px] text-ink-3">{formatDate(workspace.created_at)}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {selected ? (
        <WorkspacePanel
          key={selected.id}
          workspace={selected}
          onClose={() => setSelectedId(null)}
          onDeleted={() => setSelectedId(null)}
        />
      ) : null}

      {creating ? (
        <NewWorkspaceDialog
          onClose={() => setCreating(false)}
          onCreated={(workspaceId) => {
            setCreating(false);
            setSelectedId(workspaceId);
          }}
        />
      ) : null}
    </div>
  );
}

/**
 * Panel sửa một workspace.
 *
 * `key` là id workspace, nên đổi lựa chọn là component dựng lại từ đầu — không
 * có đường nào để bản nháp đang gõ dở của workspace này trôi sang workspace kia.
 */
function WorkspacePanel({
  workspace,
  onClose,
  onDeleted,
}: {
  workspace: Workspace;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const { organization, workspaces } = useTenant();
  const [name, setName] = useState(workspace.name);
  const [slug, setSlug] = useState(workspace.slug);
  const [error, setError] = useState<unknown>(null);
  const [deleting, setDeleting] = useState(false);

  const rename = useRenameWorkspace(organization.id);

  const slugTaken = workspaces.some((it) => it.id !== workspace.id && it.slug === slug.trim());
  const dirty = name !== workspace.name || slug !== workspace.slug;
  const valid = name.trim().length > 0 && slug.trim().length >= 3 && !slugTaken;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const saved = await rename.mutateAsync({
        workspaceId: workspace.id,
        name,
        // Địa chỉ không đổi thì đừng gửi — gửi thừa là mở đường cho một lần ghi
        // đè không ai yêu cầu.
        slug: slug === workspace.slug ? undefined : slug,
      });
      // Server chuẩn hoá tên và địa chỉ; giá trị nó trả về mới là sự thật, nên
      // form nhận lại chính nó thay vì giữ thứ vừa gõ.
      setName(saved.name);
      setSlug(saved.slug);
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <aside className="flex w-[348px] shrink-0 flex-col border-l border-line bg-canvas">
      <header className="flex h-16 shrink-0 items-center border-b border-line px-4">
        <span className="text-[13px] font-semibold">Workspace</span>
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

      <form onSubmit={submit} className="flex grow flex-col gap-4 overflow-auto p-4">
        <label className="flex flex-col gap-1.5">
          <span className="text-[12.5px] font-medium text-ink-2">Name</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            className="h-9 rounded-control border border-line-strong bg-surface px-3 text-[13.5px] outline-none focus:border-accent"
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-[12.5px] font-medium text-ink-2">Address</span>
          <div className="flex h-9 items-stretch overflow-hidden rounded-control border border-line-strong bg-surface focus-within:border-accent">
            <span className="flex items-center border-r border-line bg-sidebar px-2.5 font-mono text-xs text-ink-4">
              {organization.slug}/
            </span>
            <input
              value={slug}
              onChange={(event) => setSlug(event.target.value)}
              className="grow px-2.5 font-mono text-[12.5px] font-medium outline-none"
            />
          </div>
          <span className="text-[11.5px] leading-relaxed text-ink-4">
            {slugTaken
              ? `${organization.name} already has a workspace at that address.`
              : "Changing this breaks links people have already saved or shared. The name above is safe to change at any time."}
          </span>
        </label>

        <ErrorNote error={error} />

        <div className="flex items-center gap-2">
          <Button type="submit" className="h-8 px-3.5 text-[13px]" disabled={!dirty || !valid || rename.isPending}>
            {rename.isPending ? "Saving…" : "Save changes"}
          </Button>
          {dirty ? (
            <Button
              type="button"
              variant="secondary"
              className="h-8 px-3.5 text-[13px]"
              onClick={() => {
                setName(workspace.name);
                setSlug(workspace.slug);
                setError(null);
              }}
            >
              Cancel
            </Button>
          ) : null}
        </div>
      </form>

      <div className="flex flex-col gap-2 border-t border-line p-4">
        <button
          type="button"
          onClick={() => setDeleting(true)}
          className="h-9 rounded-control border border-danger-line bg-surface text-[13px] font-medium text-danger transition-colors hover:bg-danger-wash"
        >
          Delete workspace
        </button>
        <span className="text-[11.5px] leading-relaxed text-ink-4">
          Takes its connections, workflows and run history with it. An organisation always keeps at
          least one workspace.
        </span>
      </div>

      {deleting ? (
        <DeleteWorkspaceDialog
          workspace={workspace}
          onClose={() => setDeleting(false)}
          onDeleted={() => {
            setDeleting(false);
            onDeleted();
          }}
        />
      ) : null}
    </aside>
  );
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
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
