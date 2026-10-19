# index-worker (Python)

문서 변경 이벤트를 받아 원문을 파싱·분할·임베딩하고 Qdrant(`document_chunks`)와 PostgreSQL 전문검색 색인(`indexing.document_chunk`)에 저장한다.
규약: [`contracts/indexing/README.md`](../../../contracts/indexing/README.md).

## 구성

| 모듈 | 역할 |
|---|---|
| `app/parse.py` | md·txt·pdf(pdfplumber)·docx(python-docx)·pptx(python-pptx)·xlsx(openpyxl) → 본문·헤딩·표 |
| `app/chunk.py` | 헤딩 우선 분할, 700토큰 넘는 절만 줄 단위 분할(목표 400·겹침 60), 태그 |
| `app/documents.py` | `document` 스키마 읽기(쓰지 않는다) |
| `app/storage.py` | 객체 저장소(MinIO)에서 원본 읽기 |
| `app/chunks_store.py` | 전문검색 색인 저장 |
| `app/pipeline.py` | 파싱 → 분할 → 임베딩 → 벡터 → 전문검색, 단계별 실패 분류 |
| `app/jobs.py` | 접수·선점·재시도·집계·멈춘 항목 회수 |
| `app/runner.py` | 1초 주기, 한 번에 최대 20건 |
| `app/consumer.py` | `stream:document-events` → 작업 접수 |
| `app/queries.py`, `app/api.py` | `/api/index-jobs` |
| `app/main.py` | HTTP API·소비자·러너를 한 이벤트 루프에서 실행 |

임베딩·Qdrant·스트림 소비·웹 공통 규약은 [`history_shared`](../shared/README.md)를 쓴다.

## 설정

환경변수 이름은 필드 이름과 같다(대소문자 무시). 공통 묶음(`POSTGRES_*`, `REDIS_*`, `QDRANT_*`, `GEMINI_API_KEY`, `EMBEDDING_*`)은 shared README에 있다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `SERVER_PORT` | `8083` | |
| `INTERNAL_API_KEY` | 빈 값 | 비어 있으면 `/actuator` 밖의 모든 요청을 거부한다 |
| `STORAGE_ENDPOINT`, `STORAGE_BUCKET`, `STORAGE_REGION` | `http://localhost:9000`, `documents`, `us-east-1` | |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | `history`, 빈 값 | 읽기만 한다 |
| `CONSUMER_ENABLED`, `RUNNER_ENABLED` | `true` | 둘 다 끄면 API만 뜬다(운영 데이터로 응답만 비교할 때) |
| `CONSUMER_GROUP` | `index-worker` | Redis Stream 소비자 그룹 |
| `CONSUMER_NAME` | `index-worker-{호스트명}` | 프로세스마다 달라야 한다 |
| `STREAM_CLAIM_MIN_IDLE_MS`, `STREAM_MAX_DELIVERIES` | `60000`, `5` | 재처리 대기 시간, DLQ로 옮기는 전달 횟수 |
| `RUNNER_INTERVAL_SECONDS`, `RUNNER_MAX_PER_TICK` | `1.0`, `20` | |
| `RUNNING_HEARTBEAT_SECONDS` | `10` | 처리 중 항목의 생존 신호(`heartbeat_at`)를 쓰는 간격 |
| `RUNNING_STALE_SECONDS` | `60` | 생존 신호가 이보다 오래 끊긴 `RUNNING` 은 워커가 죽은 것으로 보고 되돌린다. 신호 간격의 두 배보다 커야 한다(아니면 기동 실패) |
| `RUNNING_RECOVER_INTERVAL_SECONDS` | `30` | 멈춘 항목을 찾는 주기. 워커가 죽은 뒤 최악의 회수 지연은 `RUNNING_STALE_SECONDS` + 이 값 |

Gemini 키가 없으면 대체 임베딩을 쓴다. rag-service 질의도 같은 대체 구현을 써 벡터 공간이 맞는다.

## 마이그레이션

`indexing` 스키마는 Flyway 가 관리하고, compose 의 `index-migrate` 컨테이너가 워커보다 먼저 적용한다. 적용된 파일은 고치지 않는다 — checksum 이 달라지면 Flyway 가 기동을 거부한다. `tests/test_migration.py` 가 V1 checksum 을 고정한다. 스키마 변경은 새 버전 파일로만 한다.

## 테스트

DB·Redis 테스트는 실제 서버가 필요하다. 주소가 없으면 건너뛴다(CI에서는 실패로 본다).

```bash
docker run -d --rm --name iw-pg -p 55432:5432 -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=index_worker_test postgres:16.4-alpine
docker run -d --rm --name iw-redis -p 56379:6379 redis:7.4-alpine

python -m pip install -r requirements.txt ../shared
TEST_DATABASE_URL=postgresql://test:test@localhost:55432/index_worker_test \
TEST_REDIS_URL=redis://localhost:56379/15 python -m pytest -q
```

테스트는 `indexing`·`document` 스키마를 지우고 다시 만든다. DB 이름에 `test`가 없으면 거부한다.

| 파일 | 확인하는 것 |
|---|---|
| `test_golden.py` | 골든 스냅샷과 파싱·청킹·point ID 완전 일치 |
| `test_chunk.py`, `test_parse.py` | 헤딩 우선 분할·길이 분할, 계약의 정규식 줄 규칙, PDF 헤딩 추정, 표·pptx·xlsx, 암호화 PDF(`fixtures/encrypted.pdf`는 pypdf로 만든 빈 쪽 문서) |
| `test_pipeline.py` | 두 색인의 청크·payload 스키마, 재색인 시 구버전 제거, 삭제 반영, 단계별 실패 분류 |
| `test_jobs.py` | 멱등·동시 접수, `SKIP LOCKED` 동시 선점, 백오프, 수동 재처리, 멈춘 항목 회수 |
| `test_api.py` | 응답 필드·순서·시각 표기, 401·403·404·409·400 |
| `test_consumer.py`, `test_runner.py` | 중복 이벤트·DLQ, 러너 상한·루프 재기동 |
| `test_migration.py` | Flyway checksum |

## 이미지

```bash
docker buildx bake index-worker --load   # history/index-worker:dev
```
