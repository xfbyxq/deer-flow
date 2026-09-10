import React from 'react';

const GRADIENTS = [
  'av-violet',
  'av-green',
  'av-orange',
  'av-purple',
  'av-cyan',
  'av-pink',
  'av-amber',
  'av-blue',
] as const;

function pickGradient(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i++) {
    hash = (hash * 31 + name.charCodeAt(i)) | 0;
  }
  return GRADIENTS[Math.abs(hash) % GRADIENTS.length];
}

export interface AvatarProps {
  name: string;
  size?: 'sm' | 'xs' | 'default';
  presence?: 'online' | 'offline' | 'busy';
}

const sizeClasses: Record<string, string> = {
  default: 'w-[34px] h-[34px] text-sm',
  sm: 'w-[26px] h-[26px] text-[11.5px]',
  xs: 'w-[22px] h-[22px] text-[10px]',
};

const presenceColor: Record<string, string> = {
  online: 'bg-[var(--green)]',
  offline: 'bg-[#c8cdd6]',
  busy: 'bg-[var(--amber)]',
};

const Avatar: React.FC<AvatarProps> = ({ name, size = 'default', presence }) => {
  const letter = name.charAt(0).toUpperCase() || '?';
  const gradient = pickGradient(name);

  return (
    <div
      className={`relative inline-flex shrink-0 items-center justify-center rounded-full font-semibold text-white ${gradient} ${sizeClasses[size]}`}
    >
      {letter}
      {presence && (
        <span
          className={`absolute -right-px -bottom-px rounded-full border-2 border-white ${presenceColor[presence]}`}
          style={{ width: size === 'xs' ? 7 : size === 'sm' ? 8 : 10, height: size === 'xs' ? 7 : size === 'sm' ? 8 : 10 }}
        />
      )}
    </div>
  );
};

export default React.memo(Avatar);
