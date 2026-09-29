import { useState, type FormEvent } from 'react';
import { api, HttpError, tokenStore } from '../api/client';
import type { CurrentUser } from '../api/types';

interface Props {
  onSignedIn: (user: CurrentUser) => void;
}

/** 로그인. 인증 실패 시 어느 항목이 틀렸는지 구분하지 않는다. */
export function LoginPage({ onSignedIn }: Props) {
  const [email, setEmail] = useState('admin@company.com');
  const [password, setPassword] = useState('password123');
  const [error, setError] = useState<string | null>(null);
  const [correlationId, setCorrelationId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setCorrelationId(null);
    try {
      const result = await api.login(email, password);
      tokenStore.save(result);
      onSignedIn(result.user);
    } catch (e) {
      const message = e instanceof HttpError ? e.body.message : '서버에 연결하지 못했습니다.';
      setError(message);
      if (e instanceof HttpError) setCorrelationId(e.body.correlationId ?? null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={styles.wrap}>
      <form onSubmit={submit} style={styles.card}>
        <div style={styles.kicker}>조직 의사결정 히스토리</div>
        <h1 style={styles.title}>로그인</h1>
        <p style={styles.sub}>회사 계정으로 접속합니다</p>

        <label style={styles.label} htmlFor="email">이메일</label>
        <input
          id="email"
          type="email"
          value={email}
          autoComplete="username"
          onChange={(e) => setEmail(e.target.value)}
          required
        />

        <label style={{ ...styles.label, marginTop: 14 }} htmlFor="password">비밀번호</label>
        <input
          id="password"
          type="password"
          value={password}
          autoComplete="current-password"
          onChange={(e) => setPassword(e.target.value)}
          required
        />

        {error && (
          <div role="alert" style={styles.error}>
            {error}
            {correlationId && <div style={styles.corr}>추적 ID {correlationId}</div>}
          </div>
        )}

        <button type="submit" disabled={busy} style={styles.submit}>
          {busy ? '확인 중…' : '로그인'}
        </button>

        <div style={styles.hint}>
          개발 시드 계정 — admin@company.com · lead@company.com · member@company.com
          <br />비밀번호는 모두 password123 입니다.
        </div>
      </form>
    </main>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrap: { display: 'grid', placeItems: 'center', minHeight: '100%', padding: 24 },
  card: {
    width: 380, background: 'var(--paper)', border: '1px solid var(--rule)',
    borderRadius: 10, padding: '32px 28px',
    boxShadow: '0 1px 3px rgba(20,22,26,.06), 0 12px 40px rgba(20,22,26,.06)',
  },
  kicker: { fontSize: 11, letterSpacing: '.14em', textTransform: 'uppercase', color: 'var(--ink-3)', fontWeight: 600 },
  title: { fontSize: 24, fontWeight: 800, margin: '10px 0 4px', letterSpacing: '-.02em' },
  sub: { color: 'var(--ink-3)', margin: '0 0 22px', fontSize: 13 },
  label: { display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--ink-2)', marginBottom: 6 },
  error: {
    marginTop: 14, padding: '10px 12px', background: 'var(--err-bg)',
    border: '1px solid #f5c9c9', borderRadius: 'var(--radius)', color: 'var(--err)', fontSize: 13,
  },
  corr: { fontFamily: 'var(--mono)', fontSize: 11, marginTop: 4, opacity: 0.8 },
  submit: {
    width: '100%', marginTop: 20, padding: '11px 0', background: 'var(--ink)',
    color: '#fff', border: '1px solid var(--ink)', fontWeight: 700,
  },
  hint: {
    marginTop: 18, paddingTop: 14, borderTop: '1px solid var(--rule-soft)',
    fontSize: 11.5, color: 'var(--ink-3)', lineHeight: 1.7,
  },
};
