"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect } from "react";
import { useAuth } from "../lib/auth";

const NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/strategy", label: "Strategy" },
  { href: "/emails", label: "Emails" },
  { href: "/opportunities", label: "Opportunities" },
  { href: "/suppliers", label: "Suppliers" },
  { href: "/deals", label: "Deals" },
  { href: "/pipeline", label: "Pipeline" },
  { href: "/profile", label: "Profile" },
  { href: "/settings/integrations", label: "Integrations" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const { user, loading, logout } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

  if (loading || !user) {
    return (
      <main className="flex h-screen items-center justify-center text-gray-400">
        Loading…
      </main>
    );
  }

  return (
    <div className="flex min-h-screen">
      <aside className="w-56 border-r border-border bg-surface p-4">
        <div className="mb-6">
          <div className="text-lg font-semibold">Atlas</div>
          <div className="text-xs text-gray-500">Trade OS</div>
        </div>
        <nav className="space-y-1">
          {NAV.map((item) => {
            const active = pathname === item.href || pathname?.startsWith(item.href + "/");
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`block rounded-md px-3 py-1.5 text-sm ${
                  active
                    ? "bg-accent/20 text-accent"
                    : "text-gray-300 hover:bg-surface2"
                }`}
              >
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-10 border-t border-border pt-3 text-xs text-gray-400">
          <div className="truncate">{user.full_name || user.email}</div>
          <button
            onClick={logout}
            className="mt-2 text-gray-500 hover:text-gray-300"
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="flex-1 bg-bg p-6">{children}</main>
    </div>
  );
}
