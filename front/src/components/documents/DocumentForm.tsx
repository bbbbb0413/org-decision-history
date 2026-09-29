import { useEffect, useState, type FormEvent } from 'react';
import { api, HttpError } from '../../api/client';
import {
  DECISION_STATUS_LABEL, DOCUMENT_TYPE_LABEL,
  type CurrentUser, type DecisionStatus, type DocumentType,
} from '../../api/types';
import { Button } from '../ui/Button';
import { Field } from '../ui/Field';
import { DOCUMENT_TEMPLATES, TEMPLATE_COMMON_GUIDE, isBlankBody } from '../../lib/documentTemplates';

interface Props {
  user: CurrentUser;
  documentId: number | null;
  onDone: (id: number) => void;
  onCancel: () => void;
}

const TYPES = Object.keys(DOCUMENT_TYPE_LABEL) as DocumentType[];
const STATUSES = Object.keys(DECISION_STATUS_LABEL) as DecisionStatus[];

/** 문서 등록·편집. 필수 항목 누락은 필드 단위로 사유를 표시한다. */
export function DocumentForm({ user, documentId, onDone, onCancel }: Props) {
  const editing = documentId !== null;

  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [documentType, setDocumentType] = useState<DocumentType | ''>('');
  const [decisionStatus, setDecisionStatus] = useState<DecisionStatus | ''>('');
  const [teamId, setTeamId] = useState<string>('');
  const [projectName, setProjectName] = useState('');
  const [authoredAt, setAuthoredAt] = useState(new Date().toISOString().slice(0, 10));
  const [currentVersion, setCurrentVersion] = useState(1);
  const [file, setFile] = useState<File | null>(null);

  const [summary, setSummary] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!editing) return;
    api.document(documentId).then((d) => {
      setTitle(d.title);
      setContent(d.content);
      setDocumentType(d.documentType);
      setDecisionStatus(d.decisionStatus);
      setTeamId(d.teamId ? String(d.teamId) : '');
      setProjectName(d.projectName ?? '');
      setAuthoredAt(d.authoredAt);
      setCurrentVersion(d.currentVersion);
    });
  }, [documentId, editing]);

  /**
   * 유형을 고르면 템플릿을 넣는다.
   *
   * 조건을 좁게 잡았다 — 새 문서이고 본문이 비어 있을 때만이다. 이미 쓴 내용을
   * 덮어쓰면 그 한 번으로 신뢰를 잃고, 편집 화면에서 끼워 넣으면 기존 문서를 망친다.
   * 유형 select 는 편집 중에는 disabled 라 편집 경로로는 애초에 들어오지 않지만,
   * 조건을 여기 명시해 둔다.
   */
  function changeType(next: DocumentType) {
    setDocumentType(next);
    if (!editing && isBlankBody(content)) {
      setContent(DOCUMENT_TEMPLATES[next].body);
    }
  }

  /** 템플릿을 지웠다가 다시 넣고 싶을 때. 쓴 내용이 있으면 확인을 받는다. */
  function insertTemplate() {
    if (!documentType) return;
    const body = DOCUMENT_TEMPLATES[documentType].body;
    if (isBlankBody(content) || window.confirm('지금 쓴 본문을 템플릿으로 바꿉니다. 계속할까요?')) {
      setContent(body);
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setSummary(null);
    setFieldErrors({});
    try {
      const teamValue = teamId ? Number(teamId) : null;
      const saved = editing
        ? await api.updateDocument(documentId, {
            title, content, decisionStatus, teamId: teamValue,
            projectName: projectName || null, changeNote: null, currentVersion,
          })
        : await api.createDocument({
            title, content, documentType, decisionStatus,
            organizationId: user.organizationId, teamId: teamValue,
            projectName: projectName || null, authoredAt,
          });

      if (file) await api.uploadFile(saved.id, file);
      onDone(saved.id);
    } catch (e) {
      if (e instanceof HttpError) {
        setSummary(e.body.message);
        setFieldErrors(Object.fromEntries((e.body.details ?? []).map((d) => [d.field, d.message])));
      } else {
        setSummary('저장하지 못했습니다.');
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} style={styles.card}>
      <header style={styles.head}>
        <b style={{ fontSize: 14 }}>{editing ? '문서 편집' : '새 문서'}</b>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <Button type="button" onClick={onCancel}>취소</Button>
          <Button type="submit" variant="primary" disabled={busy}>{busy ? '저장 중…' : '저장'}</Button>
        </div>
      </header>

      <div style={styles.body}>
        {summary && <div style={styles.summary}>{summary}</div>}

        <Field label="제목" required error={fieldErrors.title}>
          <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} />
        </Field>

        <div style={styles.grid2}>
          <Field label="문서 유형" required error={fieldErrors.documentType}>
            <select value={documentType} disabled={editing}
                    onChange={(e) => changeType(e.target.value as DocumentType)} style={styles.select}>
              <option value="">선택하세요</option>
              {TYPES.map((t) => <option key={t} value={t}>{DOCUMENT_TYPE_LABEL[t]}</option>)}
            </select>
          </Field>
          <Field label="결정 상태" required error={fieldErrors.decisionStatus}>
            <select value={decisionStatus}
                    onChange={(e) => setDecisionStatus(e.target.value as DecisionStatus)} style={styles.select}>
              <option value="">선택하세요</option>
              {STATUSES.map((s) => <option key={s} value={s}>{DECISION_STATUS_LABEL[s]}</option>)}
            </select>
          </Field>
        </div>

        <div style={styles.grid3}>
          <Field label="팀">
            <select value={teamId} onChange={(e) => setTeamId(e.target.value)} style={styles.select}>
              <option value="">선택 안 함</option>
              {user.teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </Field>
          <Field label="관련 프로젝트">
            <input value={projectName} onChange={(e) => setProjectName(e.target.value)} maxLength={100} />
          </Field>
          <Field label="작성일" required error={fieldErrors.authoredAt}>
            <input type="date" value={authoredAt} disabled={editing}
                   onChange={(e) => setAuthoredAt(e.target.value)} />
          </Field>
        </div>

        {/* 가이드는 Field 밖에 둔다. Field 의 label 이 자식을 감싸므로 안에 버튼을 넣으면
            라벨이 textarea 대신 버튼을 가리키고, 버튼의 접근성 이름까지 라벨 텍스트로
            바뀐다(테스트가 잡았다). */}
        {documentType && (
          <div style={styles.guide}>
            <div style={styles.guideRow}>
              <b style={styles.guideTitle}>{DOCUMENT_TYPE_LABEL[documentType]} 작성 요령</b>
              {!editing && (
                <Button type="button" size="sm" onClick={insertTemplate} style={{ marginLeft: 'auto' }}>
                  템플릿 다시 넣기
                </Button>
              )}
            </div>
            <div>{DOCUMENT_TEMPLATES[documentType].guide}</div>
            <div style={styles.guideCommon}>{TEMPLATE_COMMON_GUIDE}</div>
          </div>
        )}

        <Field label="본문" required error={fieldErrors.content}>
          <textarea value={content} onChange={(e) => setContent(e.target.value)} rows={16} style={styles.textarea} />
          <div style={styles.hint}>
            템플릿은 참고용입니다. 절을 지우거나 통째로 바꿔 써도 됩니다.
          </div>
        </Field>

        <Field label="원본 파일">
          <input type="file" accept=".md,.txt,.pdf,.docx,.pptx,.xlsx"
                 onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={styles.file} />
          <div style={styles.hint}>md · txt · PDF · docx · pptx · xlsx / 최대 50 MB. 업로드한 형태 그대로 보관한다.</div>
        </Field>
      </div>
    </form>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: { background: 'var(--paper)', border: '1px solid var(--rule)', borderRadius: 10, overflow: 'hidden' },
  head: { display: 'flex', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid var(--rule)', background: '#fbfcfd' },
  body: { padding: 20 },
  summary: {
    marginBottom: 16, padding: '10px 12px', background: 'var(--err-bg)',
    border: '1px solid #f5c9c9', borderRadius: 6, color: 'var(--err)', fontSize: 13, fontWeight: 600,
  },
  grid2: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 14px' },
  grid3: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '0 14px' },
  select: {
    width: '100%', padding: '10px 12px', border: '1px solid var(--rule)',
    borderRadius: 6, background: 'var(--paper)', font: 'inherit',
  },
  textarea: {
    width: '100%', padding: '10px 12px', border: '1px solid var(--rule)', borderRadius: 6,
    font: 'inherit', lineHeight: 1.7, resize: 'vertical',
  },
  file: { border: 'none', padding: 0 },
  guide: {
    marginBottom: 8, padding: '10px 12px', background: 'var(--accent-bg)',
    border: '1px solid #ccd9fb', borderRadius: 6, fontSize: 12.5,
    color: 'var(--ink-2)', lineHeight: 1.6, display: 'flex', flexDirection: 'column', gap: 4,
  },
  guideRow: { display: 'flex', alignItems: 'center', gap: 8 },
  guideTitle: { fontSize: 12.5, color: 'var(--accent)' },
  guideCommon: { fontSize: 11.5, color: 'var(--ink-3)' },
  hint: { fontSize: 11.5, color: 'var(--ink-3)', marginTop: 4 },
};
