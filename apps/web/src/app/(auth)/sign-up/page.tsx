"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";
import { Button, ErrorNote, Field, Input } from "@/components/ui";
import { useSession } from "@/lib/session";

/**
 * Đăng ký giữ đúng ba trường.
 *
 * Đặt tên organization là bước đầu của wizard thiết lập, không phải trường ở
 * đây — backend cấp sẵn một organization lúc đăng ký, và `/onboarding` là chỗ
 * người dùng đặt tên cùng địa chỉ cho nó.
 */
export default function SignUpPage() {
  const router = useRouter();
  const { signUp } = useSession();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await signUp(email, password, displayName);
      router.replace("/onboarding");
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-[34px] font-semibold tracking-tight">Create an account</h1>
        <p className="text-base text-ink-3">You will name your organisation in the next step.</p>
      </div>

      <div className="flex flex-col gap-4">
        <Field label="Your name">
          <Input
            autoComplete="name"
            required
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            placeholder="An Hoang"
          />
        </Field>
        <Field label="Work email">
          <Input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@company.com"
          />
        </Field>
        <Field label="Password" hint="At least 12 characters. Length is all that is checked.">
          <Input
            type="password"
            autoComplete="new-password"
            required
            minLength={12}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>

        <ErrorNote error={error} />

        <Button type="submit" disabled={busy}>
          {busy ? "Creating…" : "Create account"}
        </Button>
      </div>

      <p className="text-[15px] text-ink-3">
        Already have an account?{" "}
        <Link href="/sign-in" className="font-medium text-accent hover:text-accent-ink">
          Sign in
        </Link>
      </p>
    </form>
  );
}
