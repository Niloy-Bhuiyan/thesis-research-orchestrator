"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const SECTIONS: { group: string; items: { href: string; label: string }[] }[] = [
  {
    group: "Research",
    items: [
      { href: "/", label: "Dashboard" },
      { href: "/experiments", label: "Experiments" },
      { href: "/lineage", label: "Lineage" },
      { href: "/guard", label: "Scientific Guard" },
    ],
  },
  {
    group: "Execution",
    items: [
      { href: "/providers", label: "Providers" },
      { href: "/kaggle", label: "Kaggle" },
      { href: "/logs", label: "Logs" },
    ],
  },
  {
    group: "System",
    items: [{ href: "/settings", label: "Settings" }],
  },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav>
      {SECTIONS.map((section) => (
        <div key={section.group}>
          <div className="nav-group">{section.group}</div>
          <div className="nav">
            {section.items.map((item) => {
              const active =
                item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={active ? "active" : undefined}
                >
                  {item.label}
                </Link>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}
