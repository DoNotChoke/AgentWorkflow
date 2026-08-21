import { Logo } from "@/components/ui";

/** Khung chung cho đăng nhập và đăng ký: form bên trái, luận điểm sản phẩm bên phải. */
export default function AuthLayout({ children }: LayoutProps<"/">) {
  return (
    <div className="flex h-full">
      <div className="flex w-full flex-col p-10 lg:w-[560px] lg:shrink-0 lg:px-16">
        <div className="flex items-center gap-2.5">
          <Logo size={22} />
          <span className="text-[15px] font-semibold tracking-tight">AgentWorkflow</span>
        </div>
        <div className="flex grow flex-col justify-center">
          <div className="w-full max-w-[380px]">{children}</div>
        </div>
      </div>

      <aside className="hidden grow flex-col justify-center gap-7 border-l border-line bg-sunken px-14 lg:flex">
        <p className="max-w-[340px] text-[19px] leading-snug font-semibold tracking-tight">
          Show an agent how the job is done. Keep the result as an automation.
        </p>
        <ol className="flex flex-col">
          {[
            ["Ask in plain language", "The agent picks its own tools across your connected apps."],
            ["Every step is recorded", "A structured execution trace, not a chat log."],
            [
              "Compile it into a Workflow",
              "Versioned, retried, scheduled — and it runs the same way every time.",
            ],
          ].map(([title, body], index, all) => (
            <li key={title} className="flex gap-3.5">
              <div className="flex w-[22px] shrink-0 flex-col items-center">
                <span
                  className={`flex size-[22px] items-center justify-center rounded-full text-[11px] font-semibold ${
                    index === all.length - 1
                      ? "bg-accent text-white"
                      : "border border-[#dcdcd7] bg-surface text-ink-3"
                  }`}
                >
                  {index + 1}
                </span>
                {index < all.length - 1 ? <span className="w-px grow bg-[#dcdcd7]" /> : null}
              </div>
              <div className="flex flex-col gap-0.5 pb-5">
                <span className="text-[13.5px] font-medium">{title}</span>
                <span className="text-[12.5px] leading-relaxed text-ink-3">{body}</span>
              </div>
            </li>
          ))}
        </ol>
      </aside>
    </div>
  );
}
