/**
 * 브라우저 뒤로가기/앞으로가기 지원.
 *
 * 라우팅 라이브러리 없이, 탭/화면 상태를 쿼리스트링(History API)에 그대로 반영한다.
 * 앱이 쓰는 정식 URL 은 언제나 "/?tab=..." 형태다.
 *
 * 다만 진입은 경로로도 받는다. governance-worker 가 보내는 알림(Slack/Teams/메일)의
 * 링크가 `/documents/{id}`·`/governance/conflicts/{id}` 같은 경로 형태이기 때문이다
 * (notifications/models.py 의 source_doc_url·conflict_url). nginx 에 SPA 폴백이 있어
 * 그 경로로도 앱이 뜨지만, pathname 을 읽지 않으면 알림 링크가 전부 기본 화면으로
 * 떨어진다. 진입 시 경로를 해석해 상태로 바꾸고, 그 뒤 정식 쿼리스트링 URL 로 되돌려
 * 놓는다(HomePage 의 첫 replaceNav).
 */

export type Tab = 'chat' | 'timeline' | 'documents' | 'governance' | 'indexing' | 'users' | 'admin' | 'ops' | 'metrics';

export type DocView =
  | { name: 'list' }
  | { name: 'detail'; id: number }
  | { name: 'form'; id: number | null }
  | { name: 'import' };

export interface NavState {
  tab: Tab;
  docView: DocView;
  convId: number | null;
  /** 알림 링크로 들어왔을 때 펼쳐 보여줄 상충 id. 한 번 쓰고 버리는 값이라 정식 URL 에는 남기지 않는다. */
  conflictId: number | null;
}

const TABS: Tab[] = ['chat', 'timeline', 'documents', 'governance', 'indexing', 'users', 'admin', 'ops', 'metrics'];

export const DEFAULT_NAV: NavState = { tab: 'chat', docView: { name: 'list' }, convId: null, conflictId: null };

/** vite 의 base 가 "/" 라 앱은 항상 루트에 있다. 알림 링크 경로로 들어와도 정식 URL 은 루트 기준으로 쓴다. */
const APP_ROOT = '/';

const DOCUMENT_PATH = /^\/documents\/(\d+)\/?$/;
const CONFLICT_PATH = /^\/governance\/conflicts\/(\d+)\/?$/;

/**
 * 알림 링크처럼 경로로 들어온 진입점을 상태로 바꾼다. 해당 없으면 null.
 * window 를 건드리지 않는 순수 함수다(테스트·검증용).
 */
export function parsePathEntry(pathname: string): NavState | null {
  const document = pathname.match(DOCUMENT_PATH);
  if (document) {
    return { ...DEFAULT_NAV, tab: 'documents', docView: { name: 'detail', id: Number(document[1]) } };
  }
  const conflict = pathname.match(CONFLICT_PATH);
  if (conflict) {
    return { ...DEFAULT_NAV, tab: 'governance', conflictId: Number(conflict[1]) };
  }
  return null;
}

export function readNavState(): NavState {
  const fromPath = parsePathEntry(window.location.pathname);
  if (fromPath) return fromPath;

  const params = new URLSearchParams(window.location.search);
  const tabParam = params.get('tab');
  const tab: Tab = (TABS as string[]).includes(tabParam ?? '') ? (tabParam as Tab) : DEFAULT_NAV.tab;

  let docView: DocView = { name: 'list' };
  if (tab === 'documents') {
    const view = params.get('view');
    const idParam = params.get('id');
    const id = idParam !== null && idParam !== '' ? Number(idParam) : null;
    if (view === 'detail' && id !== null) docView = { name: 'detail', id };
    else if (view === 'form') docView = { name: 'form', id };
    else if (view === 'import') docView = { name: 'import' };
  }

  let convId: number | null = null;
  if (tab === 'chat') {
    const convParam = params.get('conv');
    convId = convParam !== null && convParam !== '' ? Number(convParam) : null;
  }

  return { tab, docView, convId, conflictId: null };
}

function toUrl(state: NavState): string {
  const params = new URLSearchParams();
  params.set('tab', state.tab);
  if (state.tab === 'documents') {
    if (state.docView.name === 'detail') {
      params.set('view', 'detail');
      params.set('id', String(state.docView.id));
    } else if (state.docView.name === 'form') {
      params.set('view', 'form');
      if (state.docView.id !== null) params.set('id', String(state.docView.id));
    } else if (state.docView.name === 'import') {
      params.set('view', 'import');
    }
  }
  if (state.tab === 'chat' && state.convId !== null) {
    params.set('conv', String(state.convId));
  }
  const query = params.toString();
  return query ? `${APP_ROOT}?${query}` : APP_ROOT;
}

/** 화면 전환 시 히스토리에 새 항목을 쌓는다 — 뒤로가기로 이전 화면에 돌아올 수 있게 한다. */
export function pushNav(state: NavState) {
  const url = toUrl(state);
  if (url === window.location.pathname + window.location.search) return;
  window.history.pushState(state, '', url);
}

/** 히스토리 항목을 늘리지 않고 현재 위치만 갱신한다 (초기 진입, popstate 동기화 시 사용). */
export function replaceNav(state: NavState) {
  window.history.replaceState(state, '', toUrl(state));
}
