# 재순위화 API (reranker-service)

rag-service 가 하이브리드 검색 후보를 다시 매길 때 부른다. 서버는 Hugging Face TEI(Text Embeddings Inference) 그대로이므로
API 는 TEI 의 `/rerank`·`/info` 다. 아래는 그중 우리가 쓰는 부분과 소비자 규칙만 적는다.

- 생산자: `server/inference/reranker/Dockerfile` — TEI 공식 CPU 이미지(`1.9.4`) + 모델(리비전 고정)
- 소비자: `server/python/rag-service` `app/search/reranker.py` `HttpRerankClient`
- 인증: TEI `--api-key`(환경변수 `API_KEY` = `INTERNAL_API_KEY`). 요청에 `Authorization: Bearer <키>` 를 싣는다.
  키가 없거나 틀리면 401. `/health` 는 키 없이 열린다(모델 워밍업까지 끝나야 200)

## POST /rerank

요청

```json
{ "query": "재택근무는 일주일에 며칠까지 가능한가요?", "texts": ["문단 0", "문단 1", "문단 2"], "truncate": true }
```

| 필드 | 필수 | 설명 |
|---|---|---|
| `query` | O | 비어 있으면 안 된다 |
| `texts` | O | 1개 이상, `GET /info` 의 `max_client_batch_size`(이미지에서 64) 이하. 넘으면 422 |
| `truncate` | | 질문+문단이 `max_input_length`(512토큰)를 넘으면 자른다. rag-service 는 `true` 를 보낸다 |
| `raw_scores` | | 기본 `false` — 점수는 시그모이드를 거친 0~1 관련도. rag-service 는 보내지 않는다 |
| `return_text` | | 기본 `false`. `true` 면 각 항목에 `text` 를 싣는다 |

응답 200 — **점수 높은 순**으로 정렬한 목록. `index` 는 요청 `texts` 의 위치다.

```json
[ { "index": 2, "score": 0.93 }, { "index": 0, "score": 0.41 }, { "index": 1, "score": 0.02 } ]
```

오류는 TEI 형식 `{"error": "...", "error_type": "..."}` 이다: 빈 `texts` 400, 건수 초과 등 검증 실패 422, 키 불일치 401
(본문 없음), 과부하 429. rag-service 는 어느 것이든 `http_error` 로 보고 1차 검색 순서로 답한다.

## GET /info — 운영 중인 모델 확인

`served_model_name` 이 `<모델 ID>@<리비전>` 이다. `model_id` 는 이미지 안 경로(`/models/reranker`)다.

```json
{ "model_id": "/models/reranker", "served_model_name": "Xenova/bge-reranker-base@280bcc27a84e0b898c251e06fddb25171bd9b101",
  "model_type": { "reranker": { ... } }, "max_input_length": 512, "max_client_batch_size": 64, "version": "1.9.4", ... }
```

## 모델 교체

`server/inference/reranker/Dockerfile` 의 `RERANKER_MODEL`·`RERANKER_REVISION`·`RERANKER_ONNX_FILE` 을 바꾸고 `evaluation.rerank_eval` 로 잰다(CI 가 MRR 0.70 미만이면 막는다). rag-service 는 바꾸지 않는다.
단 **점수 분포가 모델마다 다르다** — rag-service 의 `EVIDENCE_GATE_RERANK_SCORE`(근거 판정 기준)를 새 모델로 다시 재서 맞춘다.

## 소비자 규칙 (rag-service)

- 응답의 모든 `index` 에 점수가 있어야 한다. 빠지거나 모양이 다르면 `bad_response` 로 보고 1차 검색 순서로 답한다.
- `RERANKER_TIMEOUT_SECONDS`(8초) 안에 답이 없으면 `timeout`, HTTP 오류는 `http_error` 로 보고 1차 검색 순서로 답한다.
  어느 경우든 `rerank_fallback_total{reason}` 이 오른다 — 0 이 아니면 재순위화 없이 답한 것이다.
