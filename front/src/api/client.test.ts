import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, HttpError, tokenStore } from './client';

/** fetch 를 응답 큐로 대체한다. 각 호출의 요청 정보도 기록해 재발급 흐름을 따라갈 수 있게 한다. */
function mockFetch(responses: Array<{ status: number; body?: unknown }>) {
  const calls: Array<{ url: string; method: string; auth: string | null }> = [];
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    const next = responses.shift();
    if (!next) throw new Error(`예상하지 않은 추가 요청: ${url}`);
    calls.push({
      url,
      method: init.method ?? 'GET',
      auth: new Headers(init.headers).get('Authorization'),
    });
    return {
      ok: next.status >= 200 && next.status < 300,
      status: next.status,
      headers: new Headers(),
      json: async () => next.body ?? {},
    } as Response;
  });
  vi.stubGlobal('fetch', fetchMock);
  return calls;
}

const TOKENS = (suffix: string) => ({
  accessToken: `access-${suffix}`,
  refreshToken: `refresh-${suffix}`,
  expiresIn: 1800,
  user: { id: 1 },
});

describe('액세스 토큰 만료 시 재발급', () => {
  beforeEach(() => {
    tokenStore.save({ accessToken: 'access-old', refreshToken: 'refresh-old' });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('401 이면 재발급 토큰으로 갱신하고 원래 요청을 다시 보낸다', async () => {
    // 이 흐름이 없으면 액세스 토큰 수명(30분)이 지나는 순간 14일짜리 세션이 있어도 로그아웃된다
    const calls = mockFetch([
      { status: 401 },
      { status: 200, body: TOKENS('new') },
      { status: 200, body: { id: 7, email: 'a@b.com' } },
    ]);

    const me = await api.me();

    expect(me).toMatchObject({ id: 7 });
    expect(calls.map((c) => c.url)).toEqual([
      '/api/auth/me',
      '/api/auth/refresh',
      '/api/auth/me',
    ]);
    // 재발급에는 재발급 토큰을, 재시도에는 새 액세스 토큰을 쓴다
    expect(calls[1].auth).toBe('Bearer refresh-old');
    expect(calls[2].auth).toBe('Bearer access-new');
    expect(tokenStore.access).toBe('access-new');
  });

  it('503(인증 확인 불가)이면 재발급하지 않고 토큰을 그대로 둔다', async () => {
    // 게이트웨이가 키 조회·Redis 장애로 토큰을 확인하지 못한 경우다. 401 로 다루면 재발급도 같은
    // 이유로 실패해 토큰을 지우고, 장애가 짧아도 사용자가 로그아웃된다
    const calls = mockFetch([
      { status: 503, body: { code: 'AUTH_UNAVAILABLE', message: '지금은 인증을 확인할 수 없습니다.' } },
    ]);

    const error = await api.me().catch((e) => e);

    expect(error).toBeInstanceOf(HttpError);
    expect((error as HttpError).status).toBe(503);
    expect(calls.map((c) => c.url)).toEqual(['/api/auth/me']);
    expect(tokenStore.access).toBe('access-old');
    expect(tokenStore.refresh).toBe('refresh-old');
  });

  it('재발급도 실패하면 토큰을 지우고 401 을 그대로 올린다', async () => {
    // 로그아웃·비밀번호 변경으로 무효화된 경우다. 앱은 이 401 을 보고 로그인 화면으로 돌아간다
    mockFetch([
      { status: 401 },
      { status: 401, body: { code: 'TOKEN_INVALID', message: '토큰이 유효하지 않습니다.' } },
    ]);

    await expect(api.me()).rejects.toBeInstanceOf(HttpError);
    expect(tokenStore.access).toBeNull();
    expect(tokenStore.refresh).toBeNull();
  });

  it('동시에 401 을 받아도 재발급은 한 번만 돈다', async () => {
    // 재발급이 겹치면 나중 것이 앞의 토큰을 밀어내 서로를 무효화한다
    const calls = mockFetch([
      { status: 401 },
      { status: 401 },
      { status: 200, body: TOKENS('new') },
      { status: 200, body: { id: 1 } },
      { status: 200, body: { id: 2 } },
    ]);

    await Promise.all([api.me(), api.conflicts()]);

    expect(calls.filter((c) => c.url === '/api/auth/refresh')).toHaveLength(1);
  });

  it('재발급 토큰이 없으면 재발급을 시도하지 않는다', async () => {
    tokenStore.clear();
    tokenStore.save({ accessToken: 'access-only', refreshToken: '' });
    const calls = mockFetch([{ status: 401, body: { code: 'TOKEN_EXPIRED', message: '만료' } }]);

    await expect(api.me()).rejects.toBeInstanceOf(HttpError);
    expect(calls).toHaveLength(1);
  });

  it('로그인 실패는 재발급으로 이어지지 않는다', async () => {
    // 로그인 401 은 "비밀번호가 틀렸다"지 "토큰이 만료됐다"가 아니다
    const calls = mockFetch([
      { status: 401, body: { code: 'INVALID_CREDENTIALS', message: '이메일 또는 비밀번호가 올바르지 않습니다.' } },
    ]);

    await expect(api.login('a@b.com', 'wrong')).rejects.toBeInstanceOf(HttpError);
    expect(calls).toHaveLength(1);
  });
});

describe('오류 응답 처리', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('서버 오류 본문을 HttpError 에 담아 던진다', async () => {
    mockFetch([{ status: 403, body: { code: 'SCOPE_VIOLATION', message: '관리 범위 밖의 요청입니다.' } }]);

    await expect(api.me()).rejects.toMatchObject({
      status: 403,
      body: { code: 'SCOPE_VIOLATION', message: '관리 범위 밖의 요청입니다.' },
    });
  });

  it('204 응답은 본문 파싱을 시도하지 않는다', async () => {
    tokenStore.save({ accessToken: 'a', refreshToken: 'r' });
    mockFetch([{ status: 204 }]);

    await expect(api.logout()).resolves.toBeUndefined();
  });
});
