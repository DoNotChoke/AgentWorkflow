"use client";

import type { ReactNode } from "react";
import { ProviderTile } from "@/components/provider-icon";
import { describeBinding } from "@/components/workflow-outline";
import type { IrNode, RunStatus, StepStatus } from "@/lib/api";

/** Các mảnh dùng chung của trang workflow, trang lần chạy và trang Runs. */

const STATUS: Record<RunStatus | StepStatus, { label: string; look: string }> = {
  pending: { label: "Waiting", look: "bg-sidebar text-ink-3" },
  running: { label: "Running", look: "bg-[#e8effd] text-[#1d4ed8]" },
  waiting: { label: "Paused", look: "bg-[#fff4e0] text-warn" },
  waiting_for_approval: { label: "Needs approval", look: "bg-[#fff4e0] text-warn" },
  succeeded: { label: "Succeeded", look: "bg-ok-wash text-ok" },
  failed: { label: "Failed", look: "bg-danger-wash text-danger" },
  cancelled: { label: "Cancelled", look: "bg-sidebar text-ink-3" },
  timed_out: { label: "Timed out", look: "bg-danger-wash text-danger" },
  skipped: { label: "Skipped", look: "bg-[#fff4e0] text-warn" },
};

export function StatusPill({ status }: { status: RunStatus | StepStatus }) {
  const { label, look } = STATUS[status];
  return (
    <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11.5px] font-medium ${look}`}>
      {label}
    </span>
  );
}

export function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="shrink-0 rounded-full bg-sidebar px-2 py-0.5 text-[11.5px] font-medium text-ink-3">
      {children}
    </span>
  );
}

/** Một phần trong `Panel`, ngăn với phần trên bằng một nét mảnh. */
export function Part({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5 border-t border-line-soft px-3 py-2.5 first:border-t-0">
      {children}
    </div>
  );
}

export function Fact({
  name,
  mono = false,
  children,
}: {
  name: string;
  mono?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="shrink-0 text-[12px] text-ink-3">{name}</span>
      <span
        className={`min-w-0 truncate text-right text-ink-2 tabular-nums ${
          mono ? "font-mono text-[11.5px]" : "text-[12.5px]"
        }`}
      >
        {children}
      </span>
    </div>
  );
}

export function Account({
  provider,
  name,
  note,
}: {
  provider: string;
  name: string;
  note?: string;
}) {
  return (
    <div className="flex items-center gap-2.5">
      <ProviderTile provider={provider} box={26} />
      <div className="min-w-0">
        <div className="truncate text-[12.5px] font-medium text-ink-2">{name}</div>
        {note ? <div className="truncate text-[11.5px] text-ink-4">{note}</div> : null}
      </div>
    </div>
  );
}

export function providerName(provider: string): string {
  return provider.slice(0, 1).toUpperCase() + provider.slice(1);
}

/** Một node lấy từng giá trị từ đâu, mỗi giá trị một dòng. */
export function Bindings({ node }: { node: IrNode }) {
  const rows: [string, string][] = [];
  if (node.type === "loop") rows.push(["over", describeBinding(node.over)]);
  if (node.type === "condition") {
    rows.push(["left", describeBinding(node.left)]);
    rows.push(["op", node.op ?? ""]);
    rows.push(["right", describeBinding(node.right)]);
  }
  const bindings = node.type === "transform" ? node.args : node.input;
  for (const [name, binding] of Object.entries(bindings ?? {})) {
    rows.push([name, describeBinding(binding)]);
  }
  for (const [name, binding] of Object.entries(node.filter ?? {})) {
    rows.push([`only when ${name}`, describeBinding(binding)]);
  }
  if (rows.length === 0) return null;
  return (
    <Part>
      <span className="text-[11px] font-semibold tracking-wide text-ink-4 uppercase">Input</span>
      {rows.map(([name, from]) => (
        <div key={name} className="flex flex-col">
          <span className="text-[12px] text-ink-3">{name}</span>
          <span className="font-mono text-[11.5px] break-words text-ink-2">{from}</span>
        </div>
      ))}
    </Part>
  );
}

export const HEADER_BUTTON =
  "flex h-9 shrink-0 items-center gap-1.5 rounded-control px-3.5 text-[13px] font-medium transition-colors disabled:opacity-50";
export const BUTTON_PRIMARY = `${HEADER_BUTTON} bg-accent text-white hover:bg-accent-ink`;
export const BUTTON_PLAIN = `${HEADER_BUTTON} border border-line-strong bg-surface text-ink-2 hover:bg-sunken`;

export function PlayIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden>
      <path d="M6 3l14 9-14 9z" />
    </svg>
  );
}
