"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Markdown } from "@/components/markdown";
import { Dialog } from "@/components/ui";
import { Notice, outlineCount, Panel, WorkflowOutline } from "@/components/workflow-outline";
import type { Assumption, CompileProposal, CompileSession, WorkflowIntent } from "@/lib/api";
import { useCompileSession } from "@/lib/compile";

/**
 * Cửa sổ "Turn into Workflow": một cuộc trò chuyện riêng với Agent biên dịch.
 *
 * Ba bước, hai lần xác nhận (ADR-0015): chấp nhận ý đồ, rồi đồng ý với bảng giả
 * định. Không gì gõ ở đây đi vào conversation nguồn phía sau.
 *
 * Ý đồ và bảng giả định *đang hiện* được vẽ thành thẻ từ dữ liệu có cấu trúc;
 * các bản cũ hơn trong đoạn chat là markdown thu gọn, vì chúng chỉ còn là lịch sử.
 */
export function CompileDialog({
  organizationId,
  workspaceId,
  conversationId,
  conversationTitle,
  onClose,
}: {
  organizationId: string;
  workspaceId: string;
  conversationId: string;
  conversationTitle: string;
  onClose: () => void;
}) {
  const compile = useCompileSession({ organizationId, workspaceId, conversationId });
  const { session, busy, error, start } = compile;
  const [composer, setComposer] = useState("");
  const startedRef = useRef(false);
  const threadRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    void start();
  }, [start]);

  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight });
  }, [session?.revision, compile.progress.length, compile.said]);

  // Đang chờ model thì không đóng bằng Escape: một cú bấm trượt bỏ dở lời gọi đã tính tiền.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const status = session?.status ?? "awaiting_intent";
  const step = status === "compiled" ? 3 : status === "awaiting_confirmation" ? 2 : 1;

  function submit() {
    const text = composer.trim();
    if (!text || busy || !session) return;
    setComposer("");
    void compile.say(text);
  }

  return (
    <Dialog
      labelledBy="compile-title"
      onDismiss={busy ? undefined : onClose}
      className="flex h-[min(860px,calc(100dvh-48px))] max-w-[1040px] flex-col overflow-hidden"
    >
      <header className="flex h-16 shrink-0 items-center gap-4 border-b border-line px-6">
        <div className="flex min-w-0 flex-col">
          <h2 id="compile-title" className="text-[15.5px] font-semibold tracking-tight">
            Turn into Workflow
          </h2>
          <span className="truncate text-[12px] text-ink-4">
            From “{conversationTitle}” · the turns as they were when you opened this
          </span>
        </div>
        <div className="grow" />
        <Stepper step={step} />
        <span className="h-[22px] w-px bg-line" />
        <button
          type="button"
          onClick={onClose}
          disabled={busy}
          aria-label="Close"
          className="flex size-8 items-center justify-center rounded-md text-ink-3 hover:bg-sunken disabled:opacity-40"
        >
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
            <path d="M4 4l8 8M12 4l-8 8" />
          </svg>
        </button>
      </header>

      <div
        ref={threadRef}
        className="flex min-h-0 grow flex-col gap-4 overflow-y-auto bg-canvas px-6 py-5 [&>*]:shrink-0"
      >
        {session ? (
          <Thread
            session={session}
            busy={busy}
            onReopenIntent={() => void compile.reopenIntent()}
          />
        ) : null}

        {compile.said ? <UserBubble text={compile.said} /> : null}
        {busy ? <Progress lines={compile.progress} opening={!session} /> : null}
        {error ? (
          <p className="rounded-md border border-danger-line bg-surface px-3.5 py-2.5 text-[13px] text-danger">
            {error}
          </p>
        ) : null}
        {!session && !busy && error ? (
          <button
            type="button"
            onClick={() => void start()}
            className="self-start rounded-control border border-line-strong bg-surface px-3.5 py-2 text-[13px] font-medium hover:bg-sunken"
          >
            Try again
          </button>
        ) : null}
      </div>

      <Footer
        session={session}
        busy={busy}
        composer={composer}
        onComposer={setComposer}
        onSubmit={submit}
        onAcceptIntent={() => void compile.acceptIntent()}
        onConfirm={() => void compile.confirm()}
        onClose={onClose}
      />
    </Dialog>
  );
}

// ─── Đoạn chat của phiên ─────────────────────────────────────────────────────

function Thread({
  session,
  busy,
  onReopenIntent,
}: {
  session: CompileSession;
  busy: boolean;
  onReopenIntent: () => void;
}) {
  if (session.status === "compiled") return <Done session={session} />;

  const last = session.messages.length - 1;
  const atIntent = session.status === "awaiting_intent";
  // Thẻ đang hiện là tin nhắn cuối khi nó là ý đồ (bước 1) hoặc bảng giả định (bước 2).
  const cardIsLast =
    last >= 0 &&
    ((atIntent && session.messages[last].kind === "intent" && session.intent !== null) ||
      (!atIntent && session.messages[last].kind === "assumptions"));
  const history = cardIsLast ? session.messages.slice(0, last) : session.messages;

  return (
    <>
      {atIntent ? (
        <p className="max-w-[680px] text-[13px] leading-relaxed text-ink-3">
          Read from what was said in the conversation and which tools ran — not from the data they
          returned. Check it says what you want repeated; the workflow is built from this.
        </p>
      ) : session.intent ? (
        <AcceptedIntent intent={session.intent} disabled={busy} onChange={onReopenIntent} />
      ) : null}

      {history.map((message, index) =>
        message.kind === "user" ? (
          <UserBubble key={index} text={message.text} />
        ) : message.kind === "question" ? (
          <div key={index} className="max-w-[680px] rounded-[10px] border border-line bg-surface px-4 py-3">
            <Markdown>{message.text}</Markdown>
          </div>
        ) : !atIntent && message.kind === "intent" ? null : (
          <Earlier key={index} label={message.kind === "intent" ? "Earlier intent" : "Earlier assumptions"} text={message.text} />
        ),
      )}

      {cardIsLast && atIntent && session.intent ? <IntentCard intent={session.intent} /> : null}
      {cardIsLast && !atIntent ? <AssumptionCard session={session} /> : null}
    </>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex max-w-[620px] flex-col gap-1.5 self-end">
      <Eyebrow className="self-end">You</Eyebrow>
      <div className="rounded-[10px] bg-[#f0eefc] px-3.5 py-2.5 text-[13.5px] whitespace-pre-wrap">
        {text}
      </div>
    </div>
  );
}

/** Một đề xuất đã bị thay: còn đó để đọc lại, nhưng không chiếm chỗ. */
function Earlier({ label, text }: { label: string; text: string }) {
  return (
    <details className="max-w-[720px] rounded-[9px] border border-line bg-surface">
      <summary className="cursor-pointer px-3.5 py-2 text-[12.5px] text-ink-3 select-none">
        {label}
      </summary>
      <div className="border-t border-line-soft px-3.5 py-3">
        <Markdown>{text}</Markdown>
      </div>
    </details>
  );
}

function Progress({ lines, opening }: { lines: string[]; opening: boolean }) {
  // Dòng cuối là việc đang làm; các dòng trước nó đã xong.
  const shown = lines.length ? lines : [opening ? "Opening the session…" : "Working…"];
  return (
    <div className="flex flex-col gap-2.5 rounded-[10px] border border-line bg-surface px-4 py-3.5">
      {shown.map((line, index) => {
        const running = index === shown.length - 1;
        return (
          <div
            key={index}
            className={`flex items-center gap-2.5 text-[13.5px] ${running ? "font-medium" : "text-ink-3"}`}
          >
            {running ? (
              <span className="size-3.5 shrink-0 animate-spin rounded-full border-2 border-accent-line border-t-accent motion-reduce:animate-none" />
            ) : (
              <Check className="text-ok" />
            )}
            <span>{line}</span>
          </div>
        );
      })}
    </div>
  );
}

// ─── Thẻ ý đồ ────────────────────────────────────────────────────────────────

function IntentCard({ intent }: { intent: WorkflowIntent }) {
  const columns: { title: string; accent?: boolean; items: string[] }[] = [
    { title: "Changes every run", accent: true, items: intent.thay_doi },
    { title: "Stays the same", items: intent.co_dinh },
    { title: "Only when", items: intent.dieu_kien },
    { title: "Not part of it", items: intent.khong_thuoc_nhiem_vu },
  ].filter((column) => column.items.length > 0);

  return (
    <section className="overflow-hidden rounded-[10px] border border-line bg-surface">
      <div className="flex flex-col gap-2 px-[18px] pt-4 pb-3.5">
        <Eyebrow>What this workflow is for</Eyebrow>
        <p className="text-[16px] leading-normal font-medium">{intent.nhiem_vu}</p>
      </div>
      {columns.length ? (
        <div className="flex flex-wrap border-t border-line">
          {columns.map((column) => (
            <div
              key={column.title}
              className="flex min-w-[220px] flex-1 flex-col gap-2 border-r border-line px-[18px] py-3.5 last:border-r-0"
            >
              <Eyebrow className={column.accent ? "text-accent" : undefined}>{column.title}</Eyebrow>
              <ul className="flex flex-col gap-1 text-[13px] leading-normal text-ink-2">
                {column.items.map((item, index) => (
                  <li key={index} className="flex gap-2">
                    <span className="text-line-strong">–</span>
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function AcceptedIntent({
  intent,
  disabled,
  onChange,
}: {
  intent: WorkflowIntent;
  disabled: boolean;
  onChange: () => void;
}) {
  return (
    <div className="flex items-center gap-3 rounded-[9px] border border-line bg-surface px-3.5 py-2.5">
      <span className="flex size-5 shrink-0 items-center justify-center rounded-full bg-ok-wash">
        <Check className="text-ok" size={11} />
      </span>
      <div className="flex min-w-0 grow flex-col gap-px">
        <Eyebrow>Intent you accepted</Eyebrow>
        <span className="truncate text-[13px] text-ink-2" title={intent.nhiem_vu}>
          {intent.nhiem_vu}
        </span>
      </div>
      <button
        type="button"
        onClick={onChange}
        disabled={disabled}
        className="shrink-0 text-[12.5px] font-medium text-accent hover:text-accent-ink disabled:opacity-40"
      >
        Change the intent
      </button>
    </div>
  );
}

// ─── Thẻ giả định ────────────────────────────────────────────────────────────

function AssumptionCard({ session }: { session: CompileSession }) {
  const proposal = session.proposal;
  if (!proposal) {
    // Model không nộp được gì đọc được: chỉ còn lời backend đã viết.
    const text = session.messages[session.messages.length - 1]?.text ?? "";
    return (
      <Panel title="Not a valid workflow yet" tone="error">
        <div className="border-t border-line-soft px-3 py-2.5">
          <Markdown>{text}</Markdown>
        </div>
      </Panel>
    );
  }
  const values = [...proposal.linked, ...proposal.fixed, ...proposal.defaults];

  return (
    <div className="flex flex-wrap items-start gap-4">
      <div className="flex min-w-[340px] flex-1 flex-col gap-3">
        {proposal.inputs.length ? (
          <Panel title="You give it each run">
            {proposal.inputs.map(([name, sample]) => (
              <div key={name} className="flex items-center gap-2.5 border-t border-line-soft px-3 py-2">
                <span className="font-mono text-[12.5px] font-medium">{name}</span>
                <span className="grow" />
                <span className="truncate font-mono text-[11.5px] text-ink-4">sample: {sample}</span>
              </div>
            ))}
          </Panel>
        ) : null}
        {session.ir ? (
          <Panel title="What runs" count={outlineCount(session.ir)}>
            <WorkflowOutline ir={session.ir} />
          </Panel>
        ) : null}
        {proposal.dropped.length ? <Dropped proposal={proposal} /> : null}
      </div>

      <div className="flex min-w-[340px] flex-1 flex-col gap-3">
        {proposal.errors.length ? (
          <Panel title="Not a valid workflow yet" tone="error" count={String(proposal.errors.length)}>
            {proposal.errors.map((line, index) => (
              <Notice key={index} tone="error">
                {line}
              </Notice>
            ))}
            <p className="border-t border-line-soft px-3 py-2 text-[12px] leading-relaxed text-ink-3">
              Say it another way below, or change the intent.
            </p>
          </Panel>
        ) : null}
        {values.length ? (
          <Panel title="Where each value comes from" count={String(values.length)}>
            {values.map((item) => (
              <ValueRow key={item.question} item={item} />
            ))}
          </Panel>
        ) : null}
        {proposal.loops.length || proposal.conditions.length ? (
          <Panel title="Loops and conditions">
            {[...proposal.loops, ...proposal.conditions].map((line, index) => (
              <Lines key={index} text={line} />
            ))}
          </Panel>
        ) : null}
        {proposal.written.length ? (
          <Panel title="Written by the model — runs as is" count={String(proposal.written.length)}>
            {proposal.written.map((item) => (
              <Written key={item.id} item={item} />
            ))}
          </Panel>
        ) : null}
        {proposal.warnings.length ? (
          <Panel title="Worth a look" tone="warn" count={String(proposal.warnings.length)}>
            {proposal.warnings.map((line, index) => (
              <Notice key={index} tone="warn">
                {line}
              </Notice>
            ))}
          </Panel>
        ) : null}
      </div>
    </div>
  );
}

function ValueRow({ item }: { item: Assumption }) {
  return (
    <div className="flex flex-col gap-0.5 border-t border-line-soft px-3 py-2">
      <div className="flex items-center gap-2">
        <span className="shrink-0 font-mono text-[12px] font-medium">
          {item.node}.{item.field}
        </span>
        {item.by_position ? (
          <span className="shrink-0 rounded bg-[#fff3e0] px-1.5 py-0.5 text-[10.5px] font-semibold text-warn">
            BY POSITION
          </span>
        ) : null}
        <span className="grow" />
        <span className="min-w-0 truncate font-mono text-[11px] text-ink-4" title={item.value}>
          {item.value}
        </span>
      </div>
      <span className="text-[12.5px] text-ink-2">{item.detail}</span>
      {item.reason ? <span className="text-[12px] text-ink-3">{item.reason}</span> : null}
    </div>
  );
}

/** Một dòng của backend có thể kèm các dòng con thụt vào (`\n  - …`). */
function Lines({ text }: { text: string }) {
  const [head, ...rest] = text.split("\n  - ");
  return (
    <div className="flex flex-col gap-1 border-t border-line-soft px-3 py-2 text-[12.5px] leading-relaxed text-ink-2">
      <Markdown>{head}</Markdown>
      {rest.length ? (
        <ul className="flex flex-col gap-1 text-ink-3">
          {rest.map((line, index) => (
            <li key={index} className="flex gap-2">
              <span className="text-line-strong">–</span>
              <Markdown>{line}</Markdown>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function Written({ item }: { item: CompileProposal["written"][number] }) {
  return (
    <details className="group border-t border-line-soft px-3 py-2">
      <summary className="flex cursor-pointer list-none items-center gap-2 select-none">
        <span className="font-mono text-[12px] font-medium">{item.id}</span>
        <span className="min-w-0 truncate text-[11.5px] text-ink-4">{item.detail}</span>
        <span className="grow" />
        <span className="shrink-0 text-[12px] font-medium text-accent group-open:hidden">Show</span>
        <span className="hidden shrink-0 text-[12px] font-medium text-accent group-open:inline">Hide</span>
      </summary>
      <pre
        className={`mt-2 max-h-56 overflow-auto rounded-md border border-line bg-canvas p-2.5 text-[12.5px] leading-relaxed whitespace-pre-wrap ${
          item.kind === "code" ? "font-mono" : "font-sans"
        }`}
      >
        {item.text}
      </pre>
    </details>
  );
}

export function Dropped({ proposal }: { proposal: CompileProposal }) {
  return (
    <Panel title="Left out of the workflow" count={String(proposal.dropped.length)}>
      {proposal.dropped.map((step) => (
        <div key={step.step} className="flex flex-col gap-0.5 border-t border-line-soft px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="font-mono text-[12px] font-medium text-ink-3 line-through">
              {step.tool_id}
            </span>
            <span className="text-[11.5px] text-ink-4">turn {step.turn}</span>
          </div>
          <span className="text-[12.5px] leading-relaxed text-ink-2">
            {step.reason ?? "No reason given."}
          </span>
        </div>
      ))}
    </Panel>
  );
}

// ─── Xong ────────────────────────────────────────────────────────────────────

function Done({ session }: { session: CompileSession }) {
  return (
    <div className="flex grow flex-col items-center justify-center gap-4 py-10">
      <span className="flex size-11 items-center justify-center rounded-full bg-ok-wash">
        <Check className="text-ok" size={20} />
      </span>
      <div className="flex flex-col items-center gap-1.5">
        <h3 className="text-[19px] font-semibold tracking-tight">Draft created</h3>
        <p className="max-w-[460px] text-center text-[13.5px] leading-relaxed text-ink-3">
          “{session.proposal?.name ?? "The workflow"}” is a draft in Workflows. It does not run until
          it is published.
        </p>
      </div>
    </div>
  );
}

// ─── Chân cửa sổ ─────────────────────────────────────────────────────────────

function Footer({
  session,
  busy,
  composer,
  onComposer,
  onSubmit,
  onAcceptIntent,
  onConfirm,
  onClose,
}: {
  session: CompileSession | null;
  busy: boolean;
  composer: string;
  onComposer: (text: string) => void;
  onSubmit: () => void;
  onAcceptIntent: () => void;
  onConfirm: () => void;
  onClose: () => void;
}) {
  if (session?.status === "compiled") {
    return (
      <footer className="flex shrink-0 items-center justify-end gap-3 border-t border-line bg-surface px-6 py-3.5">
        <FooterButton onClick={onClose}>Back to the conversation</FooterButton>
        {session.workflow_id ? (
          <Link
            href={`/workflows/${session.workflow_id}`}
            className="flex h-10 shrink-0 items-center rounded-lg bg-ink px-4 text-[13.5px] font-medium text-white hover:bg-ink-2"
          >
            Open the draft
          </Link>
        ) : null}
      </footer>
    );
  }

  const atIntent = !session || session.status === "awaiting_intent";
  const canType = session !== null && !busy;
  return (
    <footer className="flex shrink-0 items-center gap-3 border-t border-line bg-surface px-6 py-3.5">
      <input
        id="compile-composer"
        value={composer}
        onChange={(event) => onComposer(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            onSubmit();
          }
        }}
        disabled={!canType}
        maxLength={4000}
        placeholder={
          busy
            ? "Wait for this to finish…"
            : atIntent
              ? "Tell it what to change — what varies, what stays, what to leave out…"
              : "Tell it what to change — a value, a step, the wording of a model step…"
        }
        className="h-10 min-w-0 grow rounded-lg border border-line-strong bg-surface px-3.5 text-[13.5px] outline-none placeholder:text-ink-4 focus:border-accent disabled:bg-sidebar"
      />
      {composer.trim() ? (
        <FooterButton onClick={onSubmit} disabled={!canType}>
          Send
        </FooterButton>
      ) : null}
      {atIntent ? (
        <FooterButton primary onClick={onAcceptIntent} disabled={!canType || !session?.intent}>
          Accept intent
        </FooterButton>
      ) : (
        <FooterButton primary onClick={onConfirm} disabled={!canType || !session?.is_ready}>
          Agree — create workflow
        </FooterButton>
      )}
    </footer>
  );
}

function FooterButton({
  primary,
  ...props
}: React.ComponentProps<"button"> & { primary?: boolean }) {
  const look = primary
    ? "bg-accent text-white hover:bg-accent-ink disabled:bg-rail disabled:text-ink-4"
    : "border border-line-strong bg-surface text-ink-2 hover:bg-sunken disabled:opacity-50";
  return (
    <button
      type="button"
      {...props}
      className={`flex h-10 shrink-0 items-center rounded-lg px-4 text-[13.5px] font-medium transition-colors ${look}`}
    />
  );
}

// ─── Mảnh nhỏ ────────────────────────────────────────────────────────────────

function Stepper({ step }: { step: number }) {
  const labels = ["Intent", "Assumptions", "Draft"];
  return (
    <ol className="hidden items-center gap-2.5 md:flex">
      {labels.map((label, index) => {
        const number = index + 1;
        const state = number < step ? "done" : number === step ? "now" : "next";
        return (
          <li key={label} className="flex items-center gap-2.5">
            {index > 0 ? <span className="h-px w-5 bg-line-strong" /> : null}
            <span className="flex items-center gap-1.5">
              {state === "done" ? (
                <span className="flex size-5 items-center justify-center rounded-full bg-ok">
                  <Check className="text-white" size={11} />
                </span>
              ) : (
                <span
                  className={`flex size-5 items-center justify-center rounded-full text-[11px] font-semibold ${
                    state === "now" ? "bg-accent text-white" : "border border-line-strong text-ink-4"
                  }`}
                >
                  {number}
                </span>
              )}
              <span
                className={`text-[12.5px] ${
                  state === "now" ? "font-semibold" : state === "done" ? "text-ink-2" : "text-ink-4"
                }`}
              >
                {label}
              </span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function Eyebrow({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <span className={`text-[11px] font-semibold tracking-wide uppercase ${className ?? "text-ink-4"}`}>
      {children}
    </span>
  );
}

function Check({ className, size = 14 }: { className?: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.1"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${className ?? ""}`}
      aria-hidden
    >
      <path d="M3.2 8.4l3 3 6.6-6.8" />
    </svg>
  );
}
