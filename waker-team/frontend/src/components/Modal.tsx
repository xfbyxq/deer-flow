import React, { useEffect, useCallback } from 'react';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title: string;
  subtitle?: string;
  children: React.ReactNode;
  /** Optional footer content (buttons etc.) */
  footer?: React.ReactNode;
  /** Max width class — defaults to 580px */
  width?: string;
}

const Modal: React.FC<ModalProps> = ({ open, onClose, title, subtitle, children, footer, width }) => {
  /* Close on Escape */
  const handleKey = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    },
    [onClose],
  );

  useEffect(() => {
    if (!open) return;
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [open, handleKey]);

  if (!open) return null;

  return (
    <div
      className="modal-overlay fixed inset-0 z-[90] flex items-center justify-center"
      style={{ background: 'rgba(15,23,42,.42)' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="modal-panel flex flex-col overflow-hidden rounded-2xl bg-[var(--panel)] shadow-[0_24px_64px_rgba(15,23,42,.3)]"
        style={{ width: width ?? 580, maxWidth: 'calc(100vw - 48px)', maxHeight: '78vh' }}
      >
        {/* Head */}
        <div className="flex items-start justify-between gap-2.5 border-b border-[var(--border)] px-5 pb-3 pt-4">
          <div>
            <h2 className="text-[15px] font-bold">{title}</h2>
            {subtitle && <p className="mt-0.5 text-xs text-[var(--text-3)]">{subtitle}</p>}
          </div>
          <button
            onClick={onClose}
            className="flex h-7 w-7 items-center justify-center rounded-md text-[var(--text-3)] transition-colors hover:bg-[var(--panel-3)] hover:text-[var(--text-2)]"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="min-h-[120px] flex-1 overflow-y-auto p-3.5">
          {children}
        </div>

        {/* Footer */}
        {footer && (
          <div className="flex items-center justify-end gap-2.5 border-t border-[var(--border)] px-5 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
};

/* ─── Sub-components for picker pattern (category filter + checkbox list) ─── */

export interface CategoryChipProps {
  label: string;
  active: boolean;
  onClick: () => void;
}

export const CategoryChip: React.FC<CategoryChipProps> = ({ label, active, onClick }) => (
  <button
    onClick={onClick}
    className={`rounded-full border px-2.5 py-0.5 text-[11.5px] font-semibold transition-all ${
      active
        ? 'border-[var(--text)] bg-[var(--text)] text-white'
        : 'border-[var(--border)] text-[var(--text-2)] hover:border-[var(--border-strong)]'
    }`}
  >
    {label}
  </button>
);

export interface PickerItemProps {
  selected: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}

export const PickerItem: React.FC<PickerItemProps> = ({ selected, disabled, onClick, children }) => (
  <button
    onClick={disabled ? undefined : onClick}
    className={`flex w-full items-center gap-3 rounded-[10px] border px-3 py-2.5 text-left transition-all ${
      disabled
        ? 'cursor-default opacity-55'
        : selected
          ? 'border-[#d3e0ff] bg-[var(--primary-soft)]'
          : 'border-transparent hover:bg-[var(--panel-2)]'
    }`}
  >
    <span
      className={`flex h-[18px] w-[18px] shrink-0 items-center justify-center rounded-md border-[1.5px] transition-all ${
        selected ? 'border-[var(--primary)] bg-[var(--primary)] text-white' : 'border-[var(--border-strong)] text-transparent'
      }`}
    >
      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="20 6 9 17 4 12" />
      </svg>
    </span>
    <span className="min-w-0 flex-1">{children}</span>
  </button>
);

export default React.memo(Modal);
