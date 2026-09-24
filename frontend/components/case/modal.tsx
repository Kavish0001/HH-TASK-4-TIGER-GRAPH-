"use client";

// Neither reference repo had an accessible modal, so this is the one primitive built
// from scratch: portal, focus trap, escape, aria-modal.

import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function Modal({ open, title, onClose, children, footer }: { open: boolean; title: string; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  const box = useRef<HTMLDivElement>(null);
  const prev = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    prev.current = document.activeElement as HTMLElement;
    const el = box.current;
    const focusables = () => Array.from(el?.querySelectorAll<HTMLElement>('button, [href], input, textarea, select, [tabindex]:not([tabindex="-1"])') ?? []);
    (focusables()[0] ?? el)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
      if (e.key === "Tab") {
        const f = focusables();
        if (!f.length) return;
        const first = f[0];
        const last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      prev.current?.focus();
    };
  }, [open, onClose]);

  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6" style={{ background: "rgba(12,12,12,0.72)", backdropFilter: "blur(4px)" }} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={box} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} className="panel max-w-[820px] w-full max-h-[85vh]">
        <header className="panel-h">
          <h2 className="eyebrow">{title}</h2>
          <button onClick={onClose} className="ml-auto font-mono text-[11px] text-text-faint hover:text-text" aria-label="Close">
            esc
          </button>
        </header>
        <div className="panel-b">{children}</div>
        {footer && <footer className="flex gap-2 justify-end px-3 py-2 border-t border-rust-800">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}
