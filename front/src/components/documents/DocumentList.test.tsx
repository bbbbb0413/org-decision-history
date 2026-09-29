import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DocumentList } from './DocumentList';
import { api } from '../../api/client';
import type { DocumentPage } from '../../api/types';

/** "내 문서만" 필터. 프론트는 작성자 id 를 보내지 않고 mine 플래그만 보낸다. */
function page(overrides: Partial<DocumentPage> = {}): DocumentPage {
  return {
    content: [],
    page: 0,
    size: 20,
    totalElements: 0,
    totalPages: 0,
    typeCounts: {},
    ...overrides,
  };
}

function renderList() {
  render(
    <DocumentList onOpen={() => {}} onCreate={() => {}} onImport={() => {}} reloadKey={0} />,
  );
}

describe('DocumentList 내 문서 필터', () => {
  afterEach(() => vi.restoreAllMocks());

  it('기본 조회에는 mine 을 보내지 않는다', async () => {
    const spy = vi.spyOn(api, 'documents').mockResolvedValue(page());
    renderList();

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][0]).toMatchObject({ mine: undefined });
  });

  it('체크하면 mine=true 로 다시 조회한다', async () => {
    const spy = vi.spyOn(api, 'documents').mockResolvedValue(page());
    renderList();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByLabelText('내가 쓴 문서만'));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy.mock.calls[1][0]).toMatchObject({ mine: true });
  });

  it('작성자 id 를 직접 보내지 않는다 — 서버가 요청자 id 로 바꾼다', async () => {
    const spy = vi.spyOn(api, 'documents').mockResolvedValue(page());
    renderList();
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByLabelText('내가 쓴 문서만'));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));

    expect(spy.mock.calls[1][0]).not.toHaveProperty('authorId');
  });

  it('필터를 켠 상태에서 결과가 비면 필터 때문이라고 알려준다', async () => {
    vi.spyOn(api, 'documents').mockResolvedValue(page());
    renderList();
    await screen.findByText('권한 범위 안에 문서가 없습니다.');

    fireEvent.click(screen.getByLabelText('내가 쓴 문서만'));

    expect(await screen.findByText('내가 쓴 문서가 없습니다.')).toBeInTheDocument();
    expect(screen.getByText(/필터를 해제하면/)).toBeInTheDocument();
  });
});
