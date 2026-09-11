/**
 * 后端 content_json → 前端消息字段的统一解析。
 *
 * 支持三种形态：
 * - 纯文本 JSON 字符串（历史消息/回退）：显示原文；
 * - 纯文本数组：消息 parts；
 * - 结构化对象 {text, meta}：text 可为空串（纯澄清消息由交互卡片渲染），
 *   meta 携带澄清请求/回答、群过程消息标记（dispatch/report/leader_post）。
 */
import type { ChatMessageMeta, MessagePart } from '../types';
import { parseClarificationRequest, parseClarificationResponse } from './clarification';

export interface ParsedMessageContent {
  text?: string;
  parts?: MessagePart[];
  meta?: ChatMessageMeta;
}

function parseMeta(value: unknown): ChatMessageMeta | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const raw = value as Record<string, unknown>;
  const meta: ChatMessageMeta = {};
  // CONTRACT-CLARIFICATIONS：优先读复数键 clarifications（本 run 全部澄清，保序），
  // 缺失/非数组时回退单数键 clarification（包成单元素列表）。
  const rawList = Array.isArray(raw.clarifications)
    ? raw.clarifications
    : raw.clarification
      ? [raw.clarification]
      : [];
  const parsedClarifications = rawList
    .map((item) => parseClarificationRequest(item))
    .filter((item): item is NonNullable<typeof item> => item !== null);
  if (parsedClarifications.length > 0) {
    meta.clarifications = parsedClarifications;
    meta.clarification = parsedClarifications[0];
  }
  const response = parseClarificationResponse(raw.clarification_response);
  if (response) meta.clarification_response = response;
  if (raw.kind === 'dispatch' || raw.kind === 'report' || raw.kind === 'leader_post') {
    meta.kind = raw.kind;
  }
  if (raw.partial === true) meta.partial = true;
  if (typeof raw.target === 'string') meta.target = raw.target;
  if (typeof raw.status === 'string') meta.status = raw.status;
  if (typeof raw.mode === 'string') meta.mode = raw.mode;
  if (Array.isArray(raw.mentions)) {
    meta.mentions = raw.mentions.filter((m): m is string => typeof m === 'string');
  }
  return Object.keys(meta).length > 0 ? meta : undefined;
}

/** 解析后端 content_json；无可识别结构时回退显示原文（保持旧行为） */
export function parseMessageContent(contentJson: string | null): ParsedMessageContent {
  if (!contentJson) return {};
  try {
    const parsed = JSON.parse(contentJson);
    if (Array.isArray(parsed)) {
      return { parts: parsed as MessagePart[] };
    }
    if (!parsed || typeof parsed !== 'object') {
      return { text: String(contentJson) };
    }

    const meta = parseMeta((parsed as Record<string, unknown>).meta);
    const rawText = (parsed as Record<string, unknown>).text;
    const text = typeof rawText === 'string' && rawText ? rawText : undefined;

    // 无文本且无结构化 meta → 非标准结构，保持"显示原文"行为
    if (!text && !meta) return { text: contentJson };
    return { text, ...(meta ? { meta } : {}) };
  } catch {
    return { text: contentJson };
  }
}
