"use client";

import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { Button, ErrorNote, Logo, Spinner } from "@/components/ui";
import { ApiError } from "@/lib/api";
import {
  useCompleteOnboarding,
  useOrganizations,
  useSlugAvailability,
  useUpdateOrganization,
} from "@/lib/queries";
import { useSession } from "@/lib/session";

/** Bỏ dấu và ký tự lạ để gợi ý địa chỉ từ tên vừa gõ. */
function suggestSlug(name: string): string {
  return name
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/đ/g, "d")
    .replace(/Đ/g, "D")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

export default function OnboardingPage() {
  const router = useRouter();
  const { status, user } = useSession();
  const organizations = useOrganizations();
  const organization = organizations.data?.[0] ?? null;

  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  // Người dùng chạm vào ô địa chỉ rồi thì thôi tự sinh theo tên.
  const [slugTouched, setSlugTouched] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const update = useUpdateOrganization(organization?.id ?? null);
  const complete = useCompleteOnboarding(organization?.id ?? null);
  const availability = useSlugAvailability(slugTouched ? slug : suggestSlug(name));

  const effectiveSlug = slugTouched ? slug : suggestSlug(name);

  useEffect(() => {
    if (status === "anonymous") router.replace("/sign-in");
  }, [status, router]);

  // Đã thiết lập xong rồi thì không quay lại wizard được nữa.
  useEffect(() => {
    if (organization?.onboarded) router.replace("/agent");
  }, [organization?.onboarded, router]);

  if (status === "loading" || organizations.isPending) return <Spinner label="Loading…" />;
  if (!organization) return <Spinner label="No organisation found." />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await update.mutateAsync({ name, slug: effectiveSlug });
      await complete.mutateAsync();
      router.replace("/agent");
    } catch (caught) {
      setError(caught);
    }
  }

  const slugTooShort = effectiveSlug.length > 0 && effectiveSlug.length < 3;
  const slugRejected = availability.error instanceof ApiError;
  const busy = update.isPending || complete.isPending;

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center gap-3 px-8">
        <Logo size={20} />
        <span className="text-sm font-semibold tracking-tight">AgentWorkflow</span>
        <div className="grow" />
        <span className="text-[12.5px] text-ink-4">Step 1 of 3</span>
        <div className="flex items-center gap-1.5">
          <span className="h-1 w-[18px] rounded-full bg-accent" />
          <span className="h-1 w-[18px] rounded-full bg-[#dcdcd7]" />
          <span className="h-1 w-[18px] rounded-full bg-[#dcdcd7]" />
        </div>
      </header>

      <div className="flex grow items-center justify-center px-8">
        <form onSubmit={submit} className="flex w-full max-w-[480px] flex-col gap-6">
          <div className="flex flex-col gap-2">
            <span className="text-[13px] font-medium text-accent">
              Welcome{user ? `, ${user.display_name}` : ""}
            </span>
            <h1 className="text-[29px] leading-tight font-semibold tracking-tight">
              What is your organisation called?
            </h1>
            <p className="text-[14.5px] leading-relaxed text-ink-3">
              Usually your company or team name. This is what everyone you invite will see.
            </p>
          </div>

          <div className="flex flex-col gap-4">
            <label className="flex flex-col gap-1.5">
              <span className="text-[12.5px] font-medium text-ink-2">Organisation name</span>
              <input
                required
                autoFocus
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Acme"
                className="h-11 rounded-lg border-[1.5px] border-accent bg-surface px-3.5 text-[15.5px] outline-none"
              />
            </label>

            <label className="flex flex-col gap-1.5">
              <span className="flex items-center text-[12.5px] font-medium text-ink-2">
                Address
                <span className="grow" />
                <span className="text-xs font-normal text-ink-4">
                  Editable now, fixed afterwards
                </span>
              </span>
              <div className="flex h-11 items-stretch overflow-hidden rounded-lg border border-line-strong bg-surface focus-within:border-accent">
                <span className="flex items-center border-r border-line bg-sidebar px-3 font-mono text-[13px] text-ink-3">
                  agentworkflow.com/
                </span>
                <input
                  value={effectiveSlug}
                  onChange={(event) => {
                    setSlugTouched(true);
                    setSlug(event.target.value);
                  }}
                  className="grow px-3 font-mono text-[13.5px] font-medium outline-none"
                />
                <span className="flex items-center px-3">
                  {availability.data?.available ? (
                    <svg
                      width="16"
                      height="16"
                      viewBox="0 0 16 16"
                      fill="none"
                      stroke="#16794a"
                      strokeWidth="1.7"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M3.2 8.4l3 3 6.6-6.8" />
                    </svg>
                  ) : null}
                </span>
              </div>

              <span className="text-xs leading-relaxed text-ink-4">
                {slugTooShort
                  ? "At least 3 characters."
                  : slugRejected
                    ? (availability.error as ApiError).message
                    : availability.data && !availability.data.available
                      ? "That address is already taken."
                      : "Lowercase letters, numbers and hyphens. Links to your runs and workflows are built from this, so it cannot be changed once you finish setting up."}
              </span>
            </label>

            <ErrorNote error={error} />

            <Button
              type="submit"
              className="h-11"
              disabled={busy || !name || availability.data?.available !== true}
            >
              {busy ? "Saving…" : "Continue"}
            </Button>
          </div>
        </form>
      </div>

      <footer className="flex h-16 shrink-0 items-center justify-center text-[12.5px] text-ink-4">
        {user ? `Signed in as ${user.email}` : null}
      </footer>
    </div>
  );
}
