import { useEffect, useRef, useState } from 'react';
import { api, tokenStore } from '../api/client';
import type { CurrentUser } from '../api/types';
import { DocumentList } from './documents/DocumentList';
import { DocumentDetailView } from './documents/DocumentDetailView';
import { DocumentForm } from './documents/DocumentForm';
import { ImportPanel } from './documents/ImportPanel';
import { OpsPanel } from './admin/OpsPanel';
import { ProductMetricsPanel } from './admin/ProductMetricsPanel';
import { PolicyPanel } from './admin/PolicyPanel';
import { UserManagementPanel } from './admin/UserManagementPanel';
import { TeamNotificationSettingsPanel } from './admin/TeamNotificationSettingsPanel';
import { ChangePasswordModal } from './admin/ChangePasswordModal';
import { IndexJobPanel } from './indexing/IndexJobPanel';
import { ChatView } from './chat/ChatView';
import { TimelineView } from './timeline/TimelineView';
import { GovernanceDashboard } from './governance/GovernanceDashboard';
import { readNavState, pushNav, replaceNav, type DocView, type Tab } from '../nav';

interface Props {
  user: CurrentUser;
  onSignOut: () => void;
}

type View = DocView;

export function HomePage({ user, onSignOut }: Props) {
  const initialNav = useRef(readNavState()).current;
  const [tab, setTab] = useState<Tab>(initialNav.tab);
  const [view, setView] = useState<View>(initialNav.docView);
  const [convId, setConvId] = useState<number | null>(initialNav.convId);
  const [reloadKey, setReloadKey] = useState(0);
  // 알림 링크(/governance/conflicts/{id})로 들어왔을 때 대시보드가 펼쳐 보여줄 상충. 한 번 쓰고 비운다.
  const [focusConflictId, setFocusConflictId] = useState<number | null>(initialNav.conflictId);
  const [showPasswordModal, setShowPasswordModal] = useState(false);
  // popstate 로 되돌아온 상태 변경은 다시 히스토리에 쌓으면 안 되므로 표시해둔다.
  const skipNextPushRef = useRef(true);

  // 탭/화면/대화 선택이 바뀔 때마다 URL 에 반영해 뒤로가기가 동작하게 한다.
  useEffect(() => {
    if (skipNextPushRef.current) {
      skipNextPushRef.current = false;
      replaceNav({ tab, docView: view, convId, conflictId: null });
      return;
    }
    pushNav({ tab, docView: view, convId, conflictId: null });
  }, [tab, view, convId]);

  useEffect(() => {
    function onPopState() {
      const state = readNavState();
      skipNextPushRef.current = true;
      setTab(state.tab);
      setView(state.docView);
      setConvId(state.convId);
      setFocusConflictId(state.conflictId);
    }
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const isAdmin = user.roles.some((r) => r !== 'MEMBER');
  // 색인 현황은 시스템 관리자만 본다
  const isSystemAdmin = user.roles.includes('SYSTEM_ADMIN');

  function backToList() {
    setView({ name: 'list' });
    setReloadKey((k) => k + 1);
  }

  // 서버 쪽 토큰도 무효화한다 — 실패해도(이미 만료됐거나 네트워크 오류)
  // 로컬 토큰은 지우고 로그인 화면으로 보낸다, 사용자를 여기 붙잡아 둘 이유가 없다
  async function handleSignOut() {
    try {
      await api.logout();
    } catch {
      // 무시한다
    } finally {
      tokenStore.clear();
      onSignOut();
    }
  }

  return (
    <div style={styles.shell}>
      <header style={styles.bar}>
        <strong style={styles.brand}>조직 의사결정 히스토리</strong>
        <nav style={styles.nav}>
          <button onClick={() => setTab('chat')} style={navStyle(tab === 'chat')}>대화</button>
          <button onClick={() => setTab('timeline')} style={navStyle(tab === 'timeline')}>타임라인</button>
          <button onClick={() => { setTab('documents'); setView({ name: 'list' }); }}
                  style={navStyle(tab === 'documents')}>문서</button>
          <button onClick={() => setTab('governance')} style={navStyle(tab === 'governance')}>거버넌스</button>
          {isSystemAdmin && (
            <button onClick={() => setTab('indexing')} style={navStyle(tab === 'indexing')}>색인</button>
          )}
          {isSystemAdmin && (
            <button onClick={() => setTab('metrics')} style={navStyle(tab === 'metrics')}>제품 지표</button>
          )}
          {isSystemAdmin && (
            <button onClick={() => setTab('ops')} style={navStyle(tab === 'ops')}>운영 현황</button>
          )}
          {isAdmin && (
            <button onClick={() => setTab('users')} style={navStyle(tab === 'users')}>사용자 관리</button>
          )}
          {isAdmin && (
            <button onClick={() => setTab('admin')} style={navStyle(tab === 'admin')}>접근 관리</button>
          )}
        </nav>
        <div style={styles.who}>
          <span style={styles.name}>{user.name}</span>
          <span style={styles.org}>{user.organizationName ?? '조직 미배정'}</span>
        </div>
        <button onClick={() => setShowPasswordModal(true)} style={styles.signOut}>비밀번호 변경</button>
        <button onClick={handleSignOut} style={styles.signOut}>로그아웃</button>
      </header>

      {showPasswordModal && (
        <ChangePasswordModal
          onClose={() => setShowPasswordModal(false)}
          // 서버가 기존 토큰을 전부 끊었으므로 로컬 토큰도 지우고 로그인 화면으로 보낸다
          onChanged={() => { tokenStore.clear(); onSignOut(); }}
        />
      )}

      <main style={styles.main}>
        {tab === 'chat' && (
          <ChatView
            currentId={convId}
            onSelect={setConvId}
            onOpenDocument={(id) => { setTab('documents'); setView({ name: 'detail', id }); }}
          />
        )}
        {tab === 'timeline' && (
          <TimelineView onOpenDocument={(id) => { setTab('documents'); setView({ name: 'detail', id }); }} />
        )}
        {tab === 'documents' && view.name === 'list' && (
          <DocumentList
            reloadKey={reloadKey}
            onOpen={(id) => setView({ name: 'detail', id })}
            onCreate={() => setView({ name: 'form', id: null })}
            onImport={() => setView({ name: 'import' })}
          />
        )}
        {tab === 'documents' && view.name === 'import' && (
          <ImportPanel user={user} onDone={backToList} />
        )}
        {tab === 'documents' && view.name === 'detail' && (
          <DocumentDetailView
            documentId={view.id}
            onBack={backToList}
            onEdit={(id) => setView({ name: 'form', id })}
            onDeleted={backToList}
          />
        )}
        {tab === 'documents' && view.name === 'form' && (
          <DocumentForm
            user={user}
            documentId={view.id}
            onDone={(id) => { setReloadKey((k) => k + 1); setView({ name: 'detail', id }); }}
            onCancel={() => (view.id ? setView({ name: 'detail', id: view.id }) : backToList())}
          />
        )}
        {tab === 'indexing' && <IndexJobPanel />}
        {tab === 'governance' && (
          <GovernanceDashboard
            user={user}
            focusConflictId={focusConflictId}
            onFocusConsumed={() => setFocusConflictId(null)}
            onOpenDocument={(id) => { setTab('documents'); setView({ name: 'detail', id }); }}
          />
        )}
        {tab === 'metrics' && <ProductMetricsPanel />}
        {tab === 'ops' && <OpsPanel />}
        {tab === 'users' && (
          <>
            <UserManagementPanel user={user} />
            <TeamNotificationSettingsPanel user={user} />
          </>
        )}
        {tab === 'admin' && <PolicyPanel />}
      </main>
    </div>
  );
}

function navStyle(active: boolean): React.CSSProperties {
  return {
    padding: '6px 12px', fontSize: 13, fontWeight: 700, background: active ? 'var(--tint)' : 'transparent',
    border: '1px solid ' + (active ? 'var(--rule)' : 'transparent'),
    color: active ? 'var(--ink)' : 'var(--ink-3)',
  };
}

const styles: Record<string, React.CSSProperties> = {
  shell: { minHeight: '100%', display: 'flex', flexDirection: 'column' },
  bar: {
    display: 'flex', alignItems: 'center', gap: 16, padding: '10px 20px',
    background: 'var(--paper)', borderBottom: '1px solid var(--rule)',
  },
  brand: { fontSize: 14, fontWeight: 800 },
  nav: { display: 'flex', gap: 4 },
  who: { marginLeft: 'auto', textAlign: 'right', lineHeight: 1.3 },
  name: { display: 'block', fontSize: 13, fontWeight: 700 },
  org: { display: 'block', fontSize: 11, color: 'var(--ink-3)' },
  signOut: {
    padding: '6px 12px', background: 'transparent', border: '1px solid var(--rule)',
    color: 'var(--ink-2)', fontSize: 12.5, fontWeight: 600,
  },
  main: { flex: 1, width: '100%', maxWidth: 1100, margin: '0 auto', padding: '24px 20px 48px' },
};
