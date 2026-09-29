import { beforeEach, describe, expect, it } from 'vitest';
import { parsePathEntry, readNavState, replaceNav } from './nav';

describe('parsePathEntry', () => {
  it('알림이 보내는 문서 링크를 문서 상세 상태로 바꾼다', () => {
    // governance-worker notifications/models.py 의 source_doc_url 형식
    expect(parsePathEntry('/documents/26')).toMatchObject({
      tab: 'documents',
      docView: { name: 'detail', id: 26 },
    });
  });

  it('알림이 보내는 상충 링크를 거버넌스 탭 + 상충 id 로 바꾼다', () => {
    // conflict_url 형식. 이게 동작하지 않으면 알림의 조치 버튼이 기본 화면으로 떨어진다
    expect(parsePathEntry('/governance/conflicts/10')).toMatchObject({
      tab: 'governance',
      conflictId: 10,
    });
  });

  it('후행 슬래시를 허용한다', () => {
    expect(parsePathEntry('/documents/26/')?.docView).toEqual({ name: 'detail', id: 26 });
    expect(parsePathEntry('/governance/conflicts/10/')?.conflictId).toBe(10);
  });

  it.each([
    '/',
    '/documents',
    '/documents/abc',
    '/documents/26/extra',
    '/governance',
    '/governance/conflicts',
    '/mydocuments/26',
    '/x/documents/26',
  ])('해당 없는 경로 %s 는 null 이라 기존 쿼리스트링 처리로 넘어간다', (path) => {
    expect(parsePathEntry(path)).toBeNull();
  });
});

describe('진입 후 URL 정규화', () => {
  beforeEach(() => {
    window.history.replaceState({}, '', '/');
  });

  it('경로로 들어와도 정식 쿼리스트링 URL 로 되돌린다', () => {
    window.history.replaceState({}, '', '/governance/conflicts/10');

    const entry = readNavState();
    expect(entry.conflictId).toBe(10);

    // HomePage 가 첫 렌더에서 하는 일. 현재 pathname 을 그대로 붙이면
    // "/governance/conflicts/10?tab=governance" 같은 엉킨 URL 이 된다
    replaceNav({ tab: entry.tab, docView: { name: 'list' }, convId: null, conflictId: null });

    expect(window.location.pathname + window.location.search).toBe('/?tab=governance');
  });

  it('문서 링크도 같은 방식으로 정규화된다', () => {
    window.history.replaceState({}, '', '/documents/26');

    const entry = readNavState();
    replaceNav({ tab: entry.tab, docView: entry.docView, convId: null, conflictId: null });

    expect(window.location.pathname + window.location.search).toBe('/?tab=documents&view=detail&id=26');
  });

  it('기존 쿼리스트링 진입은 그대로 동작한다', () => {
    window.history.replaceState({}, '', '/?tab=chat&conv=7');

    expect(readNavState()).toMatchObject({ tab: 'chat', convId: 7, conflictId: null });
  });
});
