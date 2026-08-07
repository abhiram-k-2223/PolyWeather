"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Cpu,
  Database,
  BarChart3,
  Settings,
  FileText,
  Activity,
  MessageSquare,
} from "lucide-react";

const navGroups = [
  {
    label: "Monitoring",
    items: [
      { href: "/ops/overview", icon: LayoutDashboard, label: "Overview" },
      { href: "/ops/health", icon: Activity, label: "API Status" },
      { href: "/ops/system", icon: Cpu, label: "System" },
      { href: "/ops/training", icon: Database, label: "Training" },
      { href: "/ops/analytics", icon: BarChart3, label: "Analytics" },
    ],
  },
  {
    label: "Operations",
    items: [
      { href: "/ops/feedback", icon: MessageSquare, label: "Feedback" },
    ],
  },
  {
    label: "Admin",
    items: [
      { href: "/ops/config", icon: Settings, label: "Config" },
      { href: "/ops/view-logs", icon: FileText, label: "Logs" },
    ],
  },
  {
    label: "History",
    items: [
      { href: "/ops/truth-history", icon: Activity, label: "Truth History" },
    ],
  },
];

export function AdminSidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed left-0 top-0 z-40 flex h-screen w-56 flex-col border-r border-slate-200 bg-white shadow-[1px_0_2px_rgba(15,23,42,0.04)]">
      <div className="flex h-14 items-center gap-2 border-b border-slate-200 px-4">
        <LayoutDashboard className="h-5 w-5 text-blue-600" />
        <Link href="/ops/system" className="text-sm font-extrabold text-slate-950">
          PolyWeather Ops
        </Link>
      </div>
      <nav className="flex-1 space-y-6 overflow-y-auto py-4">
        {navGroups.map((group) => (
          <div key={group.label} className="px-3">
            <h4 className="mb-2 px-2 text-[11px] font-bold uppercase tracking-wider text-slate-500">
              {group.label}
            </h4>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const isActive = pathname === item.href || pathname.startsWith(item.href + "/");
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      className={
                        "flex items-center gap-2.5 rounded-md border border-transparent px-2 py-2 text-sm transition-colors " +
                        (isActive
                          ? "border-blue-200 bg-blue-50 text-blue-700 font-semibold shadow-[inset_3px_0_0_#2563eb]"
                          : "text-slate-600 hover:border-slate-200 hover:bg-slate-50 hover:text-slate-950")
                      }
                    >
                      <item.icon className="h-4 w-4 shrink-0" />
                      <span>{item.label}</span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
      <div className="border-t border-slate-200 px-4 py-3">
        <Link
          href="/"
          className="flex items-center gap-2 text-xs font-semibold text-slate-500 transition-colors hover:text-blue-700"
        >
          ← Back to Site
        </Link>
      </div>
    </aside>
  );
}
