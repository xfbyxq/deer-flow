import React from 'react';
import type { ChatMessage, ClarificationRequest } from '../../types';
import type { ClarificationAnswer } from '../../utils/clarification';
import Avatar from '../Avatar';
import CollapseBlock from './CollapseBlock';
import ClarificationCard from './ClarificationCard';

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
  /** 澄清卡片已答（其后已有用户消息） */
  clarificationAnswered?: boolean;
  /** 已答时的回答摘要 */
  clarificationAnsweredValue?: string | null;
  /** 澄清回答提交回调 */
  onClarificationSubmit?: (
    request: ClarificationRequest,
    answer: ClarificationAnswer,
  ) => void | Promise<void>;
}

const MessageBubble: React.FC<MessageBubbleProps> = ({
  message,
  mode,
  clarificationAnswered = false,
  clarificationAnsweredValue,
  onClarificationSubmit,
}) => {
  /** 澄清卡片（waker 消息携带 meta.clarification 时渲染） */
  const clarificationCard = message.meta?.clarification ? (
    <ClarificationCard
      request={message.meta.clarification}
      answered={clarificationAnswered}
      answeredValue={clarificationAnsweredValue}
      onSubmit={(answer) => {
        void onClarificationSubmit?.(message.meta!.clarification!, answer);
      }}
    />
  ) : null;

  /* ── User message ── */
  if (message.role === 'user') {
    const answerMeta = message.meta?.clarification_response;
    return (
      <div className={`msg flex justify-end ${mode === 'group' ? 'group-mode' : ''}`}>
        <div className="max-w-[75%]">
          <div className="rounded-2xl rounded-tr-sm bg-[var(--primary)] text-white px-4 py-2.5 text-[13.5px] leading-relaxed shadow-sm">
            {answerMeta ? (
              /* 澄清回答：展示人类可读摘要（发送给模型的格式化文案在 meta 中） */
              <>
                <div className="mb-0.5 text-[10.5px] font-medium uppercase tracking-wide opacity-75">
                  回答澄清
                </div>
                {answerMeta.value}
              </>
            ) : (
              message.text && formatTextWithMentions(message.text)
            )}
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
          {/* 澄清卡片 */}
          {clarificationCard}
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
      {clarificationCard && <div className="mt-1">{clarificationCard}</div>}
    </div>
  );
};

export default React.memo(MessageBubble);
