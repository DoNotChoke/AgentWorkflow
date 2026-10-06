"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useMemo, useState } from "react";
import { ProviderIcon } from "@/components/provider-icon";
import { BUTTON_PLAIN, BUTTON_PRIMARY, PlayIcon, providerName } from "@/components/run-parts";
import { Dialog, ErrorNote } from "@/components/ui";
import type { Connection, Workflow, WorkflowIr } from "@/lib/api";
import { isConnectionUsable } from "@/lib/connections";
import { useConnections, useTools } from "@/lib/queries";
import { allNodes, providerOf, useStartRun } from "@/lib/runs";
import { useSession } from "@/lib/session";
import { useTenant } from "@/lib/tenant";

/**
 * Chạy một version đã publish: nhập đầu vào, chọn một tài khoản cho mỗi provider.
 *
 * Lần chạy hành động thay người bấm Run, nên chỉ chọn được tài khoản dùng chung
 * của workspace hoặc tài khoản riêng của chính họ.
 */
export function RunDialog({
  workflow,
  versionNumber,
  ir,
  initialInputs,
  onClose,
  onStarted,
}: {
  workflow: Workflow;
  versionNumber: number;
  ir: WorkflowIr;
  /** Đầu vào của một lần chạy trước, khi chạy lại. */
  initialInputs?: Record<string, unknown>;
  onClose: () => void;
  onStarted: (runId: string) => void;
}) {
  const { organization, workspace } = useTenant();
  const { user } = useSession();
  const workspaceId = workspace?.id ?? null;
  const connections = useConnections(organization.id, workspaceId);
  const tools = useTools();
  const start = useStartRun(organization.id, workspaceId);

  const schema = ir.nodes.find((node) => node.type === "trigger")?.input_schema;
  const fields = Object.entries(schema?.properties ?? {});
  const required = new Set(schema?.required ?? []);

  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      fields.map(([name]) => {
        const before = initialInputs?.[name];
        return [name, before === undefined ? "" : typeof before === "string" ? before : JSON.stringify(before)];
      }),
    ),
  );
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [error, setError] = useState<unknown>(null);

  const steps = useMemo(() => allNodes(ir), [ir]);
  const needs = useMemo(() => {
    const counts = new Map<string, number>();
    for (const node of steps) {
      const provider = providerOf(node);
      if (provider) counts.set(provider, (counts.get(provider) ?? 0) + 1);
    }
    return [...counts];
  }, [steps]);

  const usable = (provider: string): Connection[] =>
    (connections.data ?? []).filter(
      (connection) =>
        connection.provider === provider &&
        isConnectionUsable(connection.status) &&
        (connection.sharing === "workspace" || connection.owner_user_id === user?.id),
    );

  const writes = steps.filter(
    (node) =>
      node.type === "tool" &&
      tools.data?.find((tool) => tool.tool_id === node.tool_id)?.side_effect === "write",
  );
  const loopWrites = ir.nodes.some(
    (node) => node.type === "loop" && node.body?.some((inner) => writes.includes(inner)),
  );

  const picked = (provider: string) => chosen[provider] ?? usable(provider)[0]?.id ?? "";
  const missingAccount = needs.some(([provider]) => !picked(provider));
  const missingInput = fields.some(([name]) => required.has(name) && !values[name]?.trim());

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !start.isPending) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, start.isPending]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    const inputs: Record<string, unknown> = {};
    for (const [name, spec] of fields) {
      const text = values[name] ?? "";
      if (text.trim() === "" && !required.has(name)) continue;
      inputs[name] = coerce(text, spec.type);
    }
    try {
      const run = await start.mutateAsync({
        workflowId: workflow.id,
        inputs,
        connectionsByProvider: Object.fromEntries(needs.map(([provider]) => [provider, picked(provider)])),
      });
      onStarted(run.id);
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <Dialog
      labelledBy="run-workflow-title"
      onDismiss={start.isPending ? undefined : onClose}
      className="max-w-[520px] overflow-hidden"
    >
      <form onSubmit={submit} className="flex max-h-[85vh] flex-col">
        <div className="flex flex-col gap-0.5 border-b border-line px-6 pt-5 pb-3.5">
          <h2 id="run-workflow-title" className="text-[16px] font-semibold">
            Run workflow
          </h2>
          <span className="truncate text-[12.5px] text-ink-3">
            {workflow.name} · v{versionNumber}
          </span>
        </div>

        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto px-6 py-5">
          {fields.map(([name, spec], index) => (
            <label key={name} className="flex flex-col gap-1">
              <span className="text-[13px] font-medium">
                <span className="font-mono">{name}</span>
                {required.has(name) ? <span className="text-danger"> *</span> : null}
              </span>
              {spec.description ? (
                <span className="text-[12px] text-ink-3">{spec.description}</span>
              ) : null}
              <input
                value={values[name] ?? ""}
                autoFocus={index === 0}
                onChange={(event) => setValues((all) => ({ ...all, [name]: event.target.value }))}
                className="h-10 rounded-control border border-line-strong bg-surface px-3 text-[13.5px] outline-none focus:border-accent focus:ring-1 focus:ring-accent"
              />
            </label>
          ))}
          {fields.length === 0 ? (
            <span className="text-[13px] text-ink-3">This workflow takes no input.</span>
          ) : null}

          {needs.length ? (
            <div className="flex flex-col gap-3 border-t border-line-soft pt-3.5">
              <span className="text-[11px] font-semibold tracking-wide text-ink-4 uppercase">
                Accounts · one per provider
              </span>
              {needs.map(([provider, count]) => {
                const options = usable(provider);
                return (
                  <div key={provider} className="flex flex-col gap-1">
                    <div className="flex items-baseline justify-between">
                      <span className="text-[13px] font-medium">{providerName(provider)}</span>
                      <span className="text-[11.5px] text-ink-4">
                        used by {count} {count === 1 ? "step" : "steps"}
                      </span>
                    </div>
                    {options.length ? (
                      <div className="flex h-10 items-center gap-2.5 rounded-control border border-line-strong bg-surface pl-3">
                        <ProviderIcon provider={provider} size={17} />
                        <select
                          aria-label={`${providerName(provider)} account`}
                          value={picked(provider)}
                          onChange={(event) =>
                            setChosen((all) => ({ ...all, [provider]: event.target.value }))
                          }
                          className="h-full min-w-0 grow bg-transparent pr-2 text-[13.5px] outline-none"
                        >
                          {options.map((connection) => (
                            <option key={connection.id} value={connection.id}>
                              {connection.display_name} ·{" "}
                              {connection.sharing === "private" ? "private" : "shared"}
                            </option>
                          ))}
                        </select>
                      </div>
                    ) : (
                      <span className="rounded-control border border-danger-line bg-danger-wash px-3 py-2 text-[12.5px] text-danger">
                        {connections.isPending ? (
                          "Loading accounts…"
                        ) : (
                          <>
                            No {providerName(provider)} account you can use.{" "}
                            <Link href="/connections" className="font-medium underline">
                              Connect one
                            </Link>
                          </>
                        )}
                      </span>
                    )}
                  </div>
                );
              })}
              <span className="text-[12px] text-ink-3">
                The run acts as you, through these accounts only.
              </span>
            </div>
          ) : null}

          {writes.length ? (
            <div className="rounded-control border border-[#f2dfb8] bg-[#fff8ec] px-3 py-2 text-[12.5px] text-warn">
              This writes for real: {writes.length} {writes.length === 1 ? "step" : "steps"}{" "}
              ({[...new Set(writes.map((node) => node.tool_id))].join(", ")})
              {loopWrites ? ", repeated once per item of the loop." : "."}
            </div>
          ) : null}

          <ErrorNote error={error} />
        </div>

        <div className="flex justify-end gap-2 border-t border-line bg-canvas px-6 py-3.5">
          <button type="button" onClick={onClose} disabled={start.isPending} className={BUTTON_PLAIN}>
            Cancel
          </button>
          <button
            type="submit"
            disabled={start.isPending || missingAccount || missingInput}
            className={BUTTON_PRIMARY}
          >
            <PlayIcon />
            {start.isPending ? "Starting…" : "Run"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

/** Ô nhập là chữ; số, đúng/sai và JSON được đổi theo kiểu trong schema của trigger. */
function coerce(text: string, type: string | undefined): unknown {
  if (type === "number" || type === "integer") {
    const number = Number(text);
    return Number.isNaN(number) ? text : number;
  }
  if (type === "boolean") return text.trim().toLowerCase() === "true";
  if (type === "object" || type === "array") {
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  return text;
}
