import { useState } from 'react';
import { api, HttpError } from '../../api/client';
import { Button } from '../ui/Button';

interface Props {
  onClose: () => void;
  /** 비밀번호를 바꾸면 서버가 기존 토큰을 전부 끊는다 — 호출한 본인도 다시 로그인해야 한다. */
  onChanged: () => void;
}

/** 본인 비밀번호 변경. */
export function ChangePasswordModal({ onClose, onChanged }: Props) {
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (newPassword.length < 8) {
      setError('새 비밀번호는 8자 이상이어야 합니다.');
      return;
    }
    if (newPassword !== confirmPassword) {
      setError('새 비밀번호가 서로 다릅니다.');
      return;
    }

    setBusy(true);
    setError(null);
    try {
      await api.changePassword(currentPassword, newPassword);
      onChanged();
    } catch (err) {
      setError(err instanceof HttpError ? err.body.message : '비밀번호를 바꾸지 못했습니다.');
      setBusy(false);
    }
  }

  return (
    <div style={styles.backdrop} onClick={busy ? undefined : onClose}>
      <div style={styles.modal} onClick={(e) => e.stopPropagation()}>
        <header style={styles.header}>
          <h3 style={styles.title}>비밀번호 변경</h3>
          <button style={styles.closeBtn} onClick={onClose} disabled={busy}>✕</button>
        </header>

        <form onSubmit={submit} style={styles.body}>
          <label style={styles.field}>
            <span style={styles.label}>현재 비밀번호</span>
            <input type="password" value={currentPassword} autoComplete="current-password"
                   onChange={(e) => setCurrentPassword(e.target.value)} style={styles.input} />
          </label>
          <label style={styles.field}>
            <span style={styles.label}>새 비밀번호 (8자 이상)</span>
            <input type="password" value={newPassword} autoComplete="new-password"
                   onChange={(e) => setNewPassword(e.target.value)} style={styles.input} />
          </label>
          <label style={styles.field}>
            <span style={styles.label}>새 비밀번호 확인</span>
            <input type="password" value={confirmPassword} autoComplete="new-password"
                   onChange={(e) => setConfirmPassword(e.target.value)} style={styles.input} />
          </label>

          <div style={styles.notice}>
            바꾸고 나면 모든 기기에서 로그아웃됩니다. 새 비밀번호로 다시 로그인해 주세요.
          </div>
          {error && <div style={styles.error}>{error}</div>}

          <footer style={styles.footer}>
            <Button type="button" onClick={onClose} disabled={busy}>취소</Button>
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? '변경 중…' : '변경'}
            </Button>
          </footer>
        </form>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: 'fixed', top: 0, left: 0, right: 0, bottom: 0,
    background: 'rgba(0, 0, 0, 0.55)', display: 'flex', alignItems: 'center',
    justifyContent: 'center', zIndex: 1100, padding: 20,
  },
  modal: {
    background: 'var(--paper)', borderRadius: 12, width: '100%', maxWidth: 420,
    border: '1px solid var(--rule)', boxShadow: '0 20px 40px rgba(0, 0, 0, 0.2)',
    display: 'flex', flexDirection: 'column', overflow: 'hidden',
  },
  header: {
    display: 'flex', justifyContent: 'space-between', alignItems: 'center',
    padding: '16px 20px', borderBottom: '1px solid var(--rule)', background: 'var(--tint)',
  },
  title: { margin: 0, fontSize: 15, fontWeight: 800, color: 'var(--ink)' },
  closeBtn: {
    background: 'transparent', border: 'none', fontSize: 18, cursor: 'pointer',
    color: 'var(--ink-3)', padding: '2px 6px',
  },
  body: { padding: 20, display: 'flex', flexDirection: 'column', gap: 14 },
  field: { display: 'flex', flexDirection: 'column', gap: 6 },
  label: { fontSize: 12.5, fontWeight: 700, color: 'var(--ink-2)' },
  input: {
    padding: '8px 10px', border: '1px solid var(--rule)', borderRadius: 6,
    background: 'var(--paper)', font: 'inherit', width: '100%', boxSizing: 'border-box',
  },
  notice: { fontSize: 12, color: 'var(--ink-3)', lineHeight: 1.5 },
  error: {
    padding: '9px 12px', borderRadius: 6, background: 'var(--err-bg)',
    color: 'var(--err)', fontSize: 12.5, fontWeight: 600,
  },
  footer: { display: 'flex', justifyContent: 'flex-end', gap: 8, paddingTop: 4 },
};
