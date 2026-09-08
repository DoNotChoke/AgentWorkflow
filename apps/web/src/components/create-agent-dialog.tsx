"use client";

import { type FormEvent, useEffect, useMemo, useState } from "react";
import { ProviderTile } from "@/components/provider-icon";
import { Button, Dialog, ErrorNote } from "@/components/ui";
import type { ToolSummary } from "@/lib/api";
import { DEFAULT_MODEL_ID, MODEL_OPTIONS } from "@/lib/models";
import { useCreateAgent, useTools } from "@/lib/queries";
import { useTenant } from "@/lib/tenant";

/**
 * Tạo một Agent — prompt hệ thống, model, và whitelist tool.
 *
 * Hai bước: thông tin cơ bản trước, chọn tool sau. Whitelist là tường minh
 * (agent không có tool nào thì không gọi được gì) nên bước tool không bị bỏ
 * qua được, nhưng để trống vẫn tạo được — một agent chỉ để hỏi đáp là hợp lệ.
 */
export function CreateAgentDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (agentId: string) => void;
}) {
  const { organization, workspace } = useTenant();
  const tools = useTools();
  const create = useCreateAgent(organization.id, workspace?.id ?? null);

  const [step, setStep] = useState<"basics" | "tools">("basics");
  const [name, setName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [model, setModel] = useState(DEFAULT_MODEL_ID);
  const [maxToolCalls, setMaxToolCalls] = useState(8);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [search, setSearch] = useState("");
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !create.isPending) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose, create.isPending]);

  const groups = useMemo(() => groupTools(tools.data ?? [], search), [tools.data, search]);
  const selectedCount = Object.values(selected).filter(Boolean).length;
  const nameEmpty = name.trim().length === 0;

  // Enter trong ô Name (bước 1 chỉ có một input text → trình duyệt tự submit
  // form) không được tạo agent luôn — nó chỉ chuyển sang bước chọn tool. Chỉ
  // submit ở bước 2 mới thật sự tạo.
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (step === "basics") {
      if (!nameEmpty) setStep("tools");
      return;
    }
    setError(null);
    try {
      const agent = await create.mutateAsync({
        name: name.trim(),
        system_prompt: systemPrompt.trim(),
        model,
        enabled_tool_ids: Object.keys(selected).filter((id) => selected[id]),
        max_tool_calls_per_turn: maxToolCalls,
      });
      onCreated(agent.id);
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <Dialog
      labelledBy="create-agent-title"
      onDismiss={create.isPending ? undefined : onClose}
      className="flex max-h-[min(780px,90vh)] max-w-[640px] flex-col"
    >
      <form onSubmit={submit} className="flex min-h-0 flex-col">
        <header className="flex shrink-0 items-center justify-between border-b border-line-soft px-6 pt-5 pb-4">
          <div className="flex flex-col gap-0.5">
            <h2 id="create-agent-title" className="text-lg font-semibold tracking-tight">
              New agent
            </h2>
            <span className="text-[12.5px] text-ink-4">
              {step === "basics" ? "Step 1 of 2 · Basics" : "Step 2 of 2 · Tools"}
            </span>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={create.isPending}
            aria-label="Close"
            className="rounded-md p-1.5 text-ink-4 hover:bg-sunken disabled:opacity-50"
          >
            <IconClose />
          </button>
        </header>

        <div className="min-h-0 grow overflow-y-auto p-6">
          {step === "basics" ? (
            <div className="flex flex-col gap-5">
              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink-2">Name</span>
                <input
                  autoFocus
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  placeholder="Support Triage"
                  className="h-11 rounded-control border border-line-strong bg-surface px-3.5 text-base outline-none focus:border-accent focus:ring-1 focus:ring-accent"
                />
              </label>

              <label className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink-2">System prompt</span>
                <textarea
                  value={systemPrompt}
                  onChange={(event) => setSystemPrompt(event.target.value)}
                  rows={3}
                  placeholder="What should this agent do, and how?"
                  className="resize-y rounded-control border border-line-strong bg-surface px-3.5 py-2.5 text-[15px] leading-relaxed outline-none focus:border-accent focus:ring-1 focus:ring-accent"
                />
                <span className="text-[13px] leading-relaxed text-ink-4">
                  Optional — leave blank for no special instructions.
                </span>
              </label>

              <div className="flex flex-col gap-2">
                <span className="text-[11px] font-semibold tracking-wider text-ink-4 uppercase">
                  Model
                </span>
                <div className="flex flex-col gap-2">
                  {MODEL_OPTIONS.map((option) => {
                    const active = option.id === model;
                    return (
                      <button
                        key={option.id}
                        type="button"
                        onClick={() => setModel(option.id)}
                        className={`flex items-start gap-3 rounded-lg border px-3.5 py-3 text-left transition-colors ${
                          active
                            ? "border-accent bg-accent-wash"
                            : "border-line bg-surface hover:border-line-strong"
                        }`}
                      >
                        <span
                          className={`mt-0.5 size-4 shrink-0 rounded-full border bg-surface ${
                            active ? "border-[5px] border-accent" : "border-[1.5px] border-line-strong"
                          }`}
                        />
                        <span className="flex flex-col gap-0.5">
                          <span className="text-sm font-medium">{option.label}</span>
                          <span className="text-[12.5px] leading-snug text-ink-3">
                            {option.blurb}
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <span className="text-sm font-medium text-ink-2">Max tool calls per turn</span>
                <div className="flex items-center gap-2.5">
                  <Stepper
                    label="Fewer"
                    onClick={() => setMaxToolCalls((n) => Math.max(1, n - 1))}
                  >
                    −
                  </Stepper>
                  <span className="w-8 text-center font-mono text-sm">{maxToolCalls}</span>
                  <Stepper
                    label="More"
                    onClick={() => setMaxToolCalls((n) => Math.min(50, n + 1))}
                  >
                    +
                  </Stepper>
                  <span className="text-[12.5px] text-ink-4">
                    Stops the loop after this many tool calls in one turn.
                  </span>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex flex-col gap-3.5">
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") event.preventDefault();
                }}
                placeholder="Search tools…"
                className="h-10 rounded-control border border-line-strong bg-surface px-3 text-sm outline-none focus:border-accent focus:ring-1 focus:ring-accent"
              />
              <span className="text-[12.5px] text-ink-4">
                {selectedCount === 0
                  ? "No tools selected yet"
                  : selectedCount === 1
                    ? "1 tool selected"
                    : `${selectedCount} tools selected`}
              </span>

              {tools.isPending ? (
                <span className="text-sm text-ink-4">Loading tools…</span>
              ) : tools.isError ? (
                <ErrorNote error={tools.error} />
              ) : groups.length === 0 ? (
                <p className="rounded-lg border border-dashed border-line-strong px-4 py-3.5 text-center text-[13px] text-ink-4">
                  {search ? "No tools match your search." : "No tools available yet."}
                </p>
              ) : (
                groups.map((group) => (
                  <div key={group.provider} className="flex flex-col gap-1.5">
                    <span className="flex items-center gap-1.5 text-[11px] font-semibold tracking-wider text-ink-4 uppercase">
                      <ProviderTile provider={group.provider} box={16} icon={11} />
                      {group.provider}
                    </span>
                    {group.tools.map((tool) => {
                      const checked = !!selected[tool.tool_id];
                      return (
                        <label
                          key={tool.tool_id}
                          className={`flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 transition-colors ${
                            checked
                              ? "border-accent bg-accent-wash"
                              : "border-line bg-surface hover:border-line-strong"
                          }`}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() =>
                              setSelected((prev) => ({
                                ...prev,
                                [tool.tool_id]: !prev[tool.tool_id],
                              }))
                            }
                            className="mt-0.5 size-4 shrink-0 accent-accent"
                          />
                          <span className="flex min-w-0 grow flex-col gap-0.5">
                            <span className="flex items-center gap-1.5">
                              <span className="font-mono text-[13px] font-medium">
                                {tool.tool_id}
                              </span>
                              <EffectBadge sideEffect={tool.side_effect} />
                            </span>
                            <span className="text-[12.5px] leading-snug text-ink-3">
                              {tool.description}
                            </span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                ))
              )}
            </div>
          )}
        </div>

        {error ? (
          <div className="shrink-0 px-6 pt-3">
            <ErrorNote error={error} />
          </div>
        ) : null}

        <footer className="flex shrink-0 items-center justify-end gap-2.5 border-t border-line-soft bg-canvas px-6 py-4">
          {step === "basics" ? (
            <>
              <Button
                type="button"
                variant="secondary"
                onClick={onClose}
                className="h-10 text-sm"
              >
                Cancel
              </Button>
              <Button type="submit" disabled={nameEmpty} className="h-10 text-sm">
                Next: choose tools
              </Button>
            </>
          ) : (
            <>
              <Button
                type="button"
                variant="secondary"
                onClick={() => setStep("basics")}
                className="h-10 text-sm"
              >
                Back
              </Button>
              <Button type="submit" disabled={create.isPending || nameEmpty} className="h-10 text-sm">
                {create.isPending ? "Creating…" : "Create agent"}
              </Button>
            </>
          )}
        </footer>
      </form>
    </Dialog>
  );
}

function groupTools(tools: ToolSummary[], search: string) {
  const q = search.trim().toLowerCase();
  const match = (tool: ToolSummary) =>
    !q ||
    tool.tool_id.toLowerCase().includes(q) ||
    tool.description.toLowerCase().includes(q);

  const byProvider = new Map<string, ToolSummary[]>();
  for (const tool of tools) {
    if (!match(tool)) continue;
    const list = byProvider.get(tool.provider) ?? [];
    list.push(tool);
    byProvider.set(tool.provider, list);
  }
  return [...byProvider.entries()]
    .map(([provider, list]) => ({
      provider,
      tools: list.sort((a, b) => a.tool_id.localeCompare(b.tool_id)),
    }))
    .sort((a, b) => a.provider.localeCompare(b.provider));
}

function EffectBadge({ sideEffect }: { sideEffect: string }) {
  const write = sideEffect === "write";
  return (
    <span
      className={`inline-flex rounded px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase ${
        write ? "bg-warn-wash text-warn" : "bg-sunken text-ink-3"
      }`}
    >
      {write ? "Write" : "Read"}
    </span>
  );
}

function Stepper({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="size-8 rounded-control border border-line-strong bg-surface text-base text-ink-2 hover:bg-sunken"
    >
      {children}
    </button>
  );
}

function IconClose() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      aria-hidden
    >
      <path d="M4 4l8 8M12 4l-8 8" />
    </svg>
  );
}
