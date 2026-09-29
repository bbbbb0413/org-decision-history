import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { GovernanceDashboard } from './GovernanceDashboard';
import { api } from '../../api/client';
import type { ConflictItem, CurrentUser } from '../../api/types';

/**
 * "내 문서 관련만" 필터.
 *
 * 이건 서버 필터다 — `author_id` 가 상충 목록 DTO 에 없어 클라이언트에서 거를 수 없다.
 * 그래서 값이 바뀌면 다시 불러와야 한다는 것이 이 테스트의 요점이다.
 */
const USER: CurrentUser = {
  id: 2,
  email: 'admin@company.com',
  name: '김하늘',
  status: 'ACTIVE',
  organizationId: 1,
  organizationName: '플랫폼본부',
  teams: [],
  roles: ['SYSTEM_ADMIN'],
};

function conflict(overrides: Partial<ConflictItem> = {}): ConflictItem {
  return {
    id: 1,
    sourceDocId: 10,
    sourceDocTitle: '내가 쓴 재택근무 지침',
    targetDocId: 20,
    targetDocTitle: '전사 근무제도 개정안',
    sourceClause: '주 2회까지 재택근무를 신청할 수 있다',
    targetClause: '전원 사무실 출근을 의무화한다',
    conflictType: 'DIRECT_CONTRADICTION',
    severityScore: 0.91,
    reasoning: '재택 허용과 전원 출근 의무가 정면으로 어긋난다',
    affectedTeams: ['인증팀'],
    status: 'OPEN',
    resolvedBy: null,
    resolutionNote: null,
    resolvedAt: null,
    createdAt: '2026-09-27T00:00:00Z',
    ...overrides,
  };
}

function renderDashboard() {
  render(
    <GovernanceDashboard
      user={USER}
      focusConflictId={null}
      onFocusConsumed={() => {}}
      onOpenDocument={() => {}}
    />,
  );
}

describe('GovernanceDashboard 내 문서 필터', () => {
  afterEach(() => vi.restoreAllMocks());

  it('기본 조회에는 mine 을 보내지 않는다', async () => {
    const spy = vi.spyOn(api, 'conflicts').mockResolvedValue({
      content: [conflict()], page: 0, size: 100, totalElements: 1, totalPages: 1,
    });
    renderDashboard();

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][0]).toMatchObject({ mine: undefined });
  });

  it('체크하면 서버에 mine=true 로 다시 조회한다', async () => {
    const spy = vi.spyOn(api, 'conflicts').mockResolvedValue({
      content: [conflict()], page: 0, size: 100, totalElements: 1, totalPages: 1,
    });
    renderDashboard();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByLabelText('내 문서 관련만'));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy.mock.calls[1][0]).toMatchObject({ mine: true });
  });

  it('필터를 켠 상태에서 비면 필터 때문이라고 알려준다', async () => {
    vi.spyOn(api, 'conflicts').mockResolvedValue({
      content: [], page: 0, size: 100, totalElements: 0, totalPages: 0,
    });
    renderDashboard();
    await screen.findByText(/현재 감지된 정책 상충이 없습니다/);

    fireEvent.click(screen.getByLabelText('내 문서 관련만'));

    expect(await screen.findByText(/내 문서에 걸린 정책 상충이 없습니다/)).toBeInTheDocument();
  });
});
