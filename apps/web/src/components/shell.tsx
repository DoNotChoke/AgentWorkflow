"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { Organization, Workspace } from "@/lib/api";

/**
 * Vỏ ứng dụng: rail organization · sidebar workspace · khung chính.
 *
 * Panel ngữ cảnh bên phải chưa dựng ở lát này — nó chỉ có nội dung khi đã có
 * Agent trace (Phase 3) và Connection (Phase 2).
 */

type NavItem = { href: string; label: string; icon: React.ReactNode; badge?: number };

function initials(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  const letters = words.length >= 2 ? words[0][0] + words[1][0] : name.slice(0, 2);
  return letters.toUpperCase();
}

export function Shell({
  organizations,
  currentOrganization,
  workspaces,
  currentWorkspace,
  onSelectOrganization,
  onSelectWorkspace,
  children,
}: {
  organizations: Organization[];
  currentOrganization: Organization;
  workspaces: Workspace[];
  currentWorkspace: Workspace | null;
  onSelectOrganization: (id: string) => void;
  onSelectWorkspace: (id: string) => void;
  children: React.ReactNode;
}) {
  const pathname = usePathname();

  const nav: NavItem[] = [
    { href: "/agent", label: "Agent", icon: <IconChat /> },
    { href: "/workflows", label: "Workflows", icon: <IconNodes /> },
    { href: "/runs", label: "Runs", icon: <IconRun /> },
    { href: "/connections", label: "Connections", icon: <IconPlug /> },
    { href: "/settings/members", label: "Settings", icon: <IconGear /> },
  ];

  return (
    <div className="flex h-full">
      {/* Rail: đổi organization */}
      <div className="flex w-16 shrink-0 flex-col items-center gap-1.5 border-r border-[#dcdcd7] bg-rail py-3">
        {organizations.map((org) => {
          const active = org.id === currentOrganization.id;
          return (
            <button
              key={org.id}
              type="button"
              title={org.name}
              onClick={() => onSelectOrganization(org.id)}
              className={`flex size-[38px] items-center justify-center rounded-[9px] text-[13px] font-semibold transition-colors ${
                active
                  ? "border-[1.5px] border-accent bg-surface text-accent"
                  : "bg-[#d9d9d4] font-medium text-ink-3 hover:bg-[#cfcfca]"
              }`}
            >
              {initials(org.name)}
            </button>
          );
        })}
      </div>

      {/* Sidebar: workspace + điều hướng */}
      <div className="flex w-62 shrink-0 flex-col border-r border-line bg-sidebar">
        <div className="flex h-13 shrink-0 items-center border-b border-[#e9e9e6] px-3.5">
          <span className="truncate text-sm font-semibold">{currentOrganization.name}</span>
        </div>

        <div className="px-2.5 pt-2.5 pb-1">
          <select
            value={currentWorkspace?.id ?? ""}
            onChange={(event) => onSelectWorkspace(event.target.value)}
            className="w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-[13px] font-medium outline-none focus:border-accent"
          >
            {workspaces.length === 0 ? <option value="">No workspaces</option> : null}
            {workspaces.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                # {workspace.name}
              </option>
            ))}
          </select>
        </div>

        <nav className="flex flex-col gap-px px-2.5 py-2">
          {nav.map((item) => {
            const active = pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-[13px] transition-colors ${
                  active
                    ? "border border-line bg-surface font-medium text-ink"
                    : "border border-transparent text-ink-2 hover:bg-[#eaeae7]"
                }`}
              >
                <span className={active ? "text-accent" : "text-ink-3"}>{item.icon}</span>
                <span className="grow">{item.label}</span>
              </Link>
            );
          })}
        </nav>
      </div>

      <main className="min-w-0 grow bg-surface">{children}</main>
    </div>
  );
}

// ─── Icon, vẽ inline theo lưới 16px ─────────────────────────────────────────

const stroke = {
  width: 16,
  height: 16,
  viewBox: "0 0 16 16",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.5,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const IconChat = () => (
  <svg {...stroke} aria-hidden>
    <path d="M13.5 9.5a2 2 0 01-2 2H6l-3.5 2.5V4.5a2 2 0 012-2h7a2 2 0 012 2z" />
  </svg>
);

const IconNodes = () => (
  <svg {...stroke} aria-hidden>
    <circle cx="4" cy="4" r="2" />
    <circle cx="12" cy="4" r="2" />
    <circle cx="8" cy="12" r="2" />
    <path d="M5.4 5.6L7.2 10.2M10.6 5.6L8.8 10.2" />
  </svg>
);

const IconRun = () => (
  <svg {...stroke} aria-hidden>
    <circle cx="8" cy="8" r="6" />
    <path d="M6.6 5.6l4 2.4-4 2.4z" />
  </svg>
);

const IconPlug = () => (
  <svg {...stroke} aria-hidden>
    <path d="M6 2v3.5M10 2v3.5M4.5 5.5h7v2.2a3.5 3.5 0 01-7 0zM8 11.2V14" />
  </svg>
);

const IconGear = () => (
  <svg {...stroke} aria-hidden>
    <circle cx="8" cy="8" r="2.4" />
    <path d="M8 1.6v1.8M8 12.6v1.8M1.6 8h1.8M12.6 8h1.8M3.5 3.5l1.3 1.3M11.2 11.2l1.3 1.3M12.5 3.5l-1.3 1.3M4.8 11.2l-1.3 1.3" />
  </svg>
);
