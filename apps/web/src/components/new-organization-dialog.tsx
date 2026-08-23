"use client";

import { type FormEvent, useEffect, useState } from "react";
import { Button, ErrorNote } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { useCreateOrganization, useSlugAvailability } from "@/lib/queries";
import { suggestSlug } from "@/lib/slug";

/**
 * Tạo organization thứ hai trở đi.
 *
 * Hỏi cùng hai thứ mà wizard thiết lập hỏi — tên và địa chỉ — nhưng gói trong
 * một hộp thoại chứ không phải một màn hình riêng: người đã dùng sản phẩm rồi
 * thì không cần được chào mừng lại từ đầu. Địa chỉ vẫn phải hỏi ở đây, vì tạo
 * xong là nó đóng băng và không còn chỗ nào sửa được nữa.
 */
export function NewOrganizationDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (organizationId: string) => void;
}) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  // Chạm vào ô địa chỉ rồi thì thôi tự chạy theo tên.
  const [slugTouched, setSlugTouched] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const effectiveSlug = slugTouched ? slug : suggestSlug(name);
  const availability = useSlugAvailability(effectiveSlug);
  const create = useCreateOrganization();

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
      const organization = await create.mutateAsync({ name, slug: effectiveSlug });
      onCreated(organization.id);
    } catch (caught) {
      setError(caught);
    }
  }

  const slugTooShort = effectiveSlug.length > 0 && effectiveSlug.length < 3;
  const slugRejected = availability.error instanceof ApiError;
  const taken = availability.data ? !availability.data.available : false;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/25 p-6"
      onMouseDown={(event) => {
        // Chỉ đóng khi bấm đúng vào nền, không phải khi kéo chuột từ trong hộp ra.
        if (event.target === event.currentTarget && !create.isPending) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal
        aria-labelledby="new-organization-title"
        className="w-full max-w-[520px] rounded-xl border border-line bg-surface shadow-xl"
      >
        <form onSubmit={submit} className="flex flex-col gap-6 p-7">
          <div className="flex flex-col gap-1.5">
            <h2 id="new-organization-title" className="text-xl font-semibold tracking-tight">
              New organisation
            </h2>
            <p className="text-sm leading-relaxed text-ink-3">
              A separate tenant with its own members, connections and runs. It starts with one
              workspace called <span className="font-medium text-ink-2">Default</span>.
            </p>
          </div>

          <div className="flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-ink-2">Organisation name</span>
              <input
                required
                autoFocus
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Acme"
                className="h-11 rounded-control border border-line-strong bg-surface px-3.5 text-base outline-none transition-colors placeholder:text-ink-4 focus:border-accent"
              />
            </label>

            <label className="flex flex-col gap-1.5">
              <span className="flex items-center text-sm font-medium text-ink-2">
                Address
                <span className="grow" />
                <span className="text-[13px] font-normal text-ink-4">Cannot be changed later</span>
              </span>
              <div className="flex h-11 items-stretch overflow-hidden rounded-control border border-line-strong bg-surface focus-within:border-accent">
                <span className="flex items-center border-r border-line bg-sidebar px-3 font-mono text-[13px] text-ink-3">
                  agentworkflow.com/
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
                  {availability.data?.available ? (
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
                  ) : null}
                </span>
              </div>
              <span className="text-[13px] leading-relaxed text-ink-4">
                {slugTooShort
                  ? "At least 3 characters."
                  : slugRejected
                    ? (availability.error as ApiError).message
                    : taken
                      ? "That address is already taken."
                      : "Lowercase letters, numbers and hyphens."}
              </span>
            </label>
          </div>

          <ErrorNote error={error} />

          <div className="flex items-center justify-end gap-2.5">
            <Button type="button" variant="secondary" onClick={onClose} disabled={create.isPending}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={create.isPending || !name || availability.data?.available !== true}
            >
              {create.isPending ? "Creating…" : "Create organisation"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
