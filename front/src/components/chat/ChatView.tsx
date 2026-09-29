import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { api, askStream, resumeStream, HttpError, type StreamEvent } from '../../api/client';
import {
  ANSWER_STATUS_LABEL, type AnswerSource, type AnswerStatus,
  type Conversation, type GraphData, type Message,
} from '../../api/types';
import { Badge } from '../ui/Badge';
import { Button } from '../ui/Button';
import { AnswerFeedback } from './AnswerFeedback';
import { DecisionGraphViewLazy } from './lazy';
import { SourcePanel } from './SourcePanel';

interface Props {
  /** 선택된 대화 id. 브라우저 뒤로가기로 복원될 수 있으므로 부모(HomePage)가 URL 과 함께 관리한다. */
  currentId: number | null;
  onSelect: (id: number | null) => void;
  onOpenDocument: (documentId: number) => void;
}

const STATUS_TONE: Record<AnswerStatus, 'ok' | 'neutral' | 'warn' | 'err'> = {
  OK: 'ok', NO_EVIDENCE: 'neutral', LOW_CONFIDENCE: 'warn', DEGRADED: 'warn', RATE_LIMITED: 'err',
};

/** 끊긴 스트림을 이어받을 때의 재시도 간격. */
const RESUME_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000];

function jobStorageKey(conversationId: number) {
  return `history.job.${conversationId}`;
}
function saveJob(conversationId: number, jobKey: string) {
  try { localStorage.setItem(jobStorageKey(conversationId), jobKey); } catch { /* 무시 */ }
}
function clearJob(conversationId: number) {
  try { localStorage.removeItem(jobStorageKey(conversationId)); } catch { /* 무시 */ }
}
function loadJob(conversationId: number): string | null {
  try { return localStorage.getItem(jobStorageKey(conversationId)); } catch { return null; }
}

/** 대화 목록 + 대화. */
export function ChatView({ currentId, onSelect, onOpenDocument }: Props) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [sources, setSources] = useState<AnswerSource[]>([]);
  const [lastMessageId, setLastMessageId] = useState<number | null>(null);
  const [graphData, setGraphData] = useState<GraphData | null>(null);
  const [showGraph, setShowGraph] = useState(true);
  const [draft, setDraft] = useState('');
  const [streaming, setStreaming] = useState('');
  const [status, setStatus] = useState<AnswerStatus | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [resumeAt, setResumeAt] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** 오류로 끝난 질문. 다시 타이핑하지 않고 "다시 생성" 버튼으로 재시도할 수 있게 기억해 둔다. */
  const [failedQuestion, setFailedQuestion] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  // 재연결(resume)에 쓰는 값들 — 렌더와 무관하게 스트림 도중 계속 갱신되므로 ref 로 둔다.
  const jobKeyRef = useRef<string | null>(null);
  const lastEventIdRef = useRef<string>('');
  const terminalRef = useRef(false);

  const loadList = useCallback(async () => {
    try {
      setConversations((await api.conversations({ size: 30 })).content);
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '대화 목록을 불러오지 못했습니다.');
    }
  }, []);

  useEffect(() => { void loadList(); }, [loadList]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streaming]);

  /**
   * conversation/sources/delta/done/error 이벤트를 화면 상태로 반영한다. send()/open() 이 공유한다.
   * question 은 send() 에서만 넘어온다 — 오류로 끝났을 때 "다시 생성" 버튼에 쓸 원문을 기억하려는
   * 용도라, 원래 질문을 모르는 재진입 이어받기(open())에서는 생략해도 된다.
   */
  function makeEventHandler(conversationId: number, question?: string) {
    return (event: StreamEvent) => {
      if (event.id) lastEventIdRef.current = event.id;
      if (event.name === 'conversation') {
        const jobKey = String(event.data.jobKey ?? '');
        jobKeyRef.current = jobKey;
        saveJob(conversationId, jobKey);
      } else if (event.name === 'sources') {
        setSources((event.data.sources as AnswerSource[]) ?? []);
      } else if (event.name === 'graph') {
        setGraphData(event.data as unknown as GraphData);
      } else if (event.name === 'delta') {
        setStreaming((prev) => prev + String(event.data.text ?? ''));
      } else if (event.name === 'done') {
        terminalRef.current = true;
        setStatus(event.data.answerStatus as AnswerStatus);
        setSuggestions((event.data.suggestions as string[]) ?? []);
        setResumeAt(event.data.resumeAt ? String(event.data.resumeAt) : null);
        setLastMessageId(Number(event.data.messageId));
        clearJob(conversationId);
      } else if (event.name === 'error') {
        terminalRef.current = true;
        setError(String(event.data.message ?? '답변을 받지 못했습니다.'));
        if (question) setFailedQuestion(question);
        clearJob(conversationId);
      }
    };
  }

  /**
   * 스트림이 done/error 없이 끊기면, 남겨둔 jobKey 로 이어받기를 지수 백오프로 재시도한다.
   * jobKey 조차 못 받았다면(연결 시작 자체가 실패) 재시도할 대상이 없으므로 원래 오류를 그대로 던진다.
   */
  async function resumeUntilDone(conversationId: number, onEvent: (e: StreamEvent) => void, initialError: unknown) {
    if (terminalRef.current) return;
    if (!jobKeyRef.current) throw initialError;

    for (const delay of RESUME_BACKOFF_MS) {
      await new Promise((resolve) => setTimeout(resolve, delay));
      try {
        await resumeStream(conversationId, jobKeyRef.current, lastEventIdRef.current, onEvent);
        if (terminalRef.current) return;
      } catch {
        // 다음 시도로 넘어간다
      }
    }
    throw initialError;
  }

  /** 선택된 대화를 불러온다. currentId prop 이 바뀔 때(클릭, 새 대화, 뒤로가기 복원) 아래 effect 가 호출한다. */
  async function open(id: number) {
    setStreaming('');
    setStatus(null);
    setSuggestions([]);
    setResumeAt(null);
    setSources([]);
    setLastMessageId(null);
    setError(null);
    setFailedQuestion(null);
    setMessages(await api.messages(id));

    // 새로고침 등으로 재진입했는데 그 사이 끝나지 않은 job 이 남아있으면 처음부터 이어받는다.
    const pendingJobKey = loadJob(id);
    if (!pendingJobKey) return;

    jobKeyRef.current = pendingJobKey;
    lastEventIdRef.current = '';
    terminalRef.current = false;
    setBusy(true);
    try {
      await resumeStream(id, pendingJobKey, '', makeEventHandler(id));
    } catch {
      // 이어받기 실패. job 기록은 남겨둔다 — 다음에 열 때 다시 시도한다.
    } finally {
      setBusy(false);
      if (terminalRef.current) {
        setMessages(await api.messages(id));
        await loadList();
      }
    }
  }

  // currentId 가 바뀔 때마다(클릭 선택, 새 대화, 뒤로가기 복원) 해당 대화를 불러온다.
  useEffect(() => {
    if (currentId != null) {
      void open(currentId);
    } else {
      setMessages([]);
      setStreaming('');
      setStatus(null);
      setSuggestions([]);
      setResumeAt(null);
      setSources([]);
      setLastMessageId(null);
      setError(null);
      setFailedQuestion(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentId]);

  async function startNew() {
    const created = await api.createConversation();
    await loadList();
    onSelect(created.id);
  }

  async function remove(id: number) {
    if (!window.confirm('이 대화를 삭제할까요? 메시지도 함께 지워집니다.')) return;
    await api.deleteConversation(id);
    if (currentId === id) onSelect(null);
    await loadList();
  }

  /** 지난 답변의 근거 탭을 연다. 실시간 스트리밍이 끝나면 sources 가 비워지므로, 다시 열 방법이 필요하다. */
  async function viewSources(messageId: number) {
    if (lastMessageId === messageId) {
      // 이미 열려 있으면 다시 눌러서 닫는다
      setSources([]);
      setLastMessageId(null);
      setGraphData(null);
      return;
    }
    try {
      const srcList = await api.messageSources(messageId);
      setSources(srcList);
      setLastMessageId(messageId);
      if (srcList.length > 0) {
        try {
          const docIds = Array.from(new Set(srcList.map((s) => s.documentId)));
          const g = await api.documentsGraph(docIds);
          setGraphData(g);
        } catch {
          // 그래프 조회 실패 시 무시
        }
      }
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '근거를 불러오지 못했습니다.');
    }
  }

  /** questionOverride 가 있으면 "다시 생성" 재시도다 — 입력창(draft)은 건드리지 않는다. */
  async function send(questionOverride?: string) {
    const question = questionOverride ?? draft.trim();
    if (!currentId || !question || busy) return;
    const conversationId = currentId;
    if (!questionOverride) setDraft('');
    setBusy(true);
    setError(null);
    setFailedQuestion(null);
    setStreaming('');
    setStatus(null);
    setSuggestions([]);
    setResumeAt(null);
    setSources([]);
    setGraphData(null);
    setShowGraph(true);
    setLastMessageId(null);
    // 보낸 질문을 즉시 보여준다. 서버 응답을 기다리면 입력이 사라진 것처럼 보인다.
    setMessages((prev) => [...prev, {
      id: -Date.now(), conversationId, role: 'USER', content: question,
      modelName: null, answerStatus: null, latencyMs: null, sourceCount: 0,
      createdAt: new Date().toISOString(),
    }]);

    jobKeyRef.current = null;
    lastEventIdRef.current = '';
    terminalRef.current = false;
    const onEvent = makeEventHandler(conversationId, question);

    try {
      try {
        await askStream(conversationId, question, onEvent);
      } catch (e) {
        // 연결이 끊겼을 뿐일 수 있다 — jobKey 를 받았다면 이어받기를 시도한다.
        await resumeUntilDone(conversationId, onEvent, e);
      }
      // open() 을 다시 부르면 방금 done 이벤트로 받은 sources/lastMessageId 까지
      // 초기화돼 근거 패널이 나오자마자 사라진다. 메시지 목록만 새로 가져온다.
      setStreaming('');
      setMessages(await api.messages(conversationId));
      await loadList();
    } catch (e) {
      setError(e instanceof HttpError ? e.body.message : '답변을 받지 못했습니다.');
      setFailedQuestion(question);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={styles.wrap}>
      <aside style={styles.side}>
        <Button variant="primary" onClick={startNew} style={{ width: '100%', marginBottom: 12 }}>새 대화</Button>
        {conversations.map((c) => (
          <div key={c.id} onClick={() => onSelect(c.id)}
               style={{ ...styles.convItem, ...(currentId === c.id ? styles.convActive : {}) }}>
            <div style={styles.convTitle}>{c.title}</div>
            <div style={styles.convMeta}>
              {new Date(c.updatedAt).toLocaleString('ko-KR', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })}
              <button onClick={(e) => { e.stopPropagation(); void remove(c.id); }} style={styles.del}>⋯</button>
            </div>
          </div>
        ))}
        {conversations.length === 0 && <div style={styles.sideEmpty}>대화가 없습니다.</div>}
      </aside>

      <section style={styles.main}>
        {!currentId ? (
          <div style={styles.blank}>
            <div style={styles.blankTitle}>무엇을 확인하시겠습니까</div>
            <div style={styles.blankSub}>권한 범위 안의 문서에서 근거를 찾아 답합니다.</div>
            <Button variant="primary" onClick={startNew} style={{ marginTop: 16 }}>새 대화 시작</Button>
          </div>
        ) : (
          <>
            <div style={styles.stream}>
              {messages.map((m) => (
                <div key={m.id} style={m.role === 'USER' ? styles.userTurn : styles.botTurn}>
                  {m.role === 'ASSISTANT' && m.answerStatus && (
                    <div style={{ marginBottom: 6, display: 'flex', alignItems: 'center', gap: 6 }}>
                      <Badge tone={STATUS_TONE[m.answerStatus]}>{ANSWER_STATUS_LABEL[m.answerStatus]}</Badge>
                      {m.modelName && <span style={styles.model}>{m.modelName}</span>}
                      {m.latencyMs != null && <span style={styles.model}>{m.latencyMs}ms</span>}
                      {m.sourceCount > 0 && (
                        <Button size="sm" onClick={() => void viewSources(m.id)}>
                          {lastMessageId === m.id ? '근거 닫기' : `근거 보기 (${m.sourceCount})`}
                        </Button>
                      )}
                    </div>
                  )}
                  <div style={styles.text}>{m.content}</div>
                  {m.role === 'ASSISTANT' && lastMessageId === m.id && graphData && graphData.nodes.length > 0 && (
                    <div style={{ marginTop: 12, borderTop: '1px dashed #cbd5e1', paddingTop: 10 }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                        <span style={{ fontSize: 12, fontWeight: 700, color: '#334155' }}>
                          📊 의사결정 인과 관계망 (DAG) · {graphData.nodes.length}개 노드, {graphData.edges.length}개 관계
                        </span>
                        <Button size="sm" onClick={() => setShowGraph((v) => !v)}>
                          {showGraph ? '접기 ▲' : '펼치기 ▼'}
                        </Button>
                      </div>
                      {showGraph && (
                        <Suspense fallback={<div style={styles.lazyFallback}>관계망을 불러오는 중…</div>}>
                          <DecisionGraphViewLazy
                            graphData={graphData}
                            onOpenDocument={onOpenDocument}
                            height={340}
                          />
                        </Suspense>
                      )}
                    </div>
                  )}
                  {m.role === 'ASSISTANT' && (
                    <AnswerFeedback messageId={m.id} />
                  )}
                </div>
              ))}

              {streaming && (
                <div style={styles.botTurn}>
                  <div style={styles.text}>{streaming}</div>
                  {graphData && graphData.nodes.length > 0 && (
                    <div style={{ marginTop: 12, borderTop: '1px dashed #cbd5e1', paddingTop: 10 }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
                        <span style={{ fontSize: 12, fontWeight: 700, color: '#334155' }}>
                          📊 실시간 의사결정 인과 관계망 (DAG)
                        </span>
                        <Button size="sm" onClick={() => setShowGraph((v) => !v)}>
                          {showGraph ? '접기 ▲' : '펼치기 ▼'}
                        </Button>
                      </div>
                      {showGraph && (
                        <Suspense fallback={<div style={styles.lazyFallback}>관계망을 불러오는 중…</div>}>
                          <DecisionGraphViewLazy
                            graphData={graphData}
                            onOpenDocument={onOpenDocument}
                            height={320}
                          />
                        </Suspense>
                      )}
                    </div>
                  )}
                </div>
              )}
              {busy && !streaming && <div style={styles.thinking}>근거를 찾는 중…</div>}

              {status === 'NO_EVIDENCE' && suggestions.length > 0 && (
                <div style={styles.suggestBox}>
                  <div style={{ fontSize: 12, marginBottom: 6 }}>이렇게 검색해 보세요</div>
                  {suggestions.map((s) => (
                    <button key={s} onClick={() => setDraft(s)} style={styles.chip}>{s}</button>
                  ))}
                </div>
              )}
              {status === 'RATE_LIMITED' && (
                <div style={styles.suggestBox}>
                  현재 요청이 많아 답변을 만들지 못했습니다.
                  {resumeAt && ` 예상 재개 시각: ${new Date(resumeAt).toLocaleTimeString('ko-KR')}`}
                </div>
              )}
              {error && (
                <div style={styles.error}>
                  <div>{error}</div>
                  {failedQuestion && (
                    <Button size="sm" onClick={() => void send(failedQuestion)} style={{ marginTop: 6 }}>
                      다시 생성
                    </Button>
                  )}
                </div>
              )}
              <div ref={bottomRef} />
            </div>

            <div style={styles.composer}>
              <input
                value={draft}
                placeholder="이어서 질문하기"
                disabled={busy}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) void send(); }}
              />
              <Button variant="primary" onClick={() => void send()} disabled={busy || !draft.trim()}>
                {busy ? '생성 중…' : '전송'}
              </Button>
            </div>
          </>
        )}
      </section>

      {currentId && (sources.length > 0 || lastMessageId) && (
        <SourcePanel sources={sources} messageId={lastMessageId} onOpenDocument={onOpenDocument} />
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  // 지연 로딩되는 무거운 뷰(관계망·PDF 뷰어)를 받는 동안 자리를 지킨다
  lazyFallback: { padding: 16, textAlign: 'center', fontSize: 12.5, color: 'var(--ink-3)' },
  wrap: {
    display: 'flex', background: 'var(--paper)', border: '1px solid var(--rule)',
    borderRadius: 10, overflow: 'hidden', height: 'calc(100vh - 150px)', minHeight: 460,
  },
  side: { width: 220, flexShrink: 0, borderRight: '1px solid var(--rule)', padding: 14, overflowY: 'auto', background: '#fbfcfd' },
  convItem: { padding: '8px 10px', borderRadius: 6, cursor: 'pointer', marginBottom: 4, border: '1px solid transparent' },
  convActive: { background: 'var(--accent-bg)', borderColor: '#ccd9fb' },
  convTitle: { fontSize: 12.5, fontWeight: 600, lineHeight: 1.4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  convMeta: { fontSize: 10.5, color: 'var(--ink-3)', marginTop: 2, display: 'flex', alignItems: 'center' },
  del: { marginLeft: 'auto', background: 'none', border: 'none', color: 'var(--ink-3)', padding: '0 4px' },
  sideEmpty: { fontSize: 12, color: 'var(--ink-3)', textAlign: 'center', marginTop: 20 },
  main: { flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 },
  stream: { flex: 1, overflowY: 'auto', padding: '20px 22px' },
  userTurn: { background: 'var(--accent-bg)', borderRadius: 8, padding: '10px 13px', margin: '0 0 14px auto', maxWidth: '70%', fontSize: 13 },
  botTurn: { marginBottom: 18, maxWidth: '85%' },
  text: { fontSize: 13.5, lineHeight: 1.85, whiteSpace: 'pre-wrap' },
  model: { fontSize: 10.5, color: 'var(--ink-3)', marginLeft: 8, fontFamily: 'var(--mono)' },
  thinking: { fontSize: 13, color: 'var(--ink-3)' },
  suggestBox: { marginTop: 4, padding: '10px 12px', background: 'var(--tint)', borderRadius: 6 },
  chip: {
    fontSize: 11.5, padding: '4px 10px', borderRadius: 12, marginRight: 6,
    background: 'var(--paper)', border: '1px solid var(--rule)', color: 'var(--ink-2)',
  },
  error: { marginTop: 10, padding: '9px 11px', background: 'var(--err-bg)', color: 'var(--err)', borderRadius: 6, fontSize: 12.5 },
  composer: { display: 'flex', gap: 10, padding: 14, borderTop: '1px solid var(--rule)', background: '#fbfcfd' },
  blank: { flex: 1, display: 'grid', placeContent: 'center', textAlign: 'center', color: 'var(--ink-3)' },
  blankTitle: { fontSize: 16, fontWeight: 700, color: 'var(--ink)' },
  blankSub: { fontSize: 12.5, marginTop: 6 },
};
