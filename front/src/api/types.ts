/** 서버 API 의 요청·응답 타입. */

export interface Team {
  id: number;
  organizationId: number;
  name: string;
}

export interface Organization {
  id: number;
  name: string;
}

/** 관리자가 재발급한 임시 비밀번호. 이 응답에서만 볼 수 있다. */
export interface IssuedTemporaryPassword {
  userId: number;
  email: string;
  temporaryPassword: string;
}

// ── 팀별 거버넌스 알림 채널 ───────
export interface TeamNotificationChannel {
  teamId: number;
  slackWebhookUrl: string | null;
  teamsWebhookUrl: string | null;
  notificationEmails: string[];
  updatedAt: string | null;
}

// ── 사용자 소속·역할 ───────────────────────
export type SystemRole = 'SYSTEM_ADMIN' | 'ORG_ADMIN' | 'TEAM_ADMIN' | 'MEMBER';

export const ROLE_LABEL: Record<SystemRole, string> = {
  SYSTEM_ADMIN: '시스템 관리자',
  ORG_ADMIN: '조직 관리자',
  TEAM_ADMIN: '팀 관리자',
  MEMBER: '일반 사용자',
};

export interface UserSummary {
  id: number;
  email: string;
  name: string;
  status: 'ACTIVE' | 'DISABLED';
  organizationId: number | null;
  organizationName: string | null;
  teams: Team[];
  roles: SystemRole[];
}

export interface UserDetail extends UserSummary {
  createdAt: string;
  updatedAt: string;
}

/** 계정 발급 응답 — temporaryPassword 는 이 응답에서만 노출된다. */
export interface IssuedUserAccount extends UserDetail {
  temporaryPassword: string;
}

export interface CurrentUser {
  id: number;
  email: string;
  name: string;
  status: string;
  organizationId: number | null;
  organizationName: string | null;
  teams: Team[];
  roles: string[];
}

export interface TokenResponse {
  accessToken: string;
  refreshToken: string;
  expiresIn: number;
  user: CurrentUser;
}

export interface ApiError {
  code: string;
  message: string;
  details?: { field: string; message: string }[];
  correlationId?: string;
}

// ── 문서 ─────────────────────────────────────────────
export type DocumentType = 'MEETING_NOTE' | 'DECISION_RECORD' | 'DESIGN_DOC' | 'POLICY_DOC';
export type DecisionStatus = 'CURRENT' | 'CHANGED' | 'DEPRECATED' | 'UNDECIDED';

export const DOCUMENT_TYPE_LABEL: Record<DocumentType, string> = {
  MEETING_NOTE: '회의록',
  DECISION_RECORD: '의사결정 기록',
  DESIGN_DOC: '기획·설계 문서',
  POLICY_DOC: '정책·규정 문서',
};

export const DECISION_STATUS_LABEL: Record<DecisionStatus, string> = {
  CURRENT: '현재 적용',
  CHANGED: '변경',
  DEPRECATED: '폐기',
  UNDECIDED: '미확정',
};

export interface DocumentSummary {
  id: number;
  title: string;
  documentType: DocumentType;
  decisionStatus: DecisionStatus;
  organizationId: number;
  teamId: number | null;
  projectName: string | null;
  authorId: number | null;
  authorName: string;
  authoredAt: string;
  currentVersion: number;
  updatedAt: string;
  hasFile: boolean;
}

export interface DocumentFileInfo {
  id: number;
  documentId: number;
  version: number;
  originalFilename: string;
  mimeType: string;
  sizeBytes: number;
  checksumSha256: string;
  uploadedAt: string;
}

export interface DocumentDetail extends Omit<DocumentSummary, 'hasFile'> {
  content: string;
  origin: string;
  createdAt: string;
  file: DocumentFileInfo | null;
}

/** 그 버전 시점의 본문까지 담는다. 목록에는 본문이 없다. */
export interface DocumentVersionDetail {
  version: number;
  title: string;
  decisionStatus: DecisionStatus;
  changeNote: string | null;
  createdBy: number | null;
  createdAt: string;
  content: string;
}

export interface DocumentVersionInfo {
  version: number;
  title: string;
  decisionStatus: DecisionStatus;
  changeNote: string | null;
  createdBy: number | null;
  createdAt: string;
  fileId: number | null;
}

export interface DocumentPage {
  content: DocumentSummary[];
  page: number;
  size: number;
  totalElements: number;
  totalPages: number;
  typeCounts: Record<string, number>;
}

export interface DownloadUrl {
  downloadUrl: string;
  expiresAt: string;
  originalFilename: string;
  mimeType: string;
  sizeBytes: number;
}

// ── 권한 ─────────────────────────────────────────────
export interface AccessPolicy {
  id: number;
  subjectType: 'USER' | 'ROLE' | 'TEAM';
  subjectName: string;
  scopeType: 'ORGANIZATION' | 'TEAM' | 'DOCUMENT';
  scopeName: string;
  canRead: boolean;
  createdAt: string;
  revokedAt: string | null;
}

export interface AuditEntry {
  id: number;
  actorId: number | null;
  actorName: string | null;
  action: string;
  targetType: string | null;
  targetId: number | null;
  result: 'ALLOWED' | 'DENIED';
  reason: string | null;
  correlationId: string | null;
  occurredAt: string;
}

export interface Paged<T> {
  content: T[];
  page: number;
  size: number;
  totalElements: number;
  totalPages: number;
}

// ── 색인 ──────────────────────────────────
export type JobStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED';

export const JOB_STATUS_LABEL: Record<JobStatus, string> = {
  PENDING: '대기',
  RUNNING: '처리중',
  COMPLETED: '완료',
  FAILED: '실패',
};

export const INDEX_STAGE_LABEL: Record<string, string> = {
  PARSE: '본문 추출',
  CHUNK: '문서 분할',
  EMBED: '임베딩 생성',
  VECTOR_UPSERT: '벡터 저장',
  KEYWORD_INDEX: '전문검색 색인',
};

export const INDEX_TRIGGER_LABEL: Record<string, string> = {
  DOCUMENT_UPLOAD: '문서 등록',
  DOCUMENT_UPDATE: '문서 수정',
  DOCUMENT_DELETE: '문서 삭제',
  BULK_INDEX: '일괄 색인',
  EXTERNAL_SYNC: '외부 동기화',
};

export interface IndexJob {
  jobKey: string;
  triggerType: string;
  status: JobStatus;
  totalCount: number;
  successCount: number;
  failCount: number;
  pendingCount: number;
  progress: number;
  requestedBy: number | null;
  requestedAt: string;
  finishedAt: string | null;
}

export interface IndexJobItem {
  id: number;
  documentId: number;
  documentTitle: string | null;
  documentVersion: number;
  status: JobStatus;
  stage: string | null;
  failureReason: string | null;
  retryable: boolean;
  retryCount: number;
  lastAttemptAt: string | null;
}

// ── 대화 ──────────────────────────────────
export type AnswerStatus = 'OK' | 'NO_EVIDENCE' | 'LOW_CONFIDENCE' | 'DEGRADED' | 'RATE_LIMITED';

export const ANSWER_STATUS_LABEL: Record<AnswerStatus, string> = {
  OK: '정상',
  NO_EVIDENCE: '근거 없음',
  LOW_CONFIDENCE: '근거 부족',
  DEGRADED: '간이 모드',
  RATE_LIMITED: '대기',
};

export interface Conversation {
  id: number;
  title: string;
  createdAt: string;
  updatedAt: string;
}

export interface Message {
  id: number;
  conversationId: number;
  role: 'USER' | 'ASSISTANT';
  content: string;
  modelName: string | null;
  answerStatus: AnswerStatus | null;
  latencyMs: number | null;
  sourceCount: number;
  createdAt: string;
}

export interface AnswerSource {
  documentId: number;
  documentTitle: string;
  documentType: DocumentType;
  authoredAt: string | null;
  documentVersion: number;
  chunkNo: number;
  sectionPath: string | null;
  chunkTag: string | null;
  /** 전문검색으로만 걸린 근거는 코사인 유사도가 없다. */
  similarity: number | null;
  rank: number;
  fileId: number | null;
  /** 바운딩 박스. PDF가 아니면 셋 다 null */
  pageStart: number | null;
  pageEnd: number | null;
  bboxes: BoundingBox[] | null;
}

export interface BoundingBox {
  page: number;
  /** 0~1 정규화, 페이지 좌상단 기준 */
  x: number;
  y: number;
  w: number;
  h: number;
}

// ── 의사결정 지식 그래프 DAG ───────────────
export interface GraphNode {
  id: string;
  label: 'Decision' | 'System' | 'Team' | 'Policy' | string;
  name: string;
  properties?: Record<string, any>;
}

export interface GraphEdge {
  source: string;
  target: string;
  relType: string;
  label: string;
  properties?: Record<string, any>;
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// ── 타임라인 ──────────────────────────────
export interface TimelineEvent {
  documentId: number;
  documentTitle: string;
  documentType: DocumentType;
  occurredAt: string | null;
  decisionStatus: DecisionStatus;
  projectName: string | null;
  decisionExcerpt: string | null;
  fileId: number | null;
}

export interface TimelineConflict {
  documentAId: number;
  documentATitle: string;
  documentAAuthoredAt: string | null;
  documentAStatus: DecisionStatus;
  documentBId: number;
  documentBTitle: string;
  documentBAuthoredAt: string | null;
  documentBStatus: DecisionStatus;
  difference: string;
}

export interface TimelineResponse {
  topic: string;
  events: TimelineEvent[];
  conflicts: TimelineConflict[];
}

// ── 운영 현황 ──────────────────────────────
export type HealthStatus = 'UP' | 'DEGRADED' | 'DOWN' | 'UNKNOWN';

export const HEALTH_STATUS_LABEL: Record<HealthStatus, string> = {
  UP: '정상', DEGRADED: '저하', DOWN: '장애', UNKNOWN: '확인 불가',
};

export interface ComponentHealth {
  name: string;
  status: HealthStatus;
  latencyMs: number | null;
}

export interface OpsHealth {
  storages: ComponentHealth[];
  services: ComponentHealth[];
  checkedAt: string;
}

export interface OpsIndexStatus {
  pendingCount: number;
  runningCount: number;
  failCount: number;
  indexLagSeconds: number;
}

export interface OpsError {
  occurredAt: string;
  service: string;
  message: string;
  correlationId: string | null;
}

export interface TraceSpan {
  service: string;
  operation: string | null;
  startedAt: string;
  durationMs: number;
  status: 'OK' | 'ERROR';
  message: string | null;
}

/** 스트림 DLQ 현황. `key` 를 재처리·폐기 요청 경로에 그대로 쓴다. */
export interface DlqStatus {
  key: string;
  stream: string;
  dlqStream: string;
  consumerService: string;
  count: number;
  oldestAt: string | null;
}

export interface DlqEntry {
  id: string;
  streamKey: string;
  sourceStream: string;
  sourceId: string | null;
  consumerGroup: string | null;
  deliveries: number;
  reason: string | null;
  addedAt: string | null;
  /** `dlq.*` 메타를 뺀 원래 이벤트 본문. 되돌릴 때 실제로 다시 넣는 필드다 */
  payload: Record<string, string>;
}

export interface DlqActionResult {
  succeeded: number;
  failedIds: string[];
}

// ── 일괄 가져오기 ──────────────────────────
export interface ImportJob {
  jobKey: string;
  status: JobStatus;
  fileCount: number;
  successCount: number;
  failCount: number;
  defaultDocumentType: DocumentType;
  organizationId: number;
  teamId: number | null;
  requestedAt: string;
  finishedAt: string | null;
}

export interface ImportItem {
  id: number;
  filename: string;
  documentId: number | null;
  extractedTitle: string | null;
  extractedAuthorName: string | null;
  extractedAuthoredAt: string | null;
  status: 'SUCCESS' | 'FAILED';
  failureReason: string | null;
  retryable: boolean;
}

export interface ImportJobDetail extends ImportJob {
  items: ImportItem[];
}

// ── 거버넌스 및 정책 상충 ──────────────────
/** SUPERSEDED 는 사람이 고르는 값이 아니다 — 문서가 개정되면 governance-worker 가 옛 판 기준 상충을 닫는다. */
export type ConflictStatus = 'OPEN' | 'RESOLVED' | 'DISMISSED' | 'SUPERSEDED';

export const CONFLICT_STATUS_LABEL: Record<ConflictStatus, string> = {
  OPEN: '조치 필요 (OPEN)',
  RESOLVED: '해결 완료 (RESOLVED)',
  DISMISSED: '예외 허용 (DISMISSED)',
  SUPERSEDED: '개정으로 만료 (SUPERSEDED)',
};

/** 기록 시점 심각도 등급. governance-worker 가 점수와 그때의 구간으로 정해 저장한다. */
export type ConflictSeverityLevel = 'CRITICAL' | 'HIGH' | 'MEDIUM';

export interface ConflictItem {
  id: number;
  sourceDocId: number;
  sourceDocTitle: string;
  targetDocId: number;
  targetDocTitle: string;
  sourceClause: string;
  targetClause: string;
  conflictType: string;
  severityScore: number;
  severityLevel: ConflictSeverityLevel;
  reasoning: string;
  affectedTeams: string[];
  status: ConflictStatus;
  resolvedBy: string | null;
  resolutionNote: string | null;
  resolvedAt: string | null;
  createdAt: string;
}

export interface ResolveConflictRequest {
  status: 'RESOLVED' | 'DISMISSED';
  resolutionNote: string;
}

// ── 제품 지표 ───────────────

/**
 * 핵심 지표 목표값. 화면에서 달성 여부를 표시하는 데만 쓴다.
 * 서버는 목표를 모르고 분자·분모만 내려준다 — 목표는 제품 정책이라 여기 둔다.
 */
export const METRIC_TARGETS = {
  /** 지표 1: 출처 포함 답변 성공률 */
  answerSuccess: 0.85,
  /** 지표 2: 출처의 사용자 검증 가능 비율 */
  sourceVerifiable: 0.95,
  /** 지표 3: 유용하다고 평가된 답변 비율 */
  helpful: 0.8,
  /** 지표 8: 활성 사용자 중 질문한 비율 */
  adoption: 0.6,
} as const;

export interface StatusCount {
  status: string;
  count: number;
}

export interface AnswerQualityMetric {
  assistantMessages: number;
  withSources: number;
  ok: number;
  okWithSources: number;
  byStatus: StatusCount[];
}

export interface SourceVerifiabilityMetric {
  citedSources: number;
  withOriginalFile: number;
  withCoordinates: number;
}

export interface FeedbackMetric {
  responded: number;
  helpful: number;
  notHelpful: number;
  assistantMessages: number;
}

export interface LatencyMetric {
  measured: number;
  averageMs: number | null;
  p95Ms: number | null;
  overTargetCount: number;
  targetMs: number;
}

export interface ActionCount {
  action: string;
  count: number;
}

export interface AccessDenialsMetric {
  total: number;
  byAction: ActionCount[];
}

export interface AdoptionMetric {
  activeUsers: number;
  askedUsers: number;
  questions: number;
}

export interface ProductMetrics {
  from: string;
  to: string;
  days: number;
  answerQuality: AnswerQualityMetric;
  sources: SourceVerifiabilityMetric;
  feedback: FeedbackMetric;
  latency: LatencyMetric;
  denials: AccessDenialsMetric;
  adoption: AdoptionMetric;
  /** 읽지 못해 0으로 내려간 구획 이름. 비어 있지 않으면 그 카드의 수치를 믿으면 안 된다. */
  degraded: string[];
}

export type FeedbackRating = 'HELPFUL' | 'NOT_HELPFUL';

export interface FeedbackItem {
  messageId: number;
  conversationId: number;
  rating: FeedbackRating;
  reason: string | null;
  createdAt: string;
  raterName: string | null;
  question: string | null;
  answerPreview: string;
  answerStatus: string | null;
  latencyMs: number | null;
  sourceCount: number;
}

export interface CitedSource {
  documentId: number;
  documentTitle: string;
  documentVersion: number;
  chunkNo: number;
  rank: number;
  similarity: number | null;
  sectionPath: string | null;
  hasOriginalFile: boolean;
}

export interface FeedbackDetail {
  messageId: number;
  conversationId: number;
  rating: FeedbackRating;
  reason: string | null;
  createdAt: string;
  raterName: string | null;
  question: string | null;
  rewrittenQuery: string | null;
  answer: string;
  answerStatus: string | null;
  modelName: string | null;
  latencyMs: number | null;
  answeredAt: string;
  sources: CitedSource[];
}
