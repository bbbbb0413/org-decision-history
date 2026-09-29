import type { ButtonHTMLAttributes, CSSProperties } from 'react';

type Variant = 'primary' | 'default' | 'ghost' | 'danger';

const VARIANTS: Record<Variant, CSSProperties> = {
  primary: { background: 'var(--ink)', color: '#fff', border: '1px solid var(--ink)' },
  default: { background: 'var(--paper)', color: 'var(--ink)', border: '1px solid var(--rule)' },
  ghost: { background: 'transparent', color: 'var(--ink-3)', border: '1px solid transparent' },
  danger: { background: 'var(--paper)', color: 'var(--err)', border: '1px solid #f5c9c9' },
};

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: 'sm' | 'md';
}

export function Button({ variant = 'default', size = 'md', style, ...rest }: Props) {
  return (
    <button
      {...rest}
      style={{
        padding: size === 'sm' ? '5px 10px' : '8px 14px',
        fontSize: size === 'sm' ? 12 : 13,
        fontWeight: 600,
        whiteSpace: 'nowrap',
        ...VARIANTS[variant],
        ...style,
      }}
    />
  );
}
