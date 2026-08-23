"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { Button, ErrorNote, Field, Input } from "@/components/ui";
import { useSession } from "@/lib/session";

export default function SignInPage() {
  const router = useRouter();
  const { signIn, status } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  // Đã có phiên rồi thì không có lý do gì ở lại màn đăng nhập.
  useEffect(() => {
    if (status === "authenticated") router.replace("/agent");
  }, [status, router]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await signIn(email, password);
      router.replace("/agent");
    } catch (caught) {
      setError(caught);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="text-[34px] font-semibold tracking-tight">Sign in</h1>
        <p className="text-base text-ink-3">Welcome back. Pick up where your agents left off.</p>
      </div>

      <div className="flex flex-col gap-4">
        <Field label="Email">
          <Input
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@company.com"
          />
        </Field>
        <Field label="Password">
          <Input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>

        <ErrorNote error={error} />

        <Button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </div>

      <p className="text-[15px] text-ink-3">
        New here?{" "}
        <Link href="/sign-up" className="font-medium text-accent hover:text-accent-ink">
          Create an account
        </Link>
      </p>
    </form>
  );
}
