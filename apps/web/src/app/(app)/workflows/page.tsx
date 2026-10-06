"use client";

import Link from "next/link";
import { ErrorNote, Spinner } from "@/components/ui";
import { useWorkflows } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

/**
 * Workflow của workspace đang mở.
 *
 * Một workflow bắt đầu từ một conversation với Agent ("Turn into
 * Workflow"), không có nút tạo tay ở đây. Publish và Run nằm trong trang của từng workflow.
 */
export default function WorkflowsPage() {
  const { organization, workspace } = useTenant();
  const workflows = useWorkflows(organization.id, workspace?.id ?? null);

  if (!workspace) return <Spinner label="No workspace in this organisation." />;

  const rows = (workflows.data ?? []).filter((row) => row.archived_at === null);

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center gap-2 border-b border-line px-8">
        <h1 className="text-[15px] font-semibold">Workflows</h1>
        {workflows.data ? <span className="text-[13px] text-ink-4">{rows.length}</span> : null}
      </header>

      <div className="flex min-h-0 grow flex-col gap-4 overflow-y-auto px-8 py-6">
        <p className="max-w-[640px] text-[13px] leading-relaxed text-ink-3">
          A workflow starts as a conversation with an agent. Open one that went well and choose
          Turn into Workflow.
        </p>

        {workflows.isPending ? <span className="text-sm text-ink-4">Loading…</span> : null}
        {workflows.error ? <ErrorNote error={workflows.error} /> : null}

        {workflows.data && rows.length === 0 ? (
          <div className="flex flex-col items-start gap-3 rounded-[9px] border border-line bg-canvas px-5 py-6">
            <span className="text-[14px] font-medium">No workflows yet</span>
            <span className="max-w-[520px] text-[13px] leading-relaxed text-ink-3">
              Ask an agent to do the task once. When the run is what you want repeated, turn that
              conversation into a workflow.
            </span>
            <Link
              href="/agent"
              className="flex h-9 items-center rounded-control bg-ink px-3.5 text-[13px] font-medium text-white hover:bg-ink-2"
            >
              Go to agents
            </Link>
          </div>
        ) : null}

        {rows.length > 0 ? (
          <div className="overflow-hidden rounded-[9px] border border-line">
            <div className="flex border-b border-line bg-canvas px-4 py-2 text-[11px] font-semibold tracking-wide text-ink-4 uppercase">
              <span className="flex-[6]">Name</span>
              <span className="flex-[2]">State</span>
              <span className="flex-[2] text-right">Updated</span>
            </div>
            {rows.map((row) => (
              <Link
                key={row.id}
                href={`/workflows/${row.id}`}
                className="flex items-center border-t border-line-soft px-4 py-3 first:border-t-0 hover:bg-canvas"
              >
                <span className="min-w-0 flex-[6] truncate text-[13.5px] font-medium">{row.name}</span>
                <span className="flex-[2]">
                  <StatePill published={row.current_version_id !== null} />
                </span>
                <span className="flex-[2] text-right text-[12.5px] text-ink-4">
                  {formatWhen(row.updated_at)}
                </span>
              </Link>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function StatePill({ published }: { published: boolean }) {
  return published ? (
    <span className="rounded-full bg-ok-wash px-2 py-0.5 text-[11.5px] font-medium text-ok">
      Published
    </span>
  ) : (
    <span className="rounded-full bg-sidebar px-2 py-0.5 text-[11.5px] font-medium text-ink-3">
      Draft
    </span>
  );
}

function formatWhen(iso: string): string {
  const then = new Date(iso);
  const minutes = Math.round((Date.now() - then.getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 24 * 60) return `${Math.round(minutes / 60)} h ago`;
  return then.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}
