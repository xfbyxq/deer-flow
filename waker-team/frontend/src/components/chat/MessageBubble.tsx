import React from 'react';
import type { ChatMessage } from '../../types';
import Avatar from '../Avatar';
import CollapseBlock from './CollapseBlock';

function formatTextWithMentions(text: string) {
  // Highlight @mentions
  const parts = text.split(/(@\S+)/g);
  return parts.map((part, i) =>
    part.startsWith('@') ? (
      <span key={i} className="text-[var(--primary)] font-medium">{part}</span>
    ) : (
      <React.Fragment key={i}>{part}</React.Fragment>
    )
  );
}

export interface MessageBubbleProps {
  message: ChatMessage;
  mode: 'direct' | 'group';
}

const MessageBubble: React.FC<MessageBubbleProps> = ({ message, mode }) => {
  /* ── User message ── */
  if (message.role === 'user') {
    return (
      <div className={`msg flex justify-end ${mode === 'group' ? 'group-mode' : ''}`}>
        <div className="max-w-[75%]">
          <div className="rounded-2xl rounded-tr-sm bg-[var(--primary)] text-white px-4 py-2.5 text-[13.5px] leading-relaxed shadow-sm">
            {message.text && formatTextWithMentions(message.text)}
          </div>
          <div className="text-right text-[11px] text-[var(--text-3)] mt-1 mr-1">
            {message.time}
          </div>
        </div>
      </div>
    );
  }

  /* ── Waker message ── */
  const waker = message.waker;
  const wakerName = waker?.name ?? 'Waker';
  const wakerRole = waker?.role ?? '';

  if (mode === 'group') {
    // Group mode: white card wrapper + avatar header
    return (
      <div className="msg mb-4">
        <div className="msg-waker bg-[var(--panel)] rounded-xl border border-[var(--border)] p-4 shadow-[var(--shadow-sm)]">
          {/* Header: avatar + name + role + time */}
          <div className="msg-waker-head flex items-center gap-2 mb-3">
            <Avatar name={wakerName} size="sm" />
            <span className="text-[13.5px] font-semibold text-[var(--text)]">{wakerName}</span>
            {wakerRole && (
              <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-[var(--panel-3)] text-[var(--text-2)] font-medium">
                {wakerRole}
              </span>
            )}
            <span className="text-[11px] text-[var(--text-3)] ml-auto">{message.time}</span>
          </div>
          {/* Plain text fallback（后端以 content_json={"text": ...} 存储的消息） */}
          {message.text && (
            <div className="text-[13.5px] text-[var(--text)] leading-relaxed whitespace-pre-wrap mb-2 last:mb-0">
              {formatTextWithMentions(message.text)}
            </div>
          )}
          {/* Parts */}
          {message.parts?.map((part, i) => {
            if (part.t === 'text') {
              return (
                <div key={i} className="text-[13.5px] text-[var(--text)] leading-relaxed whitespace-pre-wrap mb-2 last:mb-0">
                  {formatTextWithMentions(part.content)}
                </div>
              );
            }
            return <CollapseBlock key={i} part={part} />;
          })}
        </div>
      </div>
    );
  }

  // Direct mode: immersive, no card wrapper
  return (
    <div className="msg direct-immersive mb-4">
      {/* Plain text fallback（后端以 content_json={"text": ...} 存储的消息） */}
      {message.text && (
        <div className="text-[13.5px] text-[var(--text)] leading-relaxed whitespace-pre-wrap mb-2 last:mb-0">
          {formatTextWithMentions(message.text)}
        </div>
      )}
      {message.parts?.map((part, i) => {
        if (part.t === 'text') {
          return (
            <div key={i} className="text-[13.5px] text-[var(--text)] leading-relaxed whitespace-pre-wrap mb-2 last:mb-0">
              {formatTextWithMentions(part.content)}
            </div>
          );
        }
        return <CollapseBlock key={i} part={part} />;
      })}
    </div>
  );
};

export default React.memo(MessageBubble);
