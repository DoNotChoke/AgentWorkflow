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
      <span className="text-sm font-medium text-ink-2">{label}</span>
      {children}
      {hint ? <span className="text-[13px] leading-relaxed text-ink-4">{hint}</span> : null}
    </label>
  );
}

export function Input(props: ComponentProps<"input">) {
  return (
    <input
      {...props}
      className={`h-11 rounded-control border border-line-strong bg-surface px-3.5 text-base
        outline-none transition-colors placeholder:text-ink-4
        focus:border-accent focus:ring-1 focus:ring-accent ${props.className ?? ""}`}
    />
  );
}

export function Button({
  variant = "primary",
  ...props
}: ComponentProps<"button"> & { variant?: "primary" | "secondary" | "danger" }) {
  const base =
    "inline-flex h-11 items-center justify-center gap-2 rounded-control px-4 text-[15px] font-medium transition-colors disabled:opacity-50";
  const look = {
    primary: "bg-ink text-white hover:bg-ink-2",
    secondary: "border border-line-strong bg-surface text-ink hover:bg-sunken",
    danger: "bg-danger text-white hover:bg-[#9a1e14]",
  }[variant];
  return <button {...props} className={`${base} ${look} ${props.className ?? ""}`} />;
}

/**
 * Vỏ hộp thoại: nền mờ, khung nổi, đóng khi bấm ra ngoài.
 *
 * Bỏ trống `onDismiss` là khoá lại — dùng khi đang gửi request, để một cú bấm
 * trượt không bỏ dở việc đã bắt đầu. Phím Escape do từng hộp thoại tự bắt, vì
 * chỉ nó biết lúc nào mình đang bận.
 */
export function Dialog({
  labelledBy,
  onDismiss,
  className,
  children,
}: {
  labelledBy: string;
  onDismiss?: () => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/25 p-6"
      onMouseDown={(event) => {
        // Chỉ đóng khi bấm đúng vào nền, không phải khi kéo chuột từ trong ra.
        if (event.target === event.currentTarget) onDismiss?.();
      }}
    >
      <div
        role="dialog"
        aria-modal
        aria-labelledby={labelledBy}
        className={`w-full rounded-xl border border-line bg-surface shadow-xl ${className ?? ""}`}
      >
        {children}
      </div>
    </div>
  );
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
    <p className="rounded-md border border-danger-line bg-white px-3.5 py-2.5 text-sm text-danger">
      {message}
    </p>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center">
      <span className="text-sm text-ink-4">{label}</span>
    </div>
  );
}

export function Logo({ size = 26 }: { size?: number }) {
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
