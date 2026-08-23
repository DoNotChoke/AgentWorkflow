import { Logo } from "@/components/ui";

/** Khung chung cho đăng nhập và đăng ký: form bên trái, luận điểm sản phẩm bên phải. */
export default function AuthLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="flex h-full min-h-[100dvh] w-full">
      <div className="flex w-full flex-col p-8 sm:p-12 lg:w-1/2 lg:shrink-0 lg:px-[clamp(4rem,8vw,9rem)] lg:py-14">
        <div className="flex items-center gap-2.5">
          <Logo size={28} />
          <span className="text-lg font-semibold tracking-tight">AgentWorkflow</span>
        </div>
        <div className="flex grow flex-col justify-center">
          <div className="w-full max-w-[480px]">{children}</div>
        </div>
      </div>

      <aside className="hidden grow flex-col justify-center gap-10 border-l border-line bg-sunken px-[clamp(4rem,8vw,9rem)] lg:flex">
        <p className="max-w-[520px] text-[28px] leading-snug font-semibold tracking-tight">
          Show an agent how the job is done. Keep the result as an automation.
        </p>
        <ol className="flex max-w-[500px] flex-col">
          {[
            ["Ask in plain language", "The agent picks its own tools across your connected apps."],
            ["Every step is recorded", "A structured execution trace, not a chat log."],
            [
              "Compile it into a Workflow",
              "Versioned, retried, scheduled — and it runs the same way every time.",
            ],
          ].map(([title, body], index, all) => (
            <li key={title} className="flex gap-4">
              <div className="flex w-7 shrink-0 flex-col items-center">
                <span
                  className={`flex size-7 items-center justify-center rounded-full text-xs font-semibold ${
                    index === all.length - 1
                      ? "bg-accent text-white"
                      : "border border-[#dcdcd7] bg-surface text-ink-3"
                  }`}
                >
                  {index + 1}
                </span>
                {index < all.length - 1 ? <span className="w-px grow bg-[#dcdcd7]" /> : null}
              </div>
              <div className="flex flex-col gap-1 pb-7">
                <span className="text-base font-medium">{title}</span>
                <span className="text-sm leading-relaxed text-ink-3">{body}</span>
              </div>
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}
