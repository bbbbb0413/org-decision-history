import { useState } from 'react';

const RATINGS = [1, 2, 3, 4, 5] as const;
const MAX_COMMENT_LENGTH = 1000;

const ACCURACY_LABELS: Record<number, string> = {
  1: '전혀 정확하지 않다',
  2: '정확하지 않다',
  3: '보통',
  4: '정확하다',
  5: '매우 정확하다',
};

const HELPFULNESS_LABELS: Record<number, string> = {
  1: '전혀 도움되지 않았다',
  2: '도움되지 않았다',
  3: '보통',
  4: '도움이 됐다',
  5: '매우 도움이 됐다',
};

export interface FeedbackData {
  accuracy: number;
  helpfulness: number;
  comment?: string;
}

interface Props {
  messageId: number;
  onSubmit?: (data: FeedbackData) => Promise<void> | void;
}

export function AnswerFeedback({ messageId, onSubmit }: Props) {
  const storageKey = `answer.feedback.${messageId}`;
  const initialData = (): FeedbackData | null => {
    try {
      const stored = localStorage.getItem(storageKey);
      return stored ? JSON.parse(stored) : null;
    } catch {
      return null;
    }
  };

  const [existing, setExisting] = useState<FeedbackData | null>(initialData);
  const [isOpen, setIsOpen] = useState(false);
  const [accuracy, setAccuracy] = useState<number | null>(existing?.accuracy ?? null);
  const [helpfulness, setHelpfulness] = useState<number | null>(existing?.helpfulness ?? null);
  const [comment, setComment] = useState(existing?.comment ?? '');
  const [saving, setSaving] = useState(false);

  const canSubmit = accuracy !== null && helpfulness !== null && !saving;

  const handleSubmit = async () => {
    if (accuracy === null || helpfulness === null || saving) return;
    setSaving(true);
    const data: FeedbackData = {
      accuracy,
      helpfulness,
      comment: comment.trim() || undefined,
    };

    try {
      if (onSubmit) {
        await onSubmit(data);
      }
      localStorage.setItem(storageKey, JSON.stringify(data));
      setExisting(data);
      setIsOpen(false);
    } catch {
      // 오류 시 로컬에는 보존
      localStorage.setItem(storageKey, JSON.stringify(data));
      setExisting(data);
      setIsOpen(false);
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) {
    return (
      <div style={{ marginTop: 8 }}>
        {existing ? (
          <button
            type="button"
            onClick={() => setIsOpen(true)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 11,
              color: 'var(--ink-3)',
              background: 'none',
              border: '1px solid var(--rule)',
              borderRadius: 12,
              padding: '2px 8px',
              cursor: 'pointer',
            }}
          >
            <span>★ 내 평가 · 정확도 {existing.accuracy}/5 · 유용성 {existing.helpfulness}/5</span>
            <span style={{ color: 'var(--accent)', textDecoration: 'underline' }}>수정</span>
          </button>
        ) : (
          <button
            type="button"
            onClick={() => setIsOpen(true)}
            style={{
              fontSize: 11,
              color: 'var(--ink-3)',
              background: 'none',
              border: '1px dashed var(--rule)',
              borderRadius: 12,
              padding: '2px 8px',
              cursor: 'pointer',
            }}
          >
            ☆ 이 답변 평가하기
          </button>
        )}
      </div>
    );
  }

  return (
    <div
      style={{
        marginTop: 10,
        padding: 12,
        background: 'var(--tint)',
        border: '1px solid var(--rule)',
        borderRadius: 8,
        fontSize: 12,
        maxWidth: 380,
      }}
    >
      <div style={{ marginBottom: 8 }}>
        <span style={{ fontWeight: 600, marginRight: 8 }}>정확도:</span>
        {RATINGS.map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => setAccuracy(r)}
            title={ACCURACY_LABELS[r]}
            style={{
              marginRight: 4,
              padding: '2px 7px',
              borderRadius: 4,
              border: '1px solid var(--rule)',
              background: accuracy === r ? 'var(--accent)' : 'var(--paper)',
              color: accuracy === r ? '#fff' : 'var(--ink)',
              cursor: 'pointer',
              fontWeight: accuracy === r ? 700 : 400,
            }}
          >
            {r}
          </button>
        ))}
        {accuracy && <span style={{ color: 'var(--ink-3)', marginLeft: 6 }}>{ACCURACY_LABELS[accuracy]}</span>}
      </div>

      <div style={{ marginBottom: 8 }}>
        <span style={{ fontWeight: 600, marginRight: 8 }}>유용성:</span>
        {RATINGS.map((r) => (
          <button
            key={r}
            type="button"
            onClick={() => setHelpfulness(r)}
            title={HELPFULNESS_LABELS[r]}
            style={{
              marginRight: 4,
              padding: '2px 7px',
              borderRadius: 4,
              border: '1px solid var(--rule)',
              background: helpfulness === r ? 'var(--accent)' : 'var(--paper)',
              color: helpfulness === r ? '#fff' : 'var(--ink)',
              cursor: 'pointer',
              fontWeight: helpfulness === r ? 700 : 400,
            }}
          >
            {r}
          </button>
        ))}
        {helpfulness && <span style={{ color: 'var(--ink-3)', marginLeft: 6 }}>{HELPFULNESS_LABELS[helpfulness]}</span>}
      </div>

      <div style={{ marginBottom: 8 }}>
        <textarea
          value={comment}
          maxLength={MAX_COMMENT_LENGTH}
          rows={2}
          placeholder="개선할 점이나 피드백을 남겨주세요 (선택)"
          onChange={(e) => setComment(e.target.value)}
          style={{
            width: '100%',
            boxSizing: 'border-box',
            padding: 6,
            borderRadius: 4,
            border: '1px solid var(--rule)',
            fontSize: 11.5,
            resize: 'vertical',
            fontFamily: 'inherit',
          }}
        />
      </div>

      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
        <button
          type="button"
          onClick={() => setIsOpen(false)}
          disabled={saving}
          style={{
            fontSize: 11,
            padding: '4px 8px',
            borderRadius: 4,
            border: '1px solid var(--rule)',
            background: 'var(--paper)',
            cursor: 'pointer',
          }}
        >
          취소
        </button>
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!canSubmit}
          style={{
            fontSize: 11,
            padding: '4px 10px',
            borderRadius: 4,
            border: 'none',
            background: canSubmit ? 'var(--accent)' : 'var(--rule)',
            color: '#fff',
            cursor: canSubmit ? 'pointer' : 'not-allowed',
            fontWeight: 600,
          }}
        >
          {saving ? '저장 중…' : existing ? '평가 수정' : '평가 제출'}
        </button>
      </div>
    </div>
  );
}

export default AnswerFeedback;
