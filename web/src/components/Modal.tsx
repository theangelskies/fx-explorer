import { useEffect, useRef, type ReactNode } from "react";

interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}

/**
 * Accessible modal on the native <dialog> element: the browser traps focus inside, closes on Esc
 * and returns focus to where it was. Clicking the backdrop also closes it.
 */
export function Modal({ open, onClose, title, children }: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      aria-label={title}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className="m-auto mt-[12vh] w-[min(32rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface p-0 text-fg shadow-xl"
    >
      {open && <div className="p-4 sm:p-5">{children}</div>}
    </dialog>
  );
}
