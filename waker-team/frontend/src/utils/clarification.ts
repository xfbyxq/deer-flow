/**
 * 澄清（ask_clarification）前端工具：解析、回答文案构造与已答推导。
 *
 * 与 DeerFlow 主 UI 的 human_input_request/response 协议对齐（核心子集）：
 * - 结构化 payload 由后端从 ToolMessage.artifact.human_input 提取写入消息 meta；
 * - 回答文案与主 UI 一致（For your clarification "…", my answer is: …），
 *   模型侧以此识别"这是对澄清的结构化回答"（对长对话压缩场景尤为重要）。
 */
import type {
  ChatMessage,
  ClarificationField,
  ClarificationFieldType,
  ClarificationOption,
  ClarificationRequest,
  ClarificationResponseMeta,
} from '../types';

const INPUT_MODES = ['free_text', 'single_choice', 'choice_with_other', 'form'] as const;
const FIELD_TYPES: readonly ClarificationFieldType[] = [
  'text',
  'textarea',
  'number',
  'select',
  'multi_select',
  'checkbox',
  'date',
];

// 与 JS Object.prototype 冲突的字段名（表单值以字段名做 key，需拒绝继承属性）
const RESERVED_FIELD_NAMES = new Set([
  '__proto__',
  'constructor',
  'prototype',
  'toString',
  'toLocaleString',
  'valueOf',
  'hasOwnProperty',
  'isPrototypeOf',
  'propertyIsEnumerable',
  '__defineGetter__',
  '__defineSetter__',
  '__lookupGetter__',
  '__lookupSetter__',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0;
}

function parseOptions(value: unknown): ClarificationOption[] | undefined {
  if (value === undefined) return undefined;
  if (!Array.isArray(value)) return undefined;
  const options: ClarificationOption[] = [];
  for (const option of value) {
    if (!isRecord(option)) return undefined;
    const { id, label } = option;
    const optionValue = option.value;
    if (!isNonEmptyString(id) || !isNonEmptyString(label) || !isNonEmptyString(optionValue)) {
      return undefined;
    }
    options.push({ id, label, value: optionValue });
  }
  return options;
}

function parseFields(value: unknown): ClarificationField[] | undefined {
  if (value === undefined) return undefined;
  if (!Array.isArray(value)) return undefined;
  const fields: ClarificationField[] = [];
  const seenNames = new Set<string>();
  for (const field of value) {
    if (!isRecord(field)) return undefined;
    const name = field.name;
    const label = field.label;
    const type = field.type;
    const required = field.required;
    if (
      !isNonEmptyString(name) ||
      RESERVED_FIELD_NAMES.has(name) ||
      seenNames.has(name) ||
      !isNonEmptyString(label) ||
      typeof type !== 'string' ||
      !FIELD_TYPES.includes(type as ClarificationFieldType) ||
      (required !== undefined && typeof required !== 'boolean')
    ) {
      return undefined;
    }
    seenNames.add(name);
    const options = parseOptions(field.options);
    if (field.options !== undefined && options === undefined) return undefined;
    if ((type === 'select' || type === 'multi_select') && (!options || options.length === 0)) {
      return undefined;
    }
    fields.push({
      name,
      label,
      type: type as ClarificationFieldType,
      required: required === true,
      ...(isNonEmptyString(field.placeholder) ? { placeholder: field.placeholder } : {}),
      ...(options ? { options } : {}),
    });
  }
  return fields;
}

/** 解析澄清请求结构化 payload（非法结构返回 null，前端降级为纯文本展示） */
export function parseClarificationRequest(value: unknown): ClarificationRequest | null {
  if (!isRecord(value)) return null;
  if (value.kind !== 'human_input_request') return null;
  if (!isNonEmptyString(value.request_id) || !isNonEmptyString(value.question)) return null;
  const inputMode = value.input_mode;
  if (typeof inputMode !== 'string' || !(INPUT_MODES as readonly string[]).includes(inputMode)) {
    return null;
  }

  const options = parseOptions(value.options);
  if (value.options !== undefined && options === undefined) return null;
  if ((inputMode === 'single_choice' || inputMode === 'choice_with_other') && (!options || options.length === 0)) {
    return null;
  }

  const fields = parseFields(value.fields);
  if (value.fields !== undefined && fields === undefined) return null;
  if (inputMode === 'form' && (!fields || fields.length === 0)) return null;

  return {
    version: typeof value.version === 'number' ? value.version : 1,
    kind: 'human_input_request',
    source: isNonEmptyString(value.source) ? value.source : 'ask_clarification',
    request_id: value.request_id,
    ...(isNonEmptyString(value.tool_call_id) ? { tool_call_id: value.tool_call_id } : {}),
    ...(isNonEmptyString(value.clarification_type)
      ? { clarification_type: value.clarification_type }
      : {}),
    question: value.question,
    context: typeof value.context === 'string' ? value.context : null,
    input_mode: inputMode as ClarificationRequest['input_mode'],
    ...(options ? { options } : {}),
    ...(fields ? { fields } : {}),
  };
}

/** 解析澄清回答标记（回答消息 meta.clarification_response） */
export function parseClarificationResponse(value: unknown): ClarificationResponseMeta | null {
  if (!isRecord(value)) return null;
  const { request_id, kind, value: answerValue } = value;
  if (!isNonEmptyString(request_id) || !isNonEmptyString(answerValue)) return null;
  if (kind !== 'option' && kind !== 'text' && kind !== 'form') return null;
  return { request_id, kind, value: answerValue };
}

/* ── 已答推导 ── */

export interface ClarificationThreadState {
  /** 已被回答的澄清 request_id 集合 */
  answeredIds: Set<string>;
  /** request_id → 回答展示摘要（无可靠回答内容时为 null） */
  answeredValues: Map<string, string | null>;
  /** 未答澄清的 request_id（按出现顺序；用于多澄清聚合判定） */
  openRequestIds: string[];
}

/**
 * 推导澄清的已答状态：
 * - 回答消息（带 meta.clarification_response）精确关闭对应 request_id 的卡片；
 * - 普通 composer 文本回复按 legacy 语义关闭最近一条未答澄清；
 * - openRequestIds 返回剩余未答卡片（多澄清并存时，前端据此延迟触发处理）。
 */
export function computeClarificationState(messages: ChatMessage[]): ClarificationThreadState {
  const answeredIds = new Set<string>();
  const answeredValues = new Map<string, string | null>();
  const open: ClarificationRequest[] = [];
  for (const m of messages) {
    if (m.role === 'user') {
      const response = m.meta?.clarification_response;
      if (response) {
        // 精确匹配：回答绑定 request_id，关闭对应卡片（不受回答顺序影响）
        const idx = open.findIndex((c) => c.request_id === response.request_id);
        if (idx >= 0) {
          open.splice(idx, 1);
          answeredIds.add(response.request_id);
          answeredValues.set(response.request_id, response.value);
        }
      } else {
        // legacy：普通文本关闭最近一条未答澄清
        const latest = open.pop();
        if (latest) {
          answeredIds.add(latest.request_id);
          answeredValues.set(latest.request_id, m.text ? m.text.slice(0, 120) : null);
        }
      }
    } else if (m.role === 'waker') {
      // CONTRACT-CLARIFICATIONS：同一条 waker 消息可能携带多张澄清卡片，
      // 优先 push 全部 clarifications（保序），缺失时回退单数键。
      const cards =
        m.meta?.clarifications ?? (m.meta?.clarification ? [m.meta.clarification] : []);
      for (const card of cards) open.push(card);
    }
  }
  return { answeredIds, answeredValues, openRequestIds: open.map((c) => c.request_id) };
}

/* ── 回答文案构造（对齐主 UI） ── */

export type ClarificationFormValue = string | number | boolean | string[];

export type ClarificationAnswer =
  | { kind: 'option'; option: ClarificationOption }
  | { kind: 'text'; value: string }
  | { kind: 'form'; values: Record<string, ClarificationFormValue> };

/** 表单初始值（checkbox 默认显式 false，避免"未触碰"与"未回答"混淆） */
export function buildInitialFormValues(
  fields: ClarificationField[],
): Record<string, ClarificationFormValue> {
  const values: Record<string, ClarificationFormValue> = {};
  for (const field of fields) {
    if (field.type === 'checkbox') values[field.name] = false;
  }
  return values;
}

/** 缺失的必填字段（提交前校验） */
export function findMissingRequiredFields(
  fields: ClarificationField[],
  values: Record<string, ClarificationFormValue>,
): ClarificationField[] {
  return fields.filter((field) => {
    if (!field.required) return false;
    const v = Object.prototype.hasOwnProperty.call(values, field.name) ? values[field.name] : undefined;
    if (v === undefined) return true;
    if (typeof v === 'string') return v.trim().length === 0;
    if (Array.isArray(v)) return v.length === 0;
    return false;
  });
}

function formatFormValue(value: ClarificationFormValue): string {
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  return String(value);
}

function isEmptyFormValue(value: ClarificationFormValue | undefined): boolean {
  if (value === undefined) return true;
  if (typeof value === 'string') return value.trim().length === 0;
  if (Array.isArray(value)) return value.length === 0;
  return false;
}

/** 表单值摘要（"标签: 值; 标签2: 值2"，人类可读） */
export function buildFormSummary(
  request: ClarificationRequest,
  values: Record<string, ClarificationFormValue>,
): string {
  const parts: string[] = [];
  for (const field of request.fields ?? []) {
    const value = Object.prototype.hasOwnProperty.call(values, field.name)
      ? values[field.name]
      : undefined;
    if (isEmptyFormValue(value)) continue;
    parts.push(`${field.label}: ${formatFormValue(value!)}`);
  }
  return parts.join('; ');
}

/** 表单提交值（摘要 + 完整 JSON 记录，消除"摘要歧义"；与主 UI 一致） */
export function buildFormSubmissionValue(
  request: ClarificationRequest,
  values: Record<string, ClarificationFormValue>,
): string {
  const record: Record<string, ClarificationFormValue> = {};
  for (const field of request.fields ?? []) {
    const value = Object.prototype.hasOwnProperty.call(values, field.name)
      ? values[field.name]
      : undefined;
    if (isEmptyFormValue(value)) continue;
    record[field.name] = value!;
  }
  return `${buildFormSummary(request, values)} [values: ${JSON.stringify(record)}]`;
}

/**
 * 构造回答文本：`For your clarification "…", my answer is: …`
 * （发送给模型的 content；与主 UI buildHumanInputResponseText 一致）
 */
export function buildClarificationAnswerText(
  request: ClarificationRequest,
  answer: ClarificationAnswer,
): string {
  let value: string;
  if (answer.kind === 'option') value = answer.option.value;
  else if (answer.kind === 'form') value = buildFormSubmissionValue(request, answer.values);
  else value = answer.value;
  return `For your clarification "${request.question}", my answer is: ${value}`;
}

/** 回答的展示值（消息气泡用人类可读文本） */
export function buildClarificationAnswerDisplay(
  request: ClarificationRequest,
  answer: ClarificationAnswer,
): string {
  if (answer.kind === 'option') return answer.option.label;
  if (answer.kind === 'form') return buildFormSummary(request, answer.values);
  return answer.value;
}
