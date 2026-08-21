"use client";

import { useSession } from "@/lib/session";

/**
 * Chỗ Agent chat sẽ nằm (Phase 3).
 *
 * Cố ý để empty state chứ không dựng khung chat giả: backend chưa có endpoint
 * nào cho Agent, và một ô soạn tin không gửi được gì sẽ khiến người thử tưởng
 * là nó hỏng.
 */
export default function AgentPage() {
  const { user, signOut } = useSession();

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-13 shrink-0 items-center border-b border-line px-6">
        <span className="text-sm font-semibold">Agent</span>
        <div className="grow" />
        <span className="text-[12.5px] text-ink-4">{user?.email}</span>
        <button
          type="button"
          onClick={() => void signOut()}
          className="ml-3 rounded-md border border-line-strong px-2.5 py-1 text-[12.5px] font-medium hover:bg-sunken"
        >
          Sign out
        </button>
      </header>

      <div className="flex grow items-center justify-center p-8">
        <div className="flex max-w-md flex-col items-center gap-2 text-center">
          <h2 className="text-lg font-semibold tracking-tight">Nothing to run yet</h2>
          <p className="text-[13.5px] leading-relaxed text-ink-3">
            The agent needs a connected app before it can do anything. Connections arrive with the
            Integration Plane; until then this workspace is set up and waiting.
          </p>
        </div>
      </div>
    </div>
  );
}
