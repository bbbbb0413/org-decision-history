import type {
  AccessPolicy, ApiError, AuditEntry, CurrentUser, DlqActionResult, DlqEntry, DlqStatus,
  DocumentDetail, DocumentFileInfo,
  AnswerSource, Conversation, DocumentPage, DocumentType, DocumentVersionDetail, DocumentVersionInfo, DownloadUrl,
  GraphData, ImportJob, ImportJobDetail, IndexJob, IndexJobItem, IssuedTemporaryPassword, IssuedUserAccount,
  Message, OpsError, OpsHealth,
  OpsIndexStatus, Organization, Paged, SystemRole, Team, TeamNotificationChannel, TimelineResponse, TokenResponse,
  TraceSpan, UserDetail, UserSummary, ConflictItem, ResolveConflictRequest,
  ProductMetrics, FeedbackItem, FeedbackDetail,
} from './types';

const ACCESS_KEY = 'history.accessToken';
const REFRESH_KEY = 'history.refreshToken';

/** 게이트웨이를 직접 호출한다. nginx 프록시 없이도 동작하도록 빌드 시점에 주입한다. */
const API_BASE = import.meta.env.VITE_GATEWAY_URL ?? '';

export const tokenStore = {
  get access() {
    try { return localStorage.getItem(ACCESS_KEY); } catch { return null; }
  },
  get refresh() {
    try { return localStorage.getItem(REFRESH_KEY); } catch { return null; }
  },
  save(tokens: { accessToken: string; refreshToken: string }) {
    try {
      localStorage.setItem(ACCESS_KEY, tokens.accessToken);
      localStorage.setItem(REFRESH_KEY, tokens.refreshToken);
    } catch {
      // 프라이빗 모드 등 저장이 막힌 환경에서는 메모리 상태만으로 동작한다
    }
  },
  clear() {
    try {
      localStorage.removeItem(ACCESS_KEY);
      localStorage.removeItem(REFRESH_KEY);
    } catch { /* 무시한다 */ }
  },
};

export class HttpError extends Error {
  constructor(readonly status: number, readonly body: ApiError) {
    super(body.message);
  }
}

/**
 * 액세스 토큰(기본 30분)이 만료되면 재발급 토큰(기본 14일)으로 조용히 갱신한다.
 *
 * 갱신하지 않으면 30분이 지날 때마다 서버가 14일짜리 세션을 인정하는데도 사용자가
 * 로그인 화면으로 튕겨 나간다.
 *
 * 동시에 여러 요청이 401 을 받아도 재발급은 한 번만 돈다 — 재발급이 겹치면 나중 것이
 * 앞의 토큰을 밀어내 서로를 무효화한다.
 */
let refreshInFlight: Promise<string | null> | null = null;

async function refreshAccessToken(): Promise<string | null> {
  return refreshInFlight ?? (refreshInFlight = (async () => {
    const refreshToken = tokenStore.refresh;
    if (!refreshToken) return null;
    try {
      const response = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${refreshToken}` },
      });
      if (!response.ok) return null;
      const tokens: TokenResponse = await response.json();
      tokenStore.save(tokens);
      return tokens.accessToken;
    } catch {
      return null;
    }
  })().finally(() => { refreshInFlight = null; }));
}

/** 재발급 자체를 다시 재발급하려 들면 안 된다. 로그인·재발급은 401 이어도 그대로 던진다. */
const NO_RETRY_PATHS = ['/api/auth/login', '/api/auth/refresh'];

async function send<T>(path: string, init: RequestInit = {}, json = true, retried = false): Promise<T> {
  const headers = new Headers(init.headers);
  if (json && init.body) headers.set('Content-Type', 'application/json');
  const token = tokenStore.access;
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });
  const correlationId = response.headers.get('X-Correlation-Id') ?? undefined;

  if (response.status === 401 && !retried && !NO_RETRY_PATHS.includes(path) && tokenStore.refresh) {
    const renewed = await refreshAccessToken();
    if (renewed) return send<T>(path, init, json, true);
    // 재발급 토큰도 죽었다(만료됐거나 로그아웃·비밀번호 변경으로 무효화됐다) — 로그인부터 다시 한다
    tokenStore.clear();
  }

  if (!response.ok) {
    let body: ApiError;
    try {
      body = await response.json();
    } catch {
      body = { code: 'UNKNOWN', message: `요청에 실패했습니다 (${response.status})` };
    }
    throw new HttpError(response.status, { ...body, correlationId: body.correlationId ?? correlationId });
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function qs(params: Record<string, unknown>) {
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    if (Array.isArray(v)) v.forEach((item) => search.append(k, String(item)));
    else search.set(k, String(v));
  }
  const s = search.toString();
  return s ? `?${s}` : '';
}

export const api = {
  login: (email: string, password: string) =>
    send<TokenResponse>('/api/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),

  logout: () => send<void>('/api/auth/logout', { method: 'POST' }),

  me: () => send<CurrentUser>('/api/auth/me'),

  // ── 문서 ──────────────────────────────
  documents: (params: Record<string, unknown>) => send<DocumentPage>(`/api/documents${qs(params)}`),

  document: (id: number) => send<DocumentDetail>(`/api/documents/${id}`),

  createDocument: (body: unknown) =>
    send<DocumentDetail>('/api/documents', { method: 'POST', body: JSON.stringify(body) }),

  updateDocument: (id: number, body: unknown) =>
    send<DocumentDetail>(`/api/documents/${id}`, { method: 'PUT', body: JSON.stringify(body) }),

  deleteDocument: (id: number) => send<void>(`/api/documents/${id}`, { method: 'DELETE' }),

  versions: (id: number) => send<DocumentVersionInfo[]>(`/api/documents/${id}/versions`),

  /** 그 버전 시점의 본문. 버전 비교(diff)의 재료다. */
  version: (id: number, version: number) =>
    send<DocumentVersionDetail>(`/api/documents/${id}/versions/${version}`),

  documentGraph: (documentId: number) => send<GraphData>(`/api/documents/${documentId}/graph`),

  documentsGraph: (documentIds: number[]) =>
    send<GraphData>(`/api/documents/graph${qs({ ids: documentIds })}`),

  // ── 원본 파일 ─────────────────────────
  uploadFile: (documentId: number, file: File) => {
    const form = new FormData();
    form.append('file', file);
    return send<DocumentFileInfo>(`/api/documents/${documentId}/file`, { method: 'POST', body: form }, false);
  },

  /** 누른 시점에 발급한다. 목록 응답에 미리 담지 않는 이유는 유효 시간이 60초이기 때문이다. */
  downloadUrl: (fileId: number) =>
    send<DownloadUrl>(`/api/document-files/${fileId}/download-url`, { method: 'POST' }),

  // ── 권한 ──────────────────────────────
  policies: (params: Record<string, unknown> = {}) =>
    send<Paged<AccessPolicy>>(`/api/access-policies${qs(params)}`),

  revokePolicy: (id: number) => send<void>(`/api/access-policies/${id}`, { method: 'DELETE' }),

  auditLogs: (params: Record<string, unknown> = {}) =>
    send<Paged<AuditEntry>>(`/api/audit-logs${qs(params)}`),

  // ── 사용자 소속·역할 ──────────────────
  organizations: () => send<Organization[]>('/api/organizations'),

  /** B2B 온보딩의 최소 요건 — 조직명은 중복 불가, 시스템 관리자만 가능하다. */
  createOrganization: (name: string) =>
    send<Organization>('/api/organizations', { method: 'POST', body: JSON.stringify({ name }) }),

  teamsOf: (organizationId: number) =>
    send<Team[]>(`/api/organizations/${organizationId}/teams`),

  users: (params: Record<string, unknown> = {}) =>
    send<Paged<UserSummary>>(`/api/users${qs(params)}`),

  /** 임시 비밀번호는 응답에서 1회만 노출된다. */
  issueUserAccount: (payload: {
    email: string; name: string; organizationId: number; teamIds: number[]; roles: SystemRole[];
  }) => send<IssuedUserAccount>('/api/users', { method: 'POST', body: JSON.stringify(payload) }),

  updateUserMembership: (userId: number, organizationId: number, teamIds: number[]) =>
    send<UserDetail>(`/api/users/${userId}/membership`, {
      method: 'PUT',
      body: JSON.stringify({ organizationId, teamIds }),
    }),

  updateUserRoles: (userId: number, roles: SystemRole[]) =>
    send<UserDetail>(`/api/users/${userId}/roles`, {
      method: 'PUT',
      body: JSON.stringify({ roles }),
    }),

  // ── 계정 보안 ───────────────
  /** 성공하면 서버가 이 사용자의 토큰을 전부 무효화한다 — 호출한 본인도 다시 로그인해야 한다. */
  changePassword: (currentPassword: string, newPassword: string) =>
    send<void>('/api/auth/password', {
      method: 'POST',
      body: JSON.stringify({ currentPassword, newPassword }),
    }),

  updateUserStatus: (userId: number, status: 'ACTIVE' | 'DISABLED') =>
    send<UserDetail>(`/api/users/${userId}/status`, {
      method: 'PUT',
      body: JSON.stringify({ status }),
    }),

  /** 임시 비밀번호는 이 응답에서만 볼 수 있다. 다시 조회할 수 없다. */
  resetUserPassword: (userId: number) =>
    send<IssuedTemporaryPassword>(`/api/users/${userId}/password-reset`, { method: 'POST' }),

  // ── 팀별 거버넌스 알림 채널 ──
  teamNotificationChannel: (teamId: number) =>
    send<TeamNotificationChannel>(`/api/teams/${teamId}/notification-channel`),

  updateTeamNotificationChannel: (
    teamId: number,
    payload: { slackWebhookUrl: string | null; teamsWebhookUrl: string | null; notificationEmails: string[] },
  ) =>
    send<TeamNotificationChannel>(`/api/teams/${teamId}/notification-channel`, {
      method: 'PUT',
      body: JSON.stringify(payload),
    }),

  // ── 색인 ────────────────
  indexJobs: (params: Record<string, unknown> = {}) =>
    send<Paged<IndexJob>>(`/api/index-jobs${qs(params)}`),

  indexJobItems: (jobKey: string, status?: string) =>
    send<IndexJobItem[]>(`/api/index-jobs/${jobKey}/items${qs({ status })}`),

  /** 전체 재색인용. 일반 최신화는 문서 변경 이벤트가 자동으로 처리한다. */
  runBulkIndex: () =>
    send<IndexJob>('/api/index-jobs', { method: 'POST', body: JSON.stringify({}) }),

  /** 자동 재시도 3회를 모두 소진한 항목에만 쓴다. */
  retryIndexItem: (jobKey: string, itemId: number) =>
    send<void>(`/api/index-jobs/${jobKey}/items/${itemId}/retry`, { method: 'POST' }),

  // ── 대화 ──────────────────────────────
  conversations: (params: Record<string, unknown> = {}) =>
    send<Paged<Conversation>>(`/api/conversations${qs(params)}`),

  createConversation: () =>
    send<Conversation>('/api/conversations', { method: 'POST' }),

  deleteConversation: (id: number) =>
    send<void>(`/api/conversations/${id}`, { method: 'DELETE' }),

  messages: (conversationId: number) =>
    send<Message[]>(`/api/conversations/${conversationId}/messages`),

  messageSources: (messageId: number) =>
    send<AnswerSource[]>(`/api/messages/${messageId}/sources`),

  // ── 타임라인 ──────────────────────────
  timeline: (topic: string) => send<TimelineResponse>(`/api/timeline${qs({ topic })}`),

  putFeedback: (messageId: number, rating: string, reason?: string) =>
    send<void>(`/api/messages/${messageId}/feedback`, {
      method: 'PUT',
      body: JSON.stringify({ rating, reason: reason ?? null }),
    }),

  // ── 운영 현황 ─────────────────────────
  opsHealth: () => send<OpsHealth>('/api/admin/ops/health'),

  opsIndexStatus: () => send<OpsIndexStatus>('/api/admin/ops/index-status'),

  opsErrors: (params: Record<string, unknown> = {}) =>
    send<Paged<OpsError>>(`/api/admin/ops/errors${qs(params)}`),

  opsTrace: (correlationId: string) =>
    send<TraceSpan[]>(`/api/admin/ops/trace/${encodeURIComponent(correlationId)}`),

  // ── 스트림 DLQ ─────────────────
  opsDlqStatuses: () => send<DlqStatus[]>('/api/admin/ops/dlq'),

  opsDlqEntries: (streamKey: string, limit = 20) =>
    send<DlqEntry[]>(`/api/admin/ops/dlq/${encodeURIComponent(streamKey)}/entries${qs({ limit })}`),

  opsDlqReplay: (streamKey: string, ids: string[]) =>
    send<DlqActionResult>(`/api/admin/ops/dlq/${encodeURIComponent(streamKey)}/replay`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),

  opsDlqDiscard: (streamKey: string, ids: string[]) =>
    send<DlqActionResult>(`/api/admin/ops/dlq/${encodeURIComponent(streamKey)}/discard`, {
      method: 'POST',
      body: JSON.stringify({ ids }),
    }),

  // ── 제품 지표 ───────────
  productMetrics: (days = 30) => send<ProductMetrics>(`/api/admin/metrics${qs({ days })}`),

  metricsFeedback: (params: Record<string, unknown> = {}) =>
    send<Paged<FeedbackItem>>(`/api/admin/metrics/feedback${qs(params)}`),

  metricsFeedbackDetail: (messageId: number) =>
    send<FeedbackDetail>(`/api/admin/metrics/feedback/${messageId}`),

  // ── 일괄 가져오기 ──────────────────────
  createImport: (files: File[], defaultDocumentType: DocumentType, organizationId: number, teamId: number | null) => {
    const form = new FormData();
    files.forEach((f) => form.append('files', f));
    form.append('defaultDocumentType', defaultDocumentType);
    form.append('organizationId', String(organizationId));
    if (teamId !== null) form.append('teamId', String(teamId));
    return send<ImportJob>('/api/document-imports', { method: 'POST', body: form }, false);
  },

  getImport: (jobKey: string) => send<ImportJobDetail>(`/api/document-imports/${jobKey}`),

  retryImport: (jobKey: string) =>
    send<ImportJob>(`/api/document-imports/${jobKey}/retry`, { method: 'POST' }),

  // ── 정책 상충 거버넌스 ────────────────
  conflicts: (params: Record<string, unknown> = {}) =>
    send<Paged<ConflictItem>>(`/api/governance/conflicts${qs(params)}`),

  conflict: (id: number) =>
    send<ConflictItem>(`/api/governance/conflicts/${id}`),

  resolveConflict: (id: number, body: ResolveConflictRequest) =>
    send<ConflictItem>(`/api/governance/conflicts/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(body),
    }),
};

/** SSE 이벤트 하나. id 는 Redis Stream 엔트리 id 그대로다 — 재연결 시 Last-Event-ID 로 되돌려 보낸다. */
export interface StreamEvent {
  name: 'conversation' | 'sources' | 'delta' | 'done' | 'error' | 'graph';
  data: Record<string, unknown>;
  id?: string;
}

/** fetch 스트림 응답을 SSE 프레임으로 잘라 순차로 콜백한다. askStream/resumeStream 이 공유한다. */
async function readSseStream(response: Response, onEvent: (event: StreamEvent) => void): Promise<void> {
  if (!response.ok || !response.body) {
    let body: ApiError = { code: 'STREAM_FAILED', message: '답변을 받지 못했습니다.' };
    try { body = await response.json(); } catch { /* 본문이 없을 수 있다 */ }
    throw new HttpError(response.status, body);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE 프레임은 빈 줄로 끊긴다
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';

    for (const frame of frames) {
      let name = 'message';
      let id: string | undefined;
      const dataLines: string[] = [];
      for (const line of frame.split('\n')) {
        if (line.startsWith(':')) continue;           // 하트비트 주석
        if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('id:')) id = line.slice(3).trim();
        else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue;
      try {
        onEvent({ name: name as StreamEvent['name'], data: JSON.parse(dataLines.join('\n')), id });
      } catch {
        // 조각 하나를 못 읽어도 나머지 스트림은 이어간다
      }
    }
  }
}

/**
 * 질문을 보내고 답변 조각을 순차로 받는다.
 *
 * EventSource 는 POST 와 헤더를 지원하지 않아 fetch 의 스트림을 직접 읽는다.
 * 이벤트 순서는 conversation → sources → delta(반복) → done 이다.
 * conversation 이벤트의 jobKey 를 기억해 두면 연결이 끊겼을 때 resumeStream 으로 이어받을 수 있다.
 */
export async function askStream(
  conversationId: number,
  content: string,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}/api/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${tokenStore.access ?? ''}`,
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({ content }),
    signal,
  });
  await readSseStream(response, onEvent);
}

/**
 * 끊긴 스트림을 이어받는다 — 재연결이거나, 새로고침 후 재진입한 경우다.
 * lastEventId 를 비워두면(빈 문자열) 서버가 처음부터 다시 보내준다.
 */
export async function resumeStream(
  conversationId: number,
  jobKey: string,
  lastEventId: string,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const headers = new Headers({
    Authorization: `Bearer ${tokenStore.access ?? ''}`,
    Accept: 'text/event-stream',
  });
  if (lastEventId) headers.set('Last-Event-ID', lastEventId);

  const response = await fetch(
    `${API_BASE}/api/conversations/${conversationId}/jobs/${jobKey}/stream`,
    { headers, signal },
  );
  await readSseStream(response, onEvent);
}
