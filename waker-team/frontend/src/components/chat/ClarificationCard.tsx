import React, { useMemo, useState } from 'react';
import type { ClarificationField, ClarificationRequest } from '../../types';
import {
  buildInitialFormValues,
  findMissingRequiredFields,
  type ClarificationAnswer,
  type ClarificationFormValue,
} from '../../utils/clarification';

const TYPE_ICONS: Record<string, string> = {
  missing_info: '❓',
  ambiguous_requirement: '🤔',
  approach_choice: '🔀',
  risk_confirmation: '⚠️',
  suggestion: '💡',
};

export interface ClarificationCardProps {
  request: ClarificationRequest;
  /** 已答（其后已有用户消息）→ 禁用交互并显示已回答态 */
  answered: boolean;
  /** 已答时的回答摘要（从回答消息 meta 读取，可空） */
  answeredValue?: string | null;
  /** 提交回答（父组件负责发送消息与等待态联动） */
  onSubmit: (answer: ClarificationAnswer) => void | Promise<void>;
  /** 提交请求进行中（防重复提交） */
  submitting?: boolean;
}

const inputClass =
  'w-full rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-2 text-[13px] text-[var(--text)] outline-none transition-colors focus:border-[var(--primary)] placeholder:text-[var(--text-3)]';

/** 单个表单字段控件 */
const FieldControl: React.FC<{
  field: ClarificationField;
  value: ClarificationFormValue | undefined;
  disabled: boolean;
  onChange: (value: ClarificationFormValue) => void;
}> = ({ field, value, disabled, onChange }) => {
  switch (field.type) {
    case 'textarea':
      return (
        <textarea
          className={`${inputClass} resize-none`}
          rows={2}
          value={typeof value === 'string' ? value : ''}
          placeholder={field.placeholder}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case 'number':
      return (
        <input
          type="number"
          className={inputClass}
          value={typeof value === 'number' || typeof value === 'string' ? value : ''}
          placeholder={field.placeholder}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
        />
      );
    case 'date':
      return (
        <input
          type="date"
          className={inputClass}
          value={typeof value === 'string' ? value : ''}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      );
    case 'select':
      return (
        <select
          className={inputClass}
          value={typeof value === 'string' ? value : ''}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">{field.placeholder || '请选择…'}</option>
          {(field.options ?? []).map((opt) => (
            <option key={opt.id} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      );
    case 'multi_select': {
      const selected = Array.isArray(value) ? value : [];
      return (
        <div className="flex flex-wrap gap-1.5">
          {(field.options ?? []).map((opt) => {
            const active = selected.includes(opt.value);
            return (
              <button
                key={opt.id}
                type="button"
                disabled={disabled}
                onClick={() =>
                  onChange(
                    active ? selected.filter((v) => v !== opt.value) : [...selected, opt.value],
                  )
                }
                className={`rounded-full border px-2.5 py-1 text-[12px] transition-colors ${
                  active
                    ? 'border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary)] font-medium'
                    : 'border-[var(--border)] text-[var(--text-2)] hover:bg-[var(--panel-2)]'
                } ${disabled ? 'cursor-not-allowed opacity-60' : ''}`}
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      );
    }
    case 'checkbox':
      return (
        <label className="flex items-center gap-2 text-[13px] text-[var(--text)]">
          <input
            type="checkbox"
            checked={value === true}
            disabled={disabled}
            onChange={(e) => onChange(e.target.checked)}
            className="h-3.5 w-3.5 accent-[var(--primary)]"
          />
          {field.placeholder || '是'}
        </label>
      );
    default:
      return (
        <input
          type="text"
          className={inputClass}
          value={typeof value === 'string' ? value : ''}
          placeholder={field.placeholder}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
      );
  }
};

/**
 * 澄清交互卡片：对齐 DeerFlow 主 UI 的 human_input_request 卡片。
 * - options / choice_with_other：选项按钮，点击即提交回答；
 * - form：字段表单（text/textarea/number/select/multi_select/checkbox/date）+ 必填校验；
 * - free_text：提示在下方输入框回复；
 * - 已答：整卡禁用并显示已回答摘要。
 */
const ClarificationCard: React.FC<ClarificationCardProps> = ({
  request,
  answered,
  answeredValue,
  onSubmit,
  submitting = false,
}) => {
  const [formValues, setFormValues] = useState<Record<string, ClarificationFormValue>>(() =>
    buildInitialFormValues(request.fields ?? []),
  );
  const [formError, setFormError] = useState<string | null>(null);

  const icon = TYPE_ICONS[request.clarification_type ?? ''] ?? '❓';
  const disabled = answered || submitting;
  const options = request.options ?? [];

  const optionsVisible = useMemo(
    () => request.input_mode === 'single_choice' || request.input_mode === 'choice_with_other',
    [request.input_mode],
  );

  const setFieldValue = (name: string, value: ClarificationFormValue) => {
    setFormValues((prev) => ({ ...prev, [name]: value }));
    setFormError(null);
  };

  const handleFormSubmit = () => {
    if (disabled) return;
    const fields = request.fields ?? [];
    const missing = findMissingRequiredFields(fields, formValues);
    if (missing.length > 0) {
      setFormError(`请填写必填项：${missing.map((f) => f.label).join('、')}`);
      return;
    }
    void onSubmit({ kind: 'form', values: formValues });
  };

  return (
    <div
      className="rounded-xl border border-violet-200 bg-violet-50/60 px-3.5 py-3 shadow-sm"
      data-testid="clarification-card"
    >
      {/* 头部：图标 + 问题 + 已答标记 */}
      <div className="flex items-start gap-2">
        <span className="mt-px text-[15px] leading-5" aria-hidden="true">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-medium leading-relaxed text-[var(--text)]">
            {request.question}
          </div>
          {request.context && (
            <div className="mt-1 text-[12px] leading-relaxed text-[var(--text-2)]">
              {request.context}
            </div>
          )}
        </div>
        {answered && (
          <span className="ml-2 shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
            ✓ 已回答
          </span>
        )}
      </div>

      {/* 已回答摘要 */}
      {answered && answeredValue && (
        <div className="mt-2 rounded-lg bg-[var(--panel)] px-2.5 py-1.5 text-[12.5px] text-[var(--text-2)]">
          {answeredValue}
        </div>
      )}

      {/* 选项按钮（点击即提交） */}
      {optionsVisible && !answered && (
        <div className="mt-2.5 flex flex-col gap-1.5">
          {options.map((opt) => (
            <button
              key={opt.id}
              type="button"
              disabled={disabled}
              onClick={() => void onSubmit({ kind: 'option', option: opt })}
              className={`w-full rounded-lg border border-[var(--border)] bg-[var(--panel)] px-3 py-1.5 text-left text-[13px] text-[var(--text)] transition-colors hover:border-[var(--primary)] hover:bg-[var(--primary-soft)] ${
                disabled ? 'cursor-not-allowed opacity-60' : ''
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}

      {/* 表单 */}
      {request.input_mode === 'form' && !answered && (
        <div className="mt-2.5 space-y-2">
          {(request.fields ?? []).map((field) => (
            <div key={field.name}>
              <div className="mb-1 text-[12px] font-medium text-[var(--text-2)]">
                {field.label}
                {field.required && <span className="ml-0.5 text-red-500">*</span>}
              </div>
              <FieldControl
                field={field}
                value={formValues[field.name]}
                disabled={disabled}
                onChange={(v) => setFieldValue(field.name, v)}
              />
            </div>
          ))}
          {formError && <div className="text-[12px] text-red-500">{formError}</div>}
          <button
            type="button"
            disabled={disabled}
            onClick={handleFormSubmit}
            className={`rounded-lg px-3.5 py-1.5 text-[12.5px] font-medium transition-colors ${
              disabled
                ? 'cursor-not-allowed bg-[var(--panel-3)] text-[var(--text-3)]'
                : 'bg-[var(--primary)] text-white hover:bg-[var(--primary-deep)]'
            }`}
          >
            {submitting ? '提交中…' : '提交'}
          </button>
        </div>
      )}

      {/* free_text 提示 */}
      {request.input_mode === 'free_text' && !answered && (
        <div className="mt-2 text-[12px] text-[var(--text-3)]">请在下方输入框回复</div>
      )}
    </div>
  );
};

export default React.memo(ClarificationCard);
