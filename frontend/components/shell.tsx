"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { API_URL, IS_LIVE } from "@/lib/api";
import { Chip, cx } from "./ui";

const NAV = [
  { href: "/", label: "queue" },
  { href: "/launch", label: "launcher" },
];

export function TopBar() {
  const path = usePathname();
  return (
    <header className="h-[46px] flex items-center gap-6 px-4 border-b border-rust-800 bg-ink-950 sticky top-0 z-20">
      <Link href="/" className="flex flex-col leading-none">
        <span className="font-display font-bold text-[15px] tracking-tight text-text">HHGOA fraud desk</span>
        <span className="mt-1 h-[2px] w-7 bg-flame-500" aria-hidden />
      </Link>
      <nav aria-label="Primary" className="flex gap-1">
        {NAV.map((n) => {
          const active = n.href === "/" ? path === "/" || path.startsWith("/cases") : path.startsWith(n.href);
          return (
            <Link
              key={n.href}
              href={n.href}
              aria-current={active ? "page" : undefined}
              className={cx(
                "font-mono text-[11px] uppercase tracking-wider px-2.5 py-1 rounded-xs border",
                active ? "border-rust-500 text-text bg-ember-950" : "border-transparent text-text-faint hover:text-text-muted",
              )}
            >
              {n.label}
            </Link>
          );
        })}
      </nav>
      <div className="ml-auto flex items-center gap-2">
        {IS_LIVE ? (
          <Chip className="border-rust-600 text-text-muted" title={API_URL}>
            api: live
          </Chip>
        ) : (
          <Chip className="border-rust-800 text-text-faint" title="NEXT_PUBLIC_API_URL is unset; serving lib/mock">
            data: mock
          </Chip>
        )}
      </div>
    </header>
  );
}

/** The 26px breadcrumb band. Right side carries run state or a live counter. */
export function CrumbBand({ crumbs, right }: { crumbs: string[]; right?: ReactNode }) {
  return (
    <div className="h-[26px] flex items-center px-4 border-b border-rust-800 dot-grid bg-ink-950">
      <p className="font-mono text-[10.5px] text-text-faint uppercase tracking-wider">
        {"// "}
        {crumbs.join(" / ")}
      </p>
      <div className="ml-auto font-mono text-[10.5px] text-text-faint">{right}</div>
    </div>
  );
}
