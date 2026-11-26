import type { ReactNode } from "react";

interface CardProps {
  title?: ReactNode;
  action?: ReactNode; // top-right: a link or toggle
  children: ReactNode;
  className?: string;
}

/** The card style from docs/ui-design.md §2.3. */
export function Card({ title, action, children, className = "" }: CardProps) {
  return (
    <section
      className={`rounded-xl border border-line bg-surface p-4 shadow-sm sm:p-6 dark:shadow-none ${className}`}
    >
      {(title || action) && (
        <header className="mb-4 flex items-center justify-between gap-4">
          {title && <h2 className="text-base font-semibold">{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}
