"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { Dropped } from "@/components/compile-dialog";
import { RunDialog } from "@/components/run-dialog";
import {
  Account,
  Bindings,
  BUTTON_PLAIN,
  BUTTON_PRIMARY,
  Fact,
  Part,
  PlayIcon,
  providerName,
  StatusPill,
} from "@/components/run-parts";
import { ErrorNote, Spinner } from "@/components/ui";
import { WorkflowGraph } from "@/components/workflow-graph";
import { Notice, outlineCount, Panel } from "@/components/workflow-outline";
import type { IrNode, WorkflowIntent, WorkflowIr } from "@/lib/api";
import {
  useCompileSessionById,
  useTools,
  useWorkflow,
  useWorkflowDraft,
} from "@/lib/queries";
import {
  allNodes,
  elapsed,
  formatDuration,
  formatStarted,
  providerOf,
  usePublishWorkflow,
  useRuns,
  useWorkflowVersion,
  useWorkflowVersions,
} from "@/lib/runs";
import { useTenant } from "@/lib/tenant";

/**
 * Một workflow: đồ thị các bước ở giữa, những gì cần biết để chạy nó ở bên phải.
 *
 * Run luôn chạy version đã publish mới nhất, nên đó là thứ được vẽ mặc định.
 * Khi draft đã khác bản publish, trang cho xem cả hai và Publish hiện ra.
 */
export default function WorkflowPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { organization, workspace } = useTenant();
  const workspaceId = workspace?.id ?? null;

  const workflow = useWorkflow(organization.id, workspaceId, id);
  const draft = useWorkflowDraft(organization.id, workspaceId, id);
  const versions = useWorkflowVersions(organization.id, workspaceId, id);
  const latest = versions.data?.[0] ?? null;
  const version = useWorkflowVersion(organization.id, workspaceId, id, latest?.number ?? null);
  const session = useCompileSessionById(
    organization.id,
    workspaceId,
    draft.data?.compiled_from ?? null,
  );
  const runs = useRuns(organization.id, workspaceId, { workflowId: id, limit: 5 });
  const tools = useTools();
  const publish = usePublishWorkflow(organization.id, workspaceId);

  const [view, setView] = useState<"published" | "draft" | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  if (!workspace) return <Spinner label="No workspace in this organisation." />;
  if (workflow.isPending || draft.isPending || versions.isPending || (latest && version.isPending)) {
    return <Spinner label="Loading workflow…" />;
  }
  if (workflow.error || draft.error || !workflow.data || !draft.data) {
    return (
      <div className="flex h-full flex-col items-start gap-3 px-8 py-6">
        <ErrorNote error={workflow.error ?? draft.error} />
        <Link href="/workflows" className="text-[13px] font-medium text-accent">
          Back to workflows
        </Link>
      </div>
    );
  }

  const published = version.data ?? null;
  const drifted =
    published !== null && JSON.stringify(published.ir) !== JSON.stringify(draft.data.ir);
  const showing = published === null ? "draft" : (view ?? "published");
  const ir: WorkflowIr = showing === "published" && published ? published.ir : draft.data.ir;
  const intent = showing === "published" && published ? published.intent : draft.data.intent;
  const { validation } = draft.data;

  const steps = allNodes(ir);
  const selectedNode = steps.find((node) => node.id === selected) ?? null;
  const schema = ir.nodes.find((node) => node.type === "trigger")?.input_schema;
  const inputs = Object.entries(schema?.properties ?? {});
  const providers = new Map<string, string[]>();
  for (const node of steps) {
    const provider = providerOf(node);
    if (provider) providers.set(provider, [...(providers.get(provider) ?? []), node.tool_id ?? ""]);
  }
  const dropped = session.data?.proposal?.dropped ?? [];
  const notes = validation.errors.length + validation.warnings.length;

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center gap-2.5 border-b border-line pr-5 pl-8">
        <Link href="/workflows" className="text-[13px] text-ink-4 hover:text-ink-2">
          Workflows
        </Link>
        <span className="text-line-strong">/</span>
        <h1 className="min-w-0 truncate text-[15px] font-semibold">{workflow.data.name}</h1>
        {latest ? (
          <span className="shrink-0 rounded-full bg-ok-wash px-2 py-0.5 text-[11.5px] font-medium text-ok">
            Published · v{latest.number}
          </span>
        ) : (
          <span className="shrink-0 rounded-full bg-sidebar px-2 py-0.5 text-[11.5px] font-medium text-ink-3">
            Draft
          </span>
        )}
        <span className="grow" />
        <span className="hidden shrink-0 text-[12px] text-ink-4 xl:inline">{outlineCount(ir)}</span>

        {drifted && latest ? (
          <div className="flex shrink-0 gap-0.5 rounded-lg bg-sidebar p-0.5">
            {(["published", "draft"] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => {
                  setView(option);
                  setSelected(null);
                }}
                className={`rounded-md px-2.5 py-1 text-[12.5px] font-medium ${
                  showing === option ? "border border-line bg-surface text-ink" : "text-ink-3"
                }`}
              >
                {option === "published" ? `v${latest.number}` : "Draft"}
              </button>
            ))}
          </div>
        ) : null}

        {published === null || drifted ? (
          <button
            type="button"
            disabled={!validation.ok || publish.isPending}
            title={validation.ok ? undefined : "The draft has errors; see Checks."}
            onClick={() => publish.mutate(id, { onSuccess: () => setView("published") })}
            className={latest ? BUTTON_PLAIN : BUTTON_PRIMARY}
          >
            {publish.isPending ? "Publishing…" : latest ? `Publish v${latest.number + 1}` : "Publish"}
          </button>
        ) : null}
        {latest ? (
          <button type="button" onClick={() => setRunning(true)} className={BUTTON_PRIMARY}>
            <PlayIcon />
            Run
          </button>
        ) : null}
      </header>

      {publish.error ? (
        <div className="shrink-0 border-b border-line px-8 py-2">
          <ErrorNote error={publish.error} />
        </div>
      ) : null}
      {drifted && showing === "published" ? (
        <div className="shrink-0 border-b border-[#f2dfb8] bg-[#fff8ec] px-8 py-2 text-[12.5px] text-warn">
          The draft has changed since v{latest?.number}. Run still uses v{latest?.number} until you
          publish.
        </div>
      ) : null}

      <div className="flex min-h-0 grow flex-col lg:flex-row">
        <div className="min-h-[420px] min-w-0 grow">
          <WorkflowGraph key={showing} ir={ir} selected={selected} onSelect={setSelected} />
        </div>

        <aside className="flex w-full shrink-0 flex-col gap-3.5 border-t border-line bg-canvas px-5 py-5 lg:w-[372px] lg:overflow-y-auto lg:border-t-0 lg:border-l [&>*]:shrink-0">
          {selectedNode ? (
            <StepSpec
              node={selectedNode}
              writes={
                tools.data?.find((tool) => tool.tool_id === selectedNode.tool_id)?.side_effect ===
                "write"
              }
            />
          ) : null}

          {intent ? <Intent intent={intent} /> : null}

          {inputs.length ? (
            <Panel title="You give it each run">
              {inputs.map(([name, spec]) => (
                <Part key={name}>
                  <span className="font-mono text-[12.5px] font-medium">{name}</span>
                  {spec.description ? (
                    <span className="text-[12px] text-ink-3">{spec.description}</span>
                  ) : null}
                </Part>
              ))}
            </Panel>
          ) : null}

          {providers.size ? (
            <Panel title="Accounts it needs" count="chosen when you run">
              <Part>
                {[...providers].map(([provider, used]) => (
                  <Account
                    key={provider}
                    provider={provider}
                    name={providerName(provider)}
                    note={`${used.length} ${used.length === 1 ? "step" : "steps"} · ${[...new Set(used)]
                      .map((tool) => tool.slice(provider.length + 1))
                      .join(", ")}`}
                  />
                ))}
              </Part>
            </Panel>
          ) : null}

          {showing === "draft" ? (
            <Panel
              title="Checks"
              tone={validation.errors.length ? "error" : "plain"}
              count={notes ? `${notes} ${notes === 1 ? "note" : "notes"}` : undefined}
            >
              {validation.ok ? <Notice tone="ok">Valid against the tool catalogue.</Notice> : null}
              {validation.errors.map((issue, index) => (
                <Notice key={`e${index}`} tone="error">
                  {issue.message}
                </Notice>
              ))}
              {validation.warnings.map((issue, index) => (
                <Notice key={`w${index}`} tone="warn">
                  {issue.message}
                </Notice>
              ))}
            </Panel>
          ) : null}

          {showing === "draft" && session.data?.proposal && dropped.length ? (
            <Dropped proposal={session.data.proposal} />
          ) : null}

          {latest ? (
            <Panel title="Recent runs" count={runs.data?.length ? String(runs.data.length) : undefined}>
              {runs.data?.length === 0 ? (
                <Part>
                  <span className="text-[12.5px] text-ink-3">Not run yet.</span>
                </Part>
              ) : null}
              {runs.data?.map((run) => {
                const took = run.finished_at ? elapsed(run.started_at, run.finished_at, 0) : null;
                return (
                  <Link
                    key={run.id}
                    href={`/runs/${run.id}`}
                    className="flex items-center gap-2.5 border-t border-line-soft px-3 py-2 first:border-t-0 hover:bg-canvas"
                  >
                    <StatusPill status={run.status} />
                    <span className="min-w-0 grow truncate text-[12.5px] text-ink-2">
                      {formatStarted(run.created_at)}
                    </span>
                    <span className="shrink-0 text-[12px] text-ink-3 tabular-nums">
                      {took === null ? "—" : formatDuration(took)}
                    </span>
                  </Link>
                );
              })}
            </Panel>
          ) : null}
        </aside>
      </div>

      {running && published && latest ? (
        <RunDialog
          workflow={workflow.data}
          versionNumber={latest.number}
          ir={published.ir}
          onClose={() => setRunning(false)}
          onStarted={(runId) => router.push(`/runs/${runId}`)}
        />
      ) : null}
    </div>
  );
}

function StepSpec({ node, writes }: { node: IrNode; writes: boolean }) {
  const provider = providerOf(node);
  return (
    <Panel title="Selected step" count={node.type}>
      <Part>
        <span className="font-mono text-[13px] font-medium break-all">{node.id}</span>
        {node.tool_id ? <Fact name="Uses" mono>{node.tool_id}</Fact> : null}
        {node.model ? <Fact name="Model" mono>{node.model}</Fact> : null}
      </Part>
      <Bindings node={node} />
      {node.instructions ? (
        <Part>
          <span className="text-[11px] font-semibold tracking-wide text-ink-4 uppercase">
            Instructions
          </span>
          <p className="max-h-40 overflow-y-auto text-[12.5px] leading-relaxed whitespace-pre-wrap text-ink-2">
            {node.instructions}
          </p>
        </Part>
      ) : null}
      {node.code ? (
        <Part>
          <span className="text-[11px] font-semibold tracking-wide text-ink-4 uppercase">Code</span>
          <pre className="max-h-40 overflow-auto font-mono text-[11.5px] leading-relaxed text-ink-2">
            {node.code}
          </pre>
        </Part>
      ) : null}
      {provider ? (
        <Part>
          <Account
            provider={provider}
            name={`Any ${providerName(provider)} account you can use`}
            note="chosen each time you run"
          />
        </Part>
      ) : null}
      {writes ? (
        <Part>
          <span className="text-[12px] leading-relaxed text-ink-3">
            Writes outside. If the worker retries this step, the same write is not made twice.
          </span>
        </Part>
      ) : null}
    </Panel>
  );
}

function Intent({ intent }: { intent: WorkflowIntent }) {
  const groups: { title: string; items: string[] }[] = [
    { title: "Changes every run", items: intent.thay_doi },
    { title: "Stays the same", items: intent.co_dinh },
    { title: "Only when", items: intent.dieu_kien },
  ].filter((group) => group.items.length > 0);

  return (
    <Panel title="What this workflow is for">
      <Part>
        <p className="text-[13px] leading-relaxed font-medium">{intent.nhiem_vu}</p>
      </Part>
      {groups.map((group) => (
        <Part key={group.title}>
          <span className="text-[12px] font-medium text-ink-3">{group.title}</span>
          <ul className="flex flex-col gap-0.5 text-[12.5px] leading-relaxed text-ink-2">
            {group.items.map((item, index) => (
              <li key={index} className="flex gap-2">
                <span className="text-line-strong">–</span>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </Part>
      ))}
    </Panel>
  );
}
