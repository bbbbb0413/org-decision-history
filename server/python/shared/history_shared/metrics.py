"""Prometheus 메트릭.

Spring 서비스는 actuator 가 `/actuator/prometheus` 를 만들어 주지만 Python 워커에는
그런 게 없었다. 여기서 같은 경로·같은 형식으로 맞춘다.

두 가지 노출 방식을 둔다 — 워커에 HTTP 앱이 있는지에 따라 다르다.

- FastAPI 앱이 있는 워커(index-worker): `install_common()` 이 `metrics_router` 를 함께
  붙인다. `/actuator/prometheus` 는 `OPEN_PREFIXES` 에 걸려 내부 키 없이 열린다
  (헬스체크와 같은 취급이다).
- HTTP 앱이 없는 워커(governance-worker·rag-service): `start_metrics_server(port)` 로
  작은 전용 서버를 띄운다. compose 에서 포트를 공개하지 않으므로 도커 네트워크
  안에서만 닿는다.

메트릭에 `service` 라벨을 따로 붙이지 않는다 — Prometheus 가 스크레이프할 때 `job`
라벨을 붙이고, 손으로 curl 할 때는 어느 주소를 찔렀는지가 곧 서비스다. 대신
`app_info` 로 서비스 이름을 한 줄 남겨 덤프만 봐도 알 수 있게 한다.
"""
import threading

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from prometheus_client import start_http_server as _start_http_server

METRICS_PATH = "/actuator/prometheus"

# ---- 스트림 소비 (RedisStreamConsumer 가 기록한다) ----

STREAM_MESSAGES = Counter(
    "stream_messages_total",
    "스트림에서 꺼내 처리한 항목 수",
    # outcome: processed(ACK) / retried(ACK 안 하고 남김) / dead_lettered(DLQ 로 옮김)
    ["stream", "group", "outcome"],
)

STREAM_HANDLER_SECONDS = Histogram(
    "stream_handler_seconds",
    "스트림 항목 하나를 핸들러가 처리하는 데 걸린 시간",
    ["stream", "group"],
    # 색인·상충 감지는 LLM 호출을 포함해 초 단위다. 기본 버킷(최대 10초)으로는 위쪽이 다 뭉친다
    buckets=(0.05, 0.25, 1, 2.5, 5, 10, 30, 60, 120, 300),
)

STREAM_DEAD_LETTER_DEPTH = Gauge(
    "stream_dead_letter_depth",
    "DLQ 에 쌓여 있는 항목 수(이 프로세스가 마지막으로 옮긴 뒤 관측한 값)",
    ["stream"],
)

# ---- LLM 호출 ----

LLM_REQUEST_SECONDS = Histogram(
    "llm_request_seconds",
    "LLM 호출 하나에 걸린 시간",
    ["model", "outcome"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 20, 40, 80),
)

# ---- 답변 자가 비판 (rag-service) ----

CRITIQUE_CONFIDENCE = Histogram(
    "answer_critique_confidence",
    "자가 비판이 매긴 답변 신뢰도(모델이 실제로 평가한 것만)",
    # 판정 기준(0.7) 근처를 촘촘히 본다 — 기준값을 조정할 때 이 분포를 근거로 쓴다
    buckets=(0.1, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0),
)

# ---- 상충 감지 (governance-worker) ----

CONFLICT_DETECTIONS = Counter(
    "conflict_detections_total",
    "상충 감지를 돌린 문서 수",
    # outcome: conflicts_found / none / skipped(문서 없음·조항 추출 실패)
    ["outcome"],
)

CONFLICTS_RECORDED = Counter(
    "conflicts_recorded_total",
    "새로 기록하거나 갱신한 상충 수",
)

CONFLICTS_SUPERSEDED = Counter(
    "conflicts_superseded_total",
    "문서 개정으로 SUPERSEDED 처리한 상충 수",
)

# ---- 재순위화 (rag-service 호출). 서버 쪽 지표(te_predict_* 등)는 TEI 가 :8090/metrics 에 낸다 ----

RERANK_FALLBACKS = Counter(
    "rerank_fallback_total",
    "rag-service 가 재순위화 없이 1차 검색 순서로 답한 횟수. 0 이 아니면 답변 품질이 떨어진 것이다",
    # reason: disabled / timeout / http_error / bad_response
    ["reason"],
)

# ---- 근거 판정 (rag-service) ----

EVIDENCE_VERDICTS = Counter(
    "evidence_verdict_total",
    "근거 판정 결과와 그 경로. 답 없는 질문이 생성까지 가는지(새는지)를 본다",
    # outcome: low_similarity(벡터 유사도가 낮아 바로 거절) / judged_answerable / judged_unanswerable(경량 LLM 판정)
    #          unjudged_gate_passed / unjudged_gate_rejected(판정 실패 — 1차 거름으로 정함, 한도 초과가 잦으면 오른다)
    #          gate_passed / gate_rejected(판정기를 끈 경우)
    #          unscored(대체 임베딩인데 리랭커 점수도 없음 — 믿을 신호가 없어 거절)
    ["outcome"],
)

EVIDENCE_JUDGE_CACHE = Counter(
    "evidence_judge_cache_total",
    "근거 충분성 판정 캐시 조회. hit 이 많을수록 경량 모델 한도를 아낀다",
    ["result"],  # hit / miss
)

_APP_INFO = Gauge("app_info", "서비스 정보", ["service"])

_server_lock = threading.Lock()
_server_started = False


def set_service_name(service: str) -> None:
    """덤프만 봐도 어느 서비스인지 알 수 있게 `app_info{service="..."} 1` 을 남긴다."""
    _APP_INFO.labels(service=service).set(1)


def render() -> tuple[bytes, str]:
    """(본문, Content-Type). FastAPI 라우터와 전용 서버가 같은 걸 쓴다."""
    return generate_latest(), CONTENT_TYPE_LATEST


def start_metrics_server(port: int, service: str) -> None:
    """HTTP 앱이 없는 워커용 전용 메트릭 서버. 같은 프로세스에서 두 번 부르면 두 번째는 무시한다.

    prometheus_client 의 서버는 데몬 스레드에서 돌아 프로세스 종료를 막지 않는다.
    `/metrics` 와 `/actuator/prometheus` 중 어느 경로로 긁어도 같은 내용이 나온다
    (prometheus_client 기본 핸들러가 경로를 가리지 않는다).
    """
    global _server_started
    with _server_lock:
        if _server_started:
            return
        _start_http_server(port)
        _server_started = True
    set_service_name(service)
