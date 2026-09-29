import { useEffect, useState } from 'react';
import { api, tokenStore } from './api/client';
import type { CurrentUser } from './api/types';
import { LoginPage } from './components/LoginPage';
import { HomePage } from './components/HomePage';

export default function App() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [checking, setChecking] = useState(true);

  // 새로고침 후 저장된 토큰으로 세션을 복원한다.
  // 토큰이 만료·위조되었으면 게이트웨이가 401 을 주므로 로그인 화면으로 돌린다.
  useEffect(() => {
    if (!tokenStore.access) {
      setChecking(false);
      return;
    }
    api
      .me()
      .then(setUser)
      .catch(() => tokenStore.clear())
      .finally(() => setChecking(false));
  }, []);

  if (checking) {
    return <div style={{ display: 'grid', placeItems: 'center', height: '100%', color: '#6b7280' }}>확인 중…</div>;
  }

  return user ? <HomePage user={user} onSignOut={() => setUser(null)} /> : <LoginPage onSignedIn={setUser} />;
}
