# history-shared

Python 워커(index-worker, rag-service, governance-worker)가 함께 쓰는 모듈이다.
서비스 사이의 데이터 규약은 저장소 루트 [`contracts/`](../../../contracts/README.md)가 원본이고, 이 패키지는 그 규약의 Python 구현이다.

## 모듈

| 모듈 | 내용 | 규약 | 사용처 |
|---|---|---|---|
| `config` | `ServiceSettings`(Postgres·Redis·Qdrant·임베딩 설정 묶음) | — | rag-service, governance-worker |
| `db` | `create_pool` | — | rag-service, governance-worker |
| `logging` | 한 줄 JSON 로그, `correlationId`·`service` 필드, `correlation_scope` | Spring common `Correlation` 과 같은 필드 | 세 워커 |
| `text` | 계약의 문자열 규칙(`strip`·`isBlank`·UTF-16 길이), 토큰 추정 | [색인 계약 §6·§7](../../../contracts/indexing/README.md) | rag-service(프롬프트 예산), index-worker |
| `embeddings` | `GeminiEmbedder`(배치 20·토큰 버킷·`Retry-After`·taskType), `LocalHashingEmbedder`, `create_embedder` | 색인 계약 §10 | rag-service(질의), governance-worker(조항), index-worker(문서) |
| `vector` | `point_id`, `scope_key`, `scope_filter`, `chunk_payload`, `ChunkVectorStore` | [payload 스키마](../../../contracts/qdrant-chunk-payload.schema.json), 색인 계약 §8·§9·§11 | rag-service, governance-worker, index-worker |
| `events` | `DocumentEvent`, `DocumentIndexedEvent`, 스트림 이름 | [이벤트 스키마](../../../contracts/events/) | index-worker, governance-worker |
| `streams` | `RedisStreamConsumer`: 성공 시에만 ACK, `XAUTOCLAIM` 재처리, 전달 횟수 초과·영구 오류는 DLQ | 색인 계약 §12.1 | index-worker, governance-worker |
| `web` | `install_common`(내부 키 검사·상관관계 ID·오류 응답·`/actuator/health`), `PageResponse`, 사용자 헤더 | Spring common 필터·`ErrorResponse` 와 같은 응답 | index-worker. `[web]` 추가 설치 필요 |
| `metrics` | Prometheus 메트릭 정의(스트림 소비·LLM 호출·상충 감지), 노출 서버 | — | 세 워커 |
| `graph` | Neo4j 지식 그래프 조회·동기화, 라벨·관계 타입 허용 목록 검증, LLM 엔티티 추출 | — | rag-service(조회), governance-worker(동기화) |

## 설치

| 상황 | 방법 |
|---|---|
| 서비스 로컬 개발 | 서비스 디렉터리에서 `pip install -r requirements.txt -e ../shared` |
| Docker 이미지 | 서비스 `requirements.txt`를 설치한 뒤 `pip install --no-deps --target=/deps ./shared` (서비스 Dockerfile 참고) |
| CI | `ci-python`의 `shared` 작업은 `pip install ".[web,test]"`, 서비스 작업은 `pip install -r requirements.txt ../shared` |

- 런타임 의존성의 정확한 버전은 **서비스 `requirements.txt`가 고정**한다. `pyproject.toml`에는 호환 범위만 둔다.
- Docker는 `--no-deps`로 설치한다. shared에 새 의존성을 추가하면 **쓰는 서비스의 `requirements.txt`에도 추가**한다. 빠뜨리면 CI 테스트는 통과해도 이미지 실행 시 `ImportError`가 난다.
- shared가 바뀌면 `ci-images`가 rag-service·governance-worker 이미지를 다시 빌드한다.

## 테스트

```bash
docker run -d --rm --name shared-test-redis -p 127.0.0.1:56379:6379 redis:7.4-alpine
pip install -e ".[web,test]"
TEST_REDIS_URL=redis://127.0.0.1:56379/15 python -m pytest
docker rm -f shared-test-redis
```

- 토큰 추정·point ID·대체 임베딩은 [골든 스냅샷](../../../contracts/indexing/golden/README.md)과 비교하고, payload·이벤트는 JSON 스키마로 검증한다.
- `streams` 테스트는 실제 Redis가 필요하다(fakeredis는 전달 횟수를 돌려주지 않는다). 로컬에서 `TEST_REDIS_URL`이 없으면 건너뛰고, CI(`CI` 환경 변수)에서는 실패한다.
