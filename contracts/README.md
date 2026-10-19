# 서비스 간 계약 (contracts)

서비스끼리 코드를 나누지 않고도 서로 어긋나지 않도록, 주고받는 데이터의 규칙을 한곳에 둔다.
Java(Spring)와 Python 워커가 섞여 있어 공용 라이브러리로 묶을 수 없는 경계가 많다 — 그 경계를
문서·스키마·스냅샷으로 고정하고 테스트로 지킨다.

## 목록

| 파일 | 계약 | 생산자 | 소비자 |
|---|---|---|---|
| [`indexing/README.md`](indexing/README.md) | 원문 선택·파싱·청킹·청크 헤더·태그·토큰 추정·point ID·scope_key·임베딩·저장·작업 관리 | index-worker | rag-service, chat-service, governance-worker, auth-service(운영 현황) |
| [`indexing/golden/`](indexing/golden/README.md) | 위 규칙의 입력과 기대 출력 스냅샷 | index-worker | `server/python/index-worker/tests/test_golden.py` |
| [`qdrant-chunk-payload.schema.json`](qdrant-chunk-payload.schema.json) | Qdrant `document_chunks` 컬렉션의 point payload | index-worker | rag-service `vector_retriever`, governance-worker `vector_retriever` |
| [`events/document-events.schema.json`](events/document-events.schema.json) | Redis Stream `stream:document-events` 항목 | document-service `OutboxPublisher` | index-worker |
| [`events/index-events.schema.json`](events/index-events.schema.json) | Redis Stream `stream:index-events`의 `DOCUMENT_INDEXED`·`DOCUMENT_UNINDEXED` | index-worker | governance-worker |
| [`reranker/README.md`](reranker/README.md) | 재순위화 API `POST /rerank`·`GET /info` (Hugging Face TEI 와 같은 모양) | reranker-service | rag-service |
| [`openapi/`](openapi/README.md) | 각 Spring 서비스가 실제로 여는 HTTP API (springdoc 생성) | auth/document/chat-service | front, 서비스 간 호출 |

## 작성 규칙

- 계약에는 코드가 **지금 실제로 하는 일**을 적는다. 고쳐야 할 동작은 지우지 않고 "알려진 동작과 한계"로 따로 적는다.
- 스키마 파일은 JSON Schema 2020-12로 쓴다.

## 변경 절차

1. 계약을 바꾸는 변경은 이 폴더와 생산자·소비자 코드를 **함께** 바꾼다. 한쪽만 바꾸면 컴파일은 되지만 실행 중에 조용히 깨진다.
2. 필드 추가는 호환 변경이다(소비자는 모르는 필드를 무시해야 한다).
3. 필드 삭제·이름 변경·의미 변경은 비호환 변경이다. 소비자가 새 형식을 먼저 받아들이게 한 뒤 생산자를 바꾼다.
4. 청킹 규칙을 바꾸면 골든 스냅샷을 다시 만들고 차이를 리뷰한다([golden/README.md](indexing/golden/README.md#다시-만들기)). 청크가 바뀌면 기존 색인을 일괄 재색인해야 한다.

## 검증

| 대상 | 방법 | 자동화 |
|---|---|---|
| 골든 스냅샷 | `server/python/index-worker`의 `test_golden.py`가 스냅샷과 파싱·청킹·point ID 를 비교 | ci-python |
| HTTP API 스펙 | 서비스별 `OpenApiSnapshotTest`가 springdoc 출력과 `openapi/<서비스>.yml`을 비교. 다르면 로컬에서는 파일을 새로 쓰고 실패시켜 diff 로 보여주고, CI(`CI=true`)에서는 고치지 않고 실패시킨다 | ci-spring |
| 이벤트·payload 스키마 | 소비자 테스트가 스키마로 검증(`history_shared` 테스트) | ci-python |
