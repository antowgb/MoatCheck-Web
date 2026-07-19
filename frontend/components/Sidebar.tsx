"use client";

import { BarChart3, Briefcase, GitCompare, History, LayoutDashboard, ListFilter } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import ThemeToggle from "./ThemeToggle";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/screener", label: "Screener", icon: ListFilter },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
  { href: "/backtest", label: "Backtest", icon: History },
  { href: "/compare", label: "Compare", icon: GitCompare },
  { href: "/methodology", label: "Methodology", icon: BarChart3 },
];

function NavLink({ href, label, icon: Icon, active }: { href: string; label: string; icon: typeof LayoutDashboard; active: boolean }) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
        active
          ? "bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-400"
          : "text-slate-600 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800/60 hover:text-slate-900 dark:hover:text-slate-100"
      }`}
    >
      <Icon size={16} strokeWidth={2} />
      {label}
    </Link>
  );
}

export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="hidden md:flex md:flex-col md:w-56 md:shrink-0 md:h-screen md:sticky md:top-0 border-r border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 px-3 py-4">
      <Link href="/" className="flex items-center gap-2 px-2 mb-6">
        <span className="w-7 h-7 rounded-md bg-gradient-to-br from-sky-500 to-emerald-500 flex items-center justify-center text-white text-xs font-bold">
          M
        </span>
        <span className="font-semibold tracking-tight text-slate-900 dark:text-slate-50">MoatCheck</span>
      </Link>
      <nav className="flex flex-col gap-1 flex-1">
        {NAV.map((item) => (
          <NavLink key={item.href} {...item} active={pathname === item.href} />
        ))}
      </nav>
      <div className="flex items-center justify-between px-2 pt-3 border-t border-slate-100 dark:border-slate-800">
        <span className="text-xs text-slate-400 dark:text-slate-600">v1</span>
        <ThemeToggle />
      </div>
    </aside>
  );
}

export function MobileTopBar() {
  const pathname = usePathname();

  return (
    <div className="md:hidden sticky top-0 z-10 border-b border-slate-200 dark:border-slate-800 bg-white/90 dark:bg-slate-900/90 backdrop-blur px-4 py-2.5 flex items-center gap-4 overflow-x-auto">
      <Link href="/" className="flex items-center gap-1.5 shrink-0">
        <span className="w-6 h-6 rounded-md bg-gradient-to-br from-sky-500 to-emerald-500 flex items-center justify-center text-white text-[10px] font-bold">
          M
        </span>
        <span className="font-semibold text-sm text-slate-900 dark:text-slate-50">MoatCheck</span>
      </Link>
      {NAV.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          className={`text-sm shrink-0 whitespace-nowrap ${
            pathname === item.href
              ? "text-sky-700 dark:text-sky-400 font-medium"
              : "text-slate-500 dark:text-slate-400"
          }`}
        >
          {item.label}
        </Link>
      ))}
      <div className="ml-auto shrink-0">
        <ThemeToggle />
      </div>
    </div>
  );
}
