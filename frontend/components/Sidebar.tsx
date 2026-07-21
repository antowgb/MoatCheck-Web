"use client";

import { BarChart3, Briefcase, GitCompare, History, LayoutDashboard, ListFilter, Newspaper } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import ThemeToggle from "./ThemeToggle";

const NAV = [
  { href: "/", label: "Dashboard", icon: LayoutDashboard },
  { href: "/screener", label: "Screener", icon: ListFilter },
  { href: "/portfolio", label: "Portfolio", icon: Briefcase },
  { href: "/backtest", label: "Backtest", icon: History },
  { href: "/compare", label: "Compare", icon: GitCompare },
  { href: "/qualitative", label: "Qualitative", icon: Newspaper },
  { href: "/methodology", label: "Methodology", icon: BarChart3 },
];

function NavLink({ href, label, icon: Icon, active }: { href: string; label: string; icon: typeof LayoutDashboard; active: boolean }) {
  return (
    <Link
      href={href}
      className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
        active
          ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-400"
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
        <span className="w-7 h-7 rounded-md bg-[#0f172a] flex items-center justify-center overflow-hidden shrink-0">
          <Image src="/logo.svg" alt="MoatCheck" width={20} height={20} />
        </span>
        <span className="font-semibold tracking-tight text-slate-900 dark:text-slate-50">MoatCheck</span>
      </Link>
      <nav className="flex flex-col gap-1 flex-1">
        {NAV.map((item) => (
          <NavLink key={item.href} {...item} active={pathname === item.href} />
        ))}
      </nav>
      <div className="flex items-center justify-between px-2 pt-3 border-t border-slate-100 dark:border-slate-800">
        <span className="text-xs text-slate-400 dark:text-slate-600">V2</span>
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
        <span className="w-6 h-6 rounded-md bg-[#0f172a] flex items-center justify-center overflow-hidden shrink-0">
          <Image src="/logo.svg" alt="MoatCheck" width={16} height={16} />
        </span>
        <span className="font-semibold text-sm text-slate-900 dark:text-slate-50">MoatCheck</span>
      </Link>
      {NAV.map((item) => (
        <Link
          key={item.href}
          href={item.href}
          className={`text-sm shrink-0 whitespace-nowrap ${
            pathname === item.href
              ? "text-emerald-700 dark:text-emerald-400 font-medium"
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
