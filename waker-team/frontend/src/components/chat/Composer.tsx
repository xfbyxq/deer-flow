import React, { useState, useRef, useCallback, useEffect } from 'react';

/* ── SVG icons ── */
const SendIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
    <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
  </svg>
);
/* 运行中状态：实心正方形（点击停止） */
const StopIcon = () => (
  <svg viewBox="0 0 24 24" fill="currentColor" className="w-4 h-4 animate-pulse" aria-hidden="true">
    <rect x="6" y="6" width="12" height="12" rx="2" />
  </svg>
);
const ClipIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="w-4 h-4">
    <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48" />
  </svg>
);

export interface MentionMember {
  name: string;
  role: string;
}

export interface ComposerProps {
  onSend: (text: string) => void;
  /** 运行中点击发送按钮触发停止（取消当前回复） */
  onStop?: () => void;
  /** 回复运行中：发送按钮变为「正在运行」状态（实心正方形，点击停止） */
  running?: boolean;
  mentionMembers?: MentionMember[];
  disabled?: boolean;
}

const Composer: React.FC<ComposerProps> = ({ onSend, onStop, running = false, mentionMembers = [], disabled = false }) => {
  const [text, setText] = useState('');
  const [showMention, setShowMention] = useState(false);
  const [mentionFilter, setMentionFilter] = useState('');
  const [mentionIdx, setMentionIdx] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mentionMenuRef = useRef<HTMLDivElement>(null);

  const filtered = mentionMembers.filter(m =>
    m.name.toLowerCase().includes(mentionFilter.toLowerCase())
  );

  // Reset mention index when filter changes
  useEffect(() => { setMentionIdx(0); }, [mentionFilter]);

  const autoGrow = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const maxH = 6 * 24; // ~6 rows
    el.style.height = `${Math.min(el.scrollHeight, maxH)}px`;
  }, []);

  const insertMention = useCallback((name: string) => {
    const el = textareaRef.current;
    if (!el) return;
    const cursorPos = el.selectionStart;
    // Find the @ position before cursor
    const before = text.slice(0, cursorPos);
    const atIdx = before.lastIndexOf('@');
    if (atIdx === -1) return;
    const after = text.slice(cursorPos);
    const newText = before.slice(0, atIdx) + `@${name} ` + after;
    setText(newText);
    setShowMention(false);
    setMentionFilter('');
    // Restore cursor after @name
    requestAnimationFrame(() => {
      if (!textareaRef.current) return;
      const newPos = atIdx + name.length + 2; // @name + space
      textareaRef.current.selectionStart = newPos;
      textareaRef.current.selectionEnd = newPos;
      textareaRef.current.focus();
    });
  }, [text]);

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const val = e.target.value;
    setText(val);
    autoGrow();

    // Check for @ mention trigger
    const cursorPos = e.target.selectionStart;
    const before = val.slice(0, cursorPos);
    const atMatch = before.match(/@(\S*)$/);
    if (atMatch && mentionMembers.length > 0) {
      setShowMention(true);
      setMentionFilter(atMatch[1]);
    } else {
      setShowMention(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (showMention && filtered.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setMentionIdx(i => (i + 1) % filtered.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setMentionIdx(i => (i - 1 + filtered.length) % filtered.length);
        return;
      }
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        insertMention(filtered[mentionIdx].name);
        return;
      }
      if (e.key === 'Escape') {
        setShowMention(false);
        return;
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      // 运行中不发送新消息（点击发送按钮可停止当前回复）
      if (running) return;
      handleSend();
    }
  };

  const handleSend = () => {
    const trimmed = text.trim();
    if (!trimmed || disabled || running) return;
    onSend(trimmed);
    setText('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  };

  return (
    <div className="relative border-t border-[var(--border)] bg-[var(--panel)] px-4 py-3">
      {/* Mention dropdown */}
      {showMention && filtered.length > 0 && (
        <div
          ref={mentionMenuRef}
          className="absolute bottom-full left-4 right-4 mb-1 bg-[var(--panel)] border border-[var(--border)] rounded-lg shadow-[var(--shadow-md)] overflow-hidden z-20"
          style={{ animation: 'pop-in 0.15s ease-out' }}
        >
          <div className="text-[11px] text-[var(--text-3)] px-3 py-1.5 border-b border-[var(--border)] font-medium">
            选择提及的成员
          </div>
          {filtered.slice(0, 8).map((m, i) => (
            <button
              key={m.name}
              type="button"
              onMouseDown={(e) => { e.preventDefault(); insertMention(m.name); }}
              className={`flex w-full items-center gap-2 px-3 py-2 text-left text-[13px] transition-colors ${
                i === mentionIdx ? 'bg-[var(--primary-soft)]' : 'hover:bg-[var(--panel-2)]'
              }`}
            >
              <span className="font-medium text-[var(--text)]">{m.name}</span>
              <span className="text-[11px] text-[var(--text-3)]">{m.role}</span>
            </button>
          ))}
        </div>
      )}

      <div className="flex items-end gap-2">
        {/* Attachment button */}
        <button
          type="button"
          className="shrink-0 p-2 rounded-lg text-[var(--text-3)] hover:text-[var(--text-2)] hover:bg-[var(--panel-2)] transition-colors"
          title="附件"
          disabled={disabled}
        >
          <ClipIcon />
        </button>

        {/* Textarea */}
        <textarea
          ref={textareaRef}
          value={text}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder="输入消息… 输入 @ 提及成员"
          disabled={disabled}
          rows={1}
          className="flex-1 resize-none rounded-lg border border-[var(--border)] bg-[var(--panel-2)] px-3 py-2 text-[13.5px] text-[var(--text)] placeholder:text-[var(--text-3)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)]/30 focus:border-[var(--primary)] transition-colors"
          style={{ maxHeight: '144px' }}
        />

        {/* Send / Stop button：运行中显示实心正方形（点击停止） */}
        <button
          type="button"
          onClick={running ? onStop : handleSend}
          disabled={running ? !onStop : !text.trim() || disabled}
          className={`shrink-0 p-2 rounded-lg transition-colors ${
            running
              ? 'bg-[var(--primary)] text-white hover:bg-[var(--primary-deep)] shadow-sm'
              : text.trim() && !disabled
                ? 'bg-[var(--primary)] text-white hover:bg-[var(--primary-deep)] shadow-sm'
                : 'bg-[var(--panel-3)] text-[var(--text-3)] cursor-not-allowed'
          }`}
          title={running ? '停止' : '发送'}
          aria-label={running ? '停止' : '发送'}
        >
          {running ? <StopIcon /> : <SendIcon />}
        </button>
      </div>
    </div>
  );
};

export default React.memo(Composer);
