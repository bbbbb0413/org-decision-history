import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ProductMetricsPanel } from './ProductMetricsPanel';
import { api } from '../../api/client';
import type { FeedbackDetail, FeedbackItem, ProductMetrics } from '../../api/types';

function metrics(overrides: Partial<ProductMetrics> = {}): ProductMetrics {
  return {
    from: '2026-08-29T00:00:00Z',
    to: '2026-09-28T00:00:00Z',
    days: 30,
    answerQuality: {
      assistantMessages: 100, withSources: 90, ok: 92, okWithSources: 88,
      byStatus: [{ status: 'OK', count: 92 }, { status: 'NO_EVIDENCE', count: 8 }],
    },
    sources: { citedSources: 300, withOriginalFile: 291, withCoordinates: 120 },
    feedback: { responded: 50, helpful: 41, notHelpful: 9, assistantMessages: 100 },
    latency: { measured: 100, averageMs: 3200, p95Ms: 6100, overTargetCount: 12, targetMs: 5000 },
    denials: { total: 4, byAction: [{ action: 'DOCUMENT_VIEW', count: 4 }] },
    adoption: { activeUsers: 40, askedUsers: 26, questions: 210 },
    degraded: [],
    ...overrides,
  };
}

function feedbackItem(overrides: Partial<FeedbackItem> = {}): FeedbackItem {
  return {
    messageId: 77,
    conversationId: 12,
    rating: 'NOT_HELPFUL',
    reason: '근거가 질문과 무관했다',
    createdAt: '2026-09-27T01:00:00Z',
    raterName: '이도윤',
    question: '결제 승인 정책 알려줘',
    answerPreview: '근거를 찾지 못했다',
    answerStatus: 'NO_EVIDENCE',
    latencyMs: 900,
    sourceCount: 0,
    ...overrides,
  };
}

function detail(overrides: Partial<FeedbackDetail> = {}): FeedbackDetail {
  return {
    messageId: 77,
    conversationId: 12,
    rating: 'NOT_HELPFUL',
    reason: '근거가 질문과 무관했다',
    createdAt: '2026-09-27T01:00:00Z',
    raterName: '이도윤',
    question: '결제 승인 정책 알려줘',
    rewrittenQuery: '2026년 결제 승인 한도 정책은 무엇인가',
    answer: '근거를 찾지 못했다',
    answerStatus: 'NO_EVIDENCE',
    modelName: 'llama-3.3-70b',
    latencyMs: 900,
    answeredAt: '2026-09-27T00:59:00Z',
    sources: [],
    ...overrides,
  };
}

function stubApi(m: ProductMetrics, items: FeedbackItem[] = [feedbackItem()]) {
  vi.spyOn(api, 'productMetrics').mockResolvedValue(m);
  vi.spyOn(api, 'metricsFeedback').mockResolvedValue({
    content: items, page: 0, size: 10, totalElements: items.length, totalPages: items.length ? 1 : 0,
  });
}

describe('ProductMetricsPanel', () => {
  afterEach(() => vi.restoreAllMocks());

  it('비율과 분모를 함께 보여준다', async () => {
    stubApi(metrics());
    render(<ProductMetricsPanel />);

    // 88/100 = 88.0%, 목표 85% 달성
    expect(await screen.findByText('88.0%')).toBeInTheDocument();
    expect(screen.getByText('88 / 100 답변')).toBeInTheDocument();
  });

  it('목표 달성 여부를 표시한다', async () => {
    stubApi(metrics());
    render(<ProductMetricsPanel />);

    await screen.findByText('88.0%');
    // 성공률 88%(≥85) 달성 · 검증가능 97%(≥95) 달성 · 유용 82%(≥80) 달성 · 정착도 65%(≥60) 달성
    expect(screen.getAllByText('달성')).toHaveLength(4);
    expect(screen.queryByText('미달')).not.toBeInTheDocument();
  });

  it('목표에 못 미치면 미달로 표시한다', async () => {
    stubApi(metrics({
      feedback: { responded: 50, helpful: 20, notHelpful: 30, assistantMessages: 100 },
    }));
    render(<ProductMetricsPanel />);

    await screen.findByText('40.0%');
    expect(screen.getByText('미달')).toBeInTheDocument();
  });

  it('표본이 적으면 달성·미달을 판정하지 않는다', async () => {
    // 평가 3건 중 2건이 도움 → 66.7% 지만 판정하지 않아야 한다
    stubApi(metrics({
      feedback: { responded: 3, helpful: 2, notHelpful: 1, assistantMessages: 4 },
    }));
    render(<ProductMetricsPanel />);

    await screen.findByText('66.7%');
    expect(screen.getByText('표본 부족')).toBeInTheDocument();
  });

  it('분모가 0이면 비율 대신 —를 보여준다', async () => {
    stubApi(metrics({
      feedback: { responded: 0, helpful: 0, notHelpful: 0, assistantMessages: 0 },
    }), []);
    render(<ProductMetricsPanel />);

    await screen.findByText('88.0%');
    expect(screen.getByText('—')).toBeInTheDocument();
  });

  it('읽지 못한 구획은 경고하고 수치를 신뢰하지 말라고 알린다', async () => {
    stubApi(metrics({ degraded: ['sources'] }));
    render(<ProductMetricsPanel />);

    expect(await screen.findByText(/일부 구획을 읽지 못해/)).toBeInTheDocument();
    expect(screen.getByText('읽기 실패')).toBeInTheDocument();
  });

  it('권한 차단이 노출 건수가 아니라는 것을 화면에 밝힌다', async () => {
    stubApi(metrics());
    render(<ProductMetricsPanel />);

    expect(await screen.findByText(/노출 건수가 아니다/)).toBeInTheDocument();
  });

  it('평가 목록에서 상세를 열면 질문·답변·재작성 질문을 보여준다', async () => {
    stubApi(metrics());
    const detailSpy = vi.spyOn(api, 'metricsFeedbackDetail').mockResolvedValue(detail());
    render(<ProductMetricsPanel />);

    fireEvent.click(await screen.findByRole('button', { name: '열기' }));

    await waitFor(() => expect(detailSpy).toHaveBeenCalledWith(77));
    expect(await screen.findByText('평가 상세 · 메시지 #77')).toBeInTheDocument();
    expect(screen.getByText('2026년 결제 승인 한도 정책은 무엇인가')).toBeInTheDocument();
    expect(screen.getByText(/인용한 출처가 없다/)).toBeInTheDocument();
  });

  it('기간을 바꾸면 그 기간으로 다시 조회한다', async () => {
    stubApi(metrics());
    render(<ProductMetricsPanel />);
    await screen.findByText('88.0%');

    fireEvent.click(screen.getByRole('button', { name: '최근 7일' }));

    await waitFor(() => expect(api.productMetrics).toHaveBeenCalledWith(7));
  });

  it('기본 필터는 부정 평가다 — 고칠 것을 먼저 보여준다', async () => {
    stubApi(metrics());
    render(<ProductMetricsPanel />);

    await waitFor(() => expect(api.metricsFeedback).toHaveBeenCalledWith(
      expect.objectContaining({ rating: 'NOT_HELPFUL' }),
    ));
  });
});
