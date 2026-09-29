import type { ReactNode } from 'react';

interface Props {
  label: string;
  required?: boolean;
  error?: string;
  children: ReactNode;
}

/**
 * 필수 표시와 필드 단위 오류를 함께 보여준다.
 *
 * `<label>` 이 입력 요소를 감싼다. 형제 관계로 두고 `htmlFor` 도 없으면 스크린 리더가 필드
 * 이름을 읽지 못하고, 라벨을 눌러도 입력칸에 초점이 가지 않는다. 감싸면 id 를 끼워 넣지
 * 않고도 묶인다(암묵적 연결).
 */
export function Field({ label, required, error, children }: Props) {
  return (
    <label style={{ display: 'block', marginBottom: 14 }}>
      <span style={{ display: 'block', fontSize: 12, fontWeight: 700, color: 'var(--ink-2)', marginBottom: 5 }}>
        {label}
        {required && <span style={{ color: 'var(--err)', marginLeft: 3 }}>*</span>}
      </span>
      {children}
      {error && (
        <div style={{ fontSize: 11.5, color: 'var(--err)', marginTop: 4 }}>{error}</div>
      )}
    </label>
  );
}
