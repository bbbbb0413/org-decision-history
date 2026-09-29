import type { CSSProperties, ReactNode } from 'react';

type Tone = 'neutral' | 'accent' | 'ok' | 'warn' | 'err';

const TONES: Record<Tone, CSSProperties> = {
  neutral: { background: 'var(--tint)', color: 'var(--ink-2)', borderColor: 'var(--rule)' },
  accent: { background: 'var(--accent-bg)', color: 'var(--accent)', borderColor: '#ccd9fb' },
  ok: { background: 'var(--ok-bg)', color: 'var(--ok)', borderColor: '#bfe3d6' },
  warn: { background: 'var(--warn-bg)', color: 'var(--warn)', borderColor: '#f0d8bf' },
  err: { background: 'var(--err-bg)', color: 'var(--err)', borderColor: '#f5c9c9' },
};

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      style={{
        display: 'inline-block', fontSize: 11, fontWeight: 700, padding: '2px 7px',
        borderRadius: 4, border: '1px solid', whiteSpace: 'nowrap', ...TONES[tone],
      }}
    >
      {children}
    </span>
  );
}
