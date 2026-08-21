"use client";

import type { ComponentProps, ReactNode } from "react";
import { ApiError } from "@/lib/api";

/** Các mảnh giao diện dùng lại, bám theo token trong `globals.css`. */

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[12.5px] font-medium text-ink-2">{label}</span>
      {children}
      {hint ? <span className="text-xs leading-relaxed text-ink-4">{hint}</span> : null}
    </label>
  );
}

export function Input(props: ComponentProps<"input">) {
  return (
    <input
      {...props}
      className={`h-[38px] rounded-control border border-line-strong bg-surface px-3 text-sm
        outline-none transition-colors placeholder:text-ink-4
        focus:border-accent focus:ring-1 focus:ring-accent ${props.className ?? ""}`}
    />
  );
}

export function Button({
  variant = "primary",
  ...props
}: ComponentProps<"button"> & { variant?: "primary" | "secondary" }) {
  const base =
    "inline-flex h-10 items-center justify-center gap-2 rounded-control px-3.5 text-sm font-medium transition-colors disabled:opacity-50";
  const look =
    variant === "primary"
      ? "bg-ink text-white hover:bg-ink-2"
      : "border border-line-strong bg-surface text-ink hover:bg-sunken";
  return <button {...props} className={`${base} ${look} ${props.className ?? ""}`} />;
}

/**
 * Hiển thị lỗi từ API.
 *
 * Backend đã trả thông điệp đọc được ở `error.message` (§37), nên ở đây không
 * diễn giải lại — chỉ những lỗi không phải `ApiError` mới cần chuỗi dự phòng,
 * vì chúng là sự cố mạng hoặc bug chứ không phải câu trả lời của hệ thống.
 */
export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const message =
    error instanceof ApiError
      ? error.message
      : "Could not reach the server. Check that the API is running.";
  return (
    <p className="rounded-md border border-danger-line bg-white px-3 py-2 text-[13px] text-danger">
      {message}
    </p>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center">
      <span className="text-[13px] text-ink-4">{label}</span>
    </div>
  );
}

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="1.5" y="1.5" width="21" height="21" rx="6" className="fill-accent" />
      <path
        d="M7 15.5L12 7l5 8.5"
        stroke="#fff"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M9.2 12.6h5.6" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}
