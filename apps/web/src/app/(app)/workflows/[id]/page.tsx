"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { Dropped } from "@/components/compile-dialog";
import { ErrorNote, Spinner } from "@/components/ui";
import { Notice, outlineCount, Panel, WorkflowOutline } from "@/components/workflow-outline";
import type { WorkflowIntent } from "@/lib/api";
import { useCompileSessionById, useWorkflow, useWorkflowDraft } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

/**
 * Draft của một workflow, chỉ đọc.
 *
 * Cùng thông tin với thẻ giả định trong cửa sổ biên dịch, ở trạng thái nghỉ: ý
 * đồ trước, các bước theo thứ tự chạy, rồi kết quả kiểm và những bước đã bị bỏ.
 * "Left out" đọc từ phiên biên dịch đã sinh ra draft; chỉ người mở phiên đó đọc
 * được nó, nên với người khác mục này vắng mặt.
 */
export default function WorkflowDraftPage() {
  const { id } = useParams<{ id: string }>();
  const { organization, workspace } = useTenant();
  const workspaceId = workspace?.id ?? null;

  const workflow = useWorkflow(organization.id, workspaceId, id);
  const draft = useWorkflowDraft(organization.id, workspaceId, id);
  const session = useCompileSessionById(
    organization.id,
    workspaceId,
    draft.data?.compiled_from ?? null,
  );

  if (!workspace) return <Spinner label="No workspace in this organisation." />;
  if (workflow.isPending || draft.isPending) return <Spinner label="Loading workflow…" />;
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

  const { ir, intent, validation } = draft.data;
  const inputs = Object.keys(
    ir.nodes.find((node) => node.type === "trigger")?.input_schema?.properties ?? {},
  );
  const dropped = session.data?.proposal?.dropped ?? [];
  const notes = validation.errors.length + validation.warnings.length;

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center gap-2.5 border-b border-line px-8">
        <Link href="/workflows" className="text-[13px] text-ink-4 hover:text-ink-2">
          Workflows
        </Link>
        <span className="text-line-strong">/</span>
        <h1 className="min-w-0 truncate text-[15px] font-semibold">{workflow.data.name}</h1>
        <span className="shrink-0 rounded-full bg-sidebar px-2 py-0.5 text-[11.5px] font-medium text-ink-3">
          Draft
        </span>
        <span className="grow" />
        <span className="shrink-0 text-[12px] text-ink-4">Read-only · publishing comes with the runner</span>
      </header>

      <div className="flex min-h-0 grow flex-col overflow-y-auto lg:flex-row lg:overflow-hidden">
        <div className="flex min-w-0 grow flex-col gap-[18px] px-8 py-6 lg:overflow-y-auto [&>*]:shrink-0">
          {intent ? <Intent intent={intent} compiled={draft.data.compiled_from !== null} /> : null}

          <Panel title="Steps, in the order they run" count={outlineCount(ir)}>
            <WorkflowOutline ir={ir} />
          </Panel>

          {inputs.length ? (
            <Panel title="You give it each run">
              {inputs.map((name) => {
                const sample = session.data?.proposal?.inputs.find(([input]) => input === name)?.[1];
                return (
                  <div key={name} className="flex items-center gap-2.5 border-t border-line-soft px-3 py-2">
                    <span className="font-mono text-[12.5px] font-medium">{name}</span>
                    <span className="grow" />
                    {sample ? (
                      <span className="font-mono text-[11.5px] text-ink-4">sample: {sample}</span>
                    ) : null}
                  </div>
                );
              })}
            </Panel>
          ) : null}
        </div>

        <aside className="flex w-full shrink-0 flex-col gap-3.5 border-t border-line bg-canvas px-5 py-6 lg:w-[372px] lg:overflow-y-auto lg:border-t-0 lg:border-l [&>*]:shrink-0">
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

          {session.data?.proposal && dropped.length ? (
            <Dropped proposal={session.data.proposal} />
          ) : null}
        </aside>
      </div>
    </div>
  );
}

function Intent({ intent, compiled }: { intent: WorkflowIntent; compiled: boolean }) {
  const groups: { title: string; items: string[] }[] = [
    { title: "Changes every run", items: intent.thay_doi },
    { title: "Stays the same", items: intent.co_dinh },
    { title: "Only when", items: intent.dieu_kien },
  ].filter((group) => group.items.length > 0);

  return (
    <section className="flex max-w-[760px] flex-col gap-2.5">
      <span className="text-[11px] font-semibold tracking-wide text-ink-4 uppercase">
        What this workflow is for
      </span>
      <p className="text-[16px] leading-normal font-medium">{intent.nhiem_vu}</p>
      {groups.map((group) => (
        <div key={group.title} className="flex flex-col gap-1">
          <span className="text-[12px] font-medium text-ink-3">{group.title}</span>
          <ul className="flex flex-col gap-0.5 text-[13px] leading-normal text-ink-2">
            {group.items.map((item, index) => (
              <li key={index} className="flex gap-2">
                <span className="text-line-strong">–</span>
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {compiled ? (
        <span className="text-[12.5px] text-ink-3">
          Accepted when this was compiled from a conversation.
        </span>
      ) : null}
    </section>
  );
}
