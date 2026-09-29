import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { OpsPanel } from './OpsPanel';
import { api } from '../../api/client';
import type { DlqEntry, DlqStatus } from '../../api/types';

/** DLQ 외 섹션은 이 테스트의 관심사가 아니라 빈 값으로 고정한다. */
function stubOtherSections() {
  vi.spyOn(api, 'opsHealth').mockResolvedValue({ storages: [], services: [], checkedAt: '2026-09-22T00:00:00Z' });
  vi.spyOn(api, 'opsIndexStatus').mockResolvedValue({ pendingCount: 0, runningCount: 0, failCount: 0, indexLagSeconds: 0 });
  vi.spyOn(api, 'opsErrors').mockResolvedValue({ content: [], page: 0, size: 20, totalElements: 0, totalPages: 0 });
}

function status(overrides: Partial<DlqStatus> = {}): DlqStatus {
  return {
    key: 'index-events',
    stream: 'stream:index-events',
    dlqStream: 'stream:index-events:dlq',
    consumerService: 'governance-worker',
    count: 2,
    oldestAt: '2026-09-22T00:00:00Z',
    ...overrides,
  };
}

function entry(id: string, overrides: Partial<DlqEntry> = {}): DlqEntry {
  return {
    id,
    streamKey: 'index-events',
    sourceStream: 'stream:index-events',
    sourceId: '1726900000000-0',
    consumerGroup: 'governance-worker',
    deliveries: 6,
    reason: '전달 6회로 한도 5회를 넘었다',
    addedAt: '2026-09-22T00:00:00Z',
    payload: { eventType: 'DOCUMENT_INDEXED', documentId: '11' },
    ...overrides,
  };
}

const EMPTY_STATUSES: DlqStatus[] = [
  status({ key: 'document-events', stream: 'stream:document-events', dlqStream: 'stream:document-events:dlq', consumerService: 'index-worker', count: 0, oldestAt: null }),
  status({ count: 0, oldestAt: null }),
];

describe('OpsPanel DLQ', () => {
  beforeEach(stubOtherSections);
  afterEach(() => vi.restoreAllMocks());

  it('건수가 0이면 빈 안내를 보여주고 항목 보기를 막는다', async () => {
    vi.spyOn(api, 'opsDlqStatuses').mockResolvedValue(EMPTY_STATUSES);
    render(<OpsPanel />);

    expect(await screen.findByText('유실된 이벤트가 없습니다.')).toBeInTheDocument();
    screen.getAllByRole('button', { name: '항목 보기' })
      .forEach((b) => expect(b).toBeDisabled());
  });

  it('스트림별 건수와 소비자 서비스를 보여준다', async () => {
    vi.spyOn(api, 'opsDlqStatuses').mockResolvedValue([status()]);
    render(<OpsPanel />);

    expect(await screen.findByText('stream:index-events')).toBeInTheDocument();
    expect(screen.getByText('2건')).toBeInTheDocument();
    expect(screen.getByText(/소비자 governance-worker/)).toBeInTheDocument();
  });

  it('항목 보기를 누르면 그 스트림의 항목만 불러온다', async () => {
    vi.spyOn(api, 'opsDlqStatuses').mockResolvedValue([status()]);
    const entries = vi.spyOn(api, 'opsDlqEntries').mockResolvedValue([entry('1-0')]);
    render(<OpsPanel />);

    fireEvent.click(await screen.findByRole('button', { name: '항목 보기' }));

    await waitFor(() => expect(entries).toHaveBeenCalledWith('index-events', 50));
    expect(await screen.findByText('전달 6회로 한도 5회를 넘었다')).toBeInTheDocument();
    // dlq.* 메타를 뺀 본문만 보여준다
    expect(screen.getByText('eventType=DOCUMENT_INDEXED documentId=11')).toBeInTheDocument();
  });

  it('선택한 항목만 재처리로 보내고, 실패한 것은 선택으로 남긴다', async () => {
    vi.spyOn(api, 'opsDlqStatuses').mockResolvedValue([status()]);
    vi.spyOn(api, 'opsDlqEntries').mockResolvedValue([entry('1-0'), entry('2-0')]);
    const replay = vi.spyOn(api, 'opsDlqReplay').mockResolvedValue({ succeeded: 1, failedIds: ['2-0'] });
    render(<OpsPanel />);

    fireEvent.click(await screen.findByRole('button', { name: '항목 보기' }));
    fireEvent.click(await screen.findByLabelText('1-0 선택'));
    fireEvent.click(screen.getByLabelText('2-0 선택'));
    fireEvent.click(screen.getByRole('button', { name: '선택 재처리' }));

    await waitFor(() => expect(replay).toHaveBeenCalledWith('index-events', ['1-0', '2-0']));
    expect(await screen.findByText(/1건 재처리, 1건 실패\(2-0\)/)).toBeInTheDocument();
    // 실패분만 선택으로 남아 바로 다시 시도할 수 있다
    await waitFor(() => expect(screen.getByLabelText('2-0 선택')).toBeChecked());
    expect(screen.getByLabelText('1-0 선택')).not.toBeChecked();
  });

  it('폐기는 확인을 거치고, 취소하면 아무 요청도 보내지 않는다', async () => {
    vi.spyOn(api, 'opsDlqStatuses').mockResolvedValue([status()]);
    vi.spyOn(api, 'opsDlqEntries').mockResolvedValue([entry('1-0')]);
    const discard = vi.spyOn(api, 'opsDlqDiscard').mockResolvedValue({ succeeded: 1, failedIds: [] });
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<OpsPanel />);

    fireEvent.click(await screen.findByRole('button', { name: '항목 보기' }));
    fireEvent.click(await screen.findByLabelText('1-0 선택'));
    fireEvent.click(screen.getByRole('button', { name: '선택 폐기' }));

    expect(confirm).toHaveBeenCalled();
    expect(discard).not.toHaveBeenCalled();
  });
});
