import React from 'react';

export interface TypingIndicatorProps {
  label?: string;
}

const TypingIndicator: React.FC<TypingIndicatorProps> = ({ label = '正在思考...' }) => {
  return (
    <div className="flex items-center gap-2.5 py-2 px-1 mb-2">
      <div className="flex items-center gap-1">
        {[0, 1, 2].map(i => (
          <span
            key={i}
            className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--text-3)]"
            style={{
              animation: 'dot-bounce 1.2s ease-in-out infinite',
              animationDelay: `${i * 0.15}s`,
            }}
          />
        ))}
      </div>
      <span className="text-[12px] text-[var(--text-3)]">{label}</span>
    </div>
  );
};

export default React.memo(TypingIndicator);
