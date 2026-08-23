"use client";

import { type FormEvent, useEffect, useState } from "react";
import { Button, Dialog, ErrorNote } from "@/components/ui";
import { useCreateWorkspace } from "@/lib/queries";
import { suggestSlug } from "@/lib/slug";
import { useTenant } from "@/lib/tenant";

/**
 * Tạo workspace trong organization đang mở.
 *
 * Địa chỉ workspace chỉ cần duy nhất trong phạm vi một organization, và cả danh
 * sách workspace đã nằm sẵn trong bộ nhớ — nên trùng hay không kiểm được ngay
 * tại chỗ, không cần hỏi backend như địa chỉ organization.
 */
export function NewWorkspaceDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (workspaceId: string) => void;
}) {
  const { organization, workspaces } = useTenant();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const create = useCreateWorkspace(organization.id);
  const effectiveSlug = slugTouched ? slug : suggestSlug(name);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !create.isPending) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, create.isPending]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const workspace = await create.mutateAsync({ name, slug: effectiveSlug });
      onCreated(workspace.id);
    } catch (caught) {
      setError(caught);
    }
  }

  const tooShort = effectiveSlug.length > 0 && effectiveSlug.length < 3;
  const taken = workspaces.some((workspace) => workspace.slug === effectiveSlug);
  const usable = effectiveSlug.length >= 3 && !taken;

  return (
    <Dialog
      labelledBy="new-workspace-title"
      onDismiss={create.isPending ? undefined : onClose}
      className="max-w-[500px]"
    >
      <form onSubmit={submit} className="flex flex-col gap-6 p-7">
        <div className="flex flex-col gap-1.5">
          <h2 id="new-workspace-title" className="text-xl font-semibold tracking-tight">
            New workspace
          </h2>
          <p className="text-sm leading-relaxed text-ink-3">
            A separate space inside {organization.name} for its own connections, workflows and
            runs.
          </p>
        </div>

        <div className="flex flex-col gap-4">
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-ink-2">Name</span>
            <input
              required
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Engineering"
              className="h-11 rounded-control border-[1.5px] border-accent bg-surface px-3.5 text-base outline-none placeholder:font-normal placeholder:text-ink-4"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="flex items-center text-sm font-medium text-ink-2">
              Address
              <span className="grow" />
              <span className="text-[13px] font-normal text-ink-4">Changeable later</span>
            </span>
            <div className="flex h-11 items-stretch overflow-hidden rounded-control border border-line-strong bg-surface focus-within:border-accent">
              <span className="flex items-center border-r border-line bg-sidebar px-3 font-mono text-[13px] text-ink-3">
                {organization.slug}/
              </span>
              <input
                value={effectiveSlug}
                onChange={(event) => {
                  setSlugTouched(true);
                  setSlug(event.target.value);
                }}
                className="grow px-3 font-mono text-sm font-medium outline-none"
              />
              <span className="flex w-8 items-center justify-center">
                {usable ? <CheckMark /> : null}
              </span>
            </div>
            <span className="text-[13px] leading-relaxed text-ink-4">
              {tooShort
                ? "At least 3 characters."
                : taken
                  ? `${organization.name} already has a workspace at that address.`
                  : `Lowercase letters, numbers and hyphens. Only has to be unique inside ${organization.name}.`}
            </span>
          </label>

          <p className="rounded-md border border-line bg-canvas px-3.5 py-2.5 text-[13px] leading-relaxed text-ink-2">
            It starts out reachable by owners and admins only. Invite the members who need it from
            the workspace panel afterwards.
          </p>
        </div>

        <ErrorNote error={error} />

        <div className="flex items-center justify-end gap-2.5">
          <Button type="button" variant="secondary" onClick={onClose} disabled={create.isPending}>
            Cancel
          </Button>
          <Button type="submit" disabled={create.isPending || !name || !usable}>
            {create.isPending ? "Creating…" : "Create workspace"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function CheckMark() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 16 16"
      fill="none"
      stroke="var(--color-ok)"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M3.2 8.4l3 3 6.6-6.8" />
    </svg>
  );
}
