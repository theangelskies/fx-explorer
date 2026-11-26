import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Briefly highlights its content when `value` CHANGES (not on first render), e.g. a rate after a
 * refresh or the trend numbers after switching windows. An honest "this updated" cue: unlike a
 * count-up animation, it never shows in-between numbers that weren't real rates.
 * The highlight is a CSS animation, so it's skipped under prefers-reduced-motion.
 */
export function FlashOnChange({ value, children, className = "" }: { value: string; children: ReactNode; className?: string }) {
  const previous = useRef(value);
  const [changes, setChanges] = useState(0);
  useEffect(() => {
    if (previous.current !== value) {
      previous.current = value;
      setChanges((n) => n + 1);
    }
  }, [value]);
  // A new key restarts the animation on every change.
  return <span key={changes} className={`rounded px-0.5 -mx-0.5 ${changes ? "flash" : ""} ${className}`}>{children}</span>;
}
