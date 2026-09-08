"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { AgentChat } from "@/components/agent-chat";
import { ArchiveAgentDialog } from "@/components/archive-agent-dialog";
import { CreateAgentDialog } from "@/components/create-agent-dialog";
import { ErrorNote, Spinner } from "@/components/ui";
import type { Agent } from "@/lib/api";
import { modelLabel } from "@/lib/models";
import { useAgents, useUnarchiveAgent } from "@/lib/queries";
import { initials } from "@/lib/name";
import { reachesEveryWorkspace } from "@/lib/roles";
import { useSession } from "@/lib/session";
import { useTenant } from "@/lib/tenant";

/**
 * Agent: một thư viện các trợ lý đã cấu hình, và màn chat với từng cái.
 *
 * Gallery hay chat được quyết bởi query `?agent=<id>` (và `?c=<conversation>`)
 * chứ không phải route con — một trang, hai trạng thái, URL vẫn chia sẻ được.
 */
export default function AgentPage() {
  const router = useRouter();
  const params = useSearchParams();
  const { organization, workspace } = useTenant();
  const { user } = useSession();

  const agents = useAgents(organization.id, workspace?.id ?? null);
  const [creating, setCreating] = useState(false);
  const [archiving, setArchiving] = useState<Agent | null>(null);

  // Ai được lưu trữ/khôi phục: người tạo agent, hoặc người quản lý được mọi
  // workspace (owner/admin org). Xấp xỉ `ensure_can_manage` ở backend — backend
  // vẫn enforce thật, đây chỉ để không mời bấm thứ chắc chắn bị từ chối.
  const canManageAny = reachesEveryWorkspace(organization.role);
  const canManage = (agent: Agent) => canManageAny || agent.created_by === user?.id;

  const activeAgentId = params.get("agent");
  const activeConversationId = params.get("c");
  const activeAgent = agents.data?.find((a) => a.id === activeAgentId) ?? null;

  const { active, archived } = useMemo(() => {
    const rows = agents.data ?? [];
    return {
      active: rows.filter((a) => a.archived_at === null),
      archived: rows.filter((a) => a.archived_at !== null),
    };
  }, [agents.data]);

  function openAgent(agentId: string, conversationId?: string | null) {
    const query = new URLSearchParams({ agent: agentId });
    if (conversationId) query.set("c", conversationId);
    router.push(`/agent?${query.toString()}`);
  }

  function backToGallery() {
    router.push("/agent");
  }

  if (!workspace) return <Spinner label="No workspace in this organisation." />;

  if (activeAgentId) {
    if (agents.isPending) return <Spinner label="Loading agent…" />;
    if (!activeAgent) {
      return (
        <EmptyState
          title="Agent not found"
          body="It may have been archived or removed, or belongs to another workspace."
          action={{ label: "Back to agents", onClick: backToGallery }}
        />
      );
    }
    return (
      <AgentChat
        agent={activeAgent}
        conversationId={activeConversationId}
        onBack={backToGallery}
        onSelectConversation={(conversationId) =>
          openAgent(activeAgent.id, conversationId ?? undefined)
        }
      />
    );
  }

  return (
    <div className="flex h-full flex-col">
      <header className="flex h-16 shrink-0 items-center border-b border-line px-8">
        <span className="text-base font-semibold">Agent</span>
        <div className="grow" />
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="inline-flex h-9 items-center gap-2 rounded-control bg-ink px-3.5 text-sm font-medium text-white hover:bg-ink-2"
        >
          <IconPlus />
          New agent
        </button>
      </header>

      <div className="min-h-0 grow overflow-y-auto p-8">
        <p className="mb-5 max-w-2xl text-[13.5px] leading-relaxed text-ink-3">
          Each agent has its own system prompt, model and tool whitelist. Pick one to open its
          conversations, or set up a new one.
        </p>

        <ErrorNote error={agents.error} />

        {agents.isPending ? (
          <span className="text-sm text-ink-4">Loading agents…</span>
        ) : (
          <>
            <div className="grid max-w-5xl grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
              {active.map((agent) => (
                <AgentCard
                  key={agent.id}
                  agent={agent}
                  onOpen={() => openAgent(agent.id)}
                  onArchive={canManage(agent) ? () => setArchiving(agent) : undefined}
                />
              ))}

              <button
                type="button"
                onClick={() => setCreating(true)}
                className="flex min-h-[172px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line-strong text-ink-4 hover:border-accent hover:text-accent"
              >
                <IconPlusLarge />
                <span className="text-[13px] font-medium">New agent</span>
              </button>
            </div>

            {archived.length > 0 ? (
              <ArchivedSection agents={archived} canManage={canManage} />
            ) : null}
          </>
        )}
      </div>

      {creating ? (
        <CreateAgentDialog
          onClose={() => setCreating(false)}
          onCreated={(agentId) => {
            setCreating(false);
            openAgent(agentId);
          }}
        />
      ) : null}

      {archiving ? (
        <ArchiveAgentDialog
          agent={archiving}
          onClose={() => setArchiving(null)}
          onArchived={() => setArchiving(null)}
        />
      ) : null}
    </div>
  );
}

function AgentCard({
  agent,
  onOpen,
  onArchive,
}: {
  agent: Agent;
  onOpen: () => void;
  onArchive?: () => void;
}) {
  const toolCount = agent.enabled_tool_ids.length;
  return (
    <div className="group relative">
      <button
        type="button"
        onClick={onOpen}
        className="flex min-h-[172px] w-full flex-col gap-3.5 rounded-xl border border-line bg-surface p-4.5 text-left transition-colors hover:border-line-strong"
      >
        <div className="flex items-center gap-2.5">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-accent-line bg-accent-wash text-[13px] font-semibold text-accent">
            {initials(agent.name)}
          </span>
          <div className="flex min-w-0 flex-col">
            <span className="truncate text-[15px] font-semibold">{agent.name}</span>
            <span className="truncate font-mono text-[12.5px] text-ink-4">
              {modelLabel(agent.model)}
            </span>
          </div>
        </div>
        <p className="line-clamp-2 text-[13px] leading-snug text-ink-3">
          {agent.system_prompt || "No system prompt."}
        </p>
        <div className="grow" />
        <div className="flex items-center gap-1.5 text-[12px] text-ink-4">
          <IconTool />
          <span>
            {toolCount === 0 ? "No tools" : toolCount === 1 ? "1 tool" : `${toolCount} tools`}
          </span>
        </div>
      </button>

      {onArchive ? (
        <button
          type="button"
          onClick={onArchive}
          aria-label={`Archive ${agent.name}`}
          title="Archive"
          className="absolute top-2.5 right-2.5 flex size-7 items-center justify-center rounded-md border border-line bg-surface text-ink-4 opacity-0 transition-opacity group-hover:opacity-100 hover:border-danger-line hover:text-danger focus-visible:opacity-100"
        >
          <IconTrash />
        </button>
      ) : null}
    </div>
  );
}

function ArchivedSection({
  agents,
  canManage,
}: {
  agents: Agent[];
  canManage: (agent: Agent) => boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-8 max-w-5xl border-t border-line-soft pt-5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 text-[11px] font-semibold tracking-wider text-ink-4 uppercase hover:text-ink-3"
      >
        <IconChevron open={open} />
        Archived ({agents.length})
      </button>

      {open ? (
        <div className="mt-3 flex flex-col gap-2">
          {agents.map((agent) => (
            <ArchivedRow key={agent.id} agent={agent} canManage={canManage(agent)} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ArchivedRow({ agent, canManage }: { agent: Agent; canManage: boolean }) {
  const { organization, workspace } = useTenant();
  const unarchive = useUnarchiveAgent(organization.id, workspace?.id ?? null);
  return (
    <div className="flex items-center gap-3 rounded-lg border border-line bg-canvas px-3.5 py-2.5">
      <span className="flex size-7 shrink-0 items-center justify-center rounded-md border border-line-strong bg-sunken text-[11px] font-semibold text-ink-3">
        {initials(agent.name)}
      </span>
      <div className="flex min-w-0 grow flex-col">
        <span className="truncate text-[13.5px] font-medium text-ink-2">{agent.name}</span>
        <span className="truncate font-mono text-[11.5px] text-ink-4">{modelLabel(agent.model)}</span>
      </div>
      {canManage ? (
        <button
          type="button"
          onClick={() => unarchive.mutate(agent.id)}
          disabled={unarchive.isPending}
          className="h-7 shrink-0 rounded-control border border-line-strong bg-surface px-2.5 text-xs font-medium hover:bg-sunken disabled:opacity-50"
        >
          {unarchive.isPending ? "Restoring…" : "Restore"}
        </button>
      ) : null}
    </div>
  );
}

function EmptyState({
  title,
  body,
  action,
}: {
  title: string;
  body: string;
  action: { label: string; onClick: () => void };
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
      <p className="max-w-md text-[13.5px] leading-relaxed text-ink-3">{body}</p>
      <button
        type="button"
        onClick={action.onClick}
        className="mt-1 inline-flex h-9 items-center rounded-control border border-line-strong bg-surface px-3.5 text-sm font-medium hover:bg-sunken"
      >
        {action.label}
      </button>
    </div>
  );
}

function IconPlus() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" aria-hidden>
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

function IconPlusLarge() {
  return (
    <svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" aria-hidden>
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

function IconTool() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M6 2v3.5M10 2v3.5M4.5 5.5h7v2.2a3.5 3.5 0 01-7 0zM8 11.2V14" />
    </svg>
  );
}

function IconTrash() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M2.5 4.5h11M6 4.5V3a1 1 0 011-1h2a1 1 0 011 1v1.5M4 4.5l.6 8.2a1 1 0 001 .8h4.8a1 1 0 001-.8l.6-8.2" />
    </svg>
  );
}

function IconChevron({ open }: { open: boolean }) {
  return (
    <svg
      width="11"
      height="11"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`transition-transform ${open ? "rotate-90" : ""}`}
      aria-hidden
    >
      <path d="M6 4l4 4-4 4" />
    </svg>
  );
}
