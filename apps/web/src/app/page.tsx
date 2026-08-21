"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { Spinner } from "@/components/ui";
import { useSession } from "@/lib/session";

/**
 * Trang gốc chỉ là một ngã ba.
 *
 * Không thể quyết định trên server: phiên nằm trong cookie httpOnly mà chỉ
 * backend đọc được, còn access token thì sống trong bộ nhớ của client.
 */
export default function Home() {
  const router = useRouter();
  const { status } = useSession();

  useEffect(() => {
    if (status === "authenticated") router.replace("/agent");
    if (status === "anonymous") router.replace("/sign-in");
  }, [status, router]);

  return <Spinner label="Loading…" />;
}
