import React, { createContext, useContext, useState, useCallback, useRef } from 'react';

/* ─── Types ─── */
type ToastVariant = 'success' | 'error' | 'info';

interface ToastItem {
  id: number;
  message: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  show: (message: string, variant?: ToastVariant) => void;
}

/* ─── Context ─── */
const ToastCtx = createContext<ToastContextValue | null>(null);

export const useToast = (): ToastContextValue => {
  const ctx = useContext(ToastCtx);
  if (!ctx) {
    // Fallback: no-op when used outside provider
    return { show: () => {} };
  }
  return ctx;
};

/* ─── Provider ─── */
export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [items, setItems] = useState<ToastItem[]>([]);
  const idRef = useRef(0);

  const show = useCallback((message: string, variant: ToastVariant = 'info') => {
    const id = ++idRef.current;
    setItems((prev) => [...prev, { id, message, variant }]);
    setTimeout(() => {
      setItems((prev) => prev.filter((t) => t.id !== id));
    }, 3000);
  }, []);

  return (
    <ToastCtx.Provider value={{ show }}>
      {children}
      <ToastContainer items={items} />
    </ToastCtx.Provider>
  );
};

/* ─── Container (renders the toast stack) ─── */
const variantIcon: Record<ToastVariant, string> = {
  success: '✓',
  error: '✕',
  info: 'ℹ',
};

const variantBg: Record<ToastVariant, string> = {
  success: 'bg-[var(--green)]',
  error: 'bg-[var(--red)]',
  info: 'bg-[var(--text)]',
};

const ToastContainer: React.FC<{ items: ToastItem[] }> = ({ items }) => {
  if (items.length === 0) return null;
  // Only show the latest toast
  const toast = items[items.length - 1];

  return (
    <div
      className={`pointer-events-none fixed bottom-[26px] left-1/2 z-[100] -translate-x-1/2 translate-y-0 rounded-[10px] px-[18px] py-[9px] text-[13px] font-medium text-white shadow-[var(--shadow-md)] transition-all duration-200 ${variantBg[toast.variant]}`}
      style={{ animation: 'toast-in 0.22s ease' }}
    >
      <span className="mr-1.5">{variantIcon[toast.variant]}</span>
      {toast.message}
    </div>
  );
};

/* ─── Standalone imperative API (no context needed) ─── */
let _standaloneContainer: HTMLDivElement | null = null;

export function showToast(message: string, variant: ToastVariant = 'info'): void {
  if (!_standaloneContainer) {
    _standaloneContainer = document.createElement('div');
    _standaloneContainer.style.cssText = 'position:fixed;bottom:0;left:0;pointer-events:none;z-index:100';
    document.body.appendChild(_standaloneContainer);
  }
  const el = document.createElement('div');
  const bgMap: Record<ToastVariant, string> = { success: '#12b76a', error: '#f04438', info: '#1a1d24' };
  const iconMap: Record<ToastVariant, string> = { success: '✓', error: '✕', info: 'ℹ' };
  el.style.cssText = `
    position:fixed;bottom:26px;left:50%;transform:translateX(-50%) translateY(8px);
    background:${bgMap[variant]};color:#fff;font-size:13px;font-weight:550;
    padding:9px 18px;border-radius:10px;box-shadow:0 6px 24px rgba(16,24,40,.09);
    opacity:0;transition:all .22s ease;z-index:100;pointer-events:none;
  `;
  el.textContent = `${iconMap[variant]} ${message}`;
  _standaloneContainer.appendChild(el);
  requestAnimationFrame(() => {
    el.style.opacity = '1';
    el.style.transform = 'translateX(-50%) translateY(0)';
  });
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transform = 'translateX(-50%) translateY(8px)';
    setTimeout(() => el.remove(), 250);
  }, 3000);
}
