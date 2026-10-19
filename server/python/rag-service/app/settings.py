"""환경변수 설정. application.yml(chat-service)의 키와 1:1로 대응한다.

Postgres·Redis·Qdrant·임베딩(Gemini) 설정은 history_shared.config.ServiceSettings 에서 물려받는다.
임베딩 값은 index-worker 와 반드시 같아야 검색이 성립한다.
Postgres 스키마는 매 쿼리에서 명시적으로 붙인다(chat./indexing./document.) — JDBC 의 currentSchema 검색 경로에 기대지 않는다.
"""
from history_shared.config import ServiceSettings
from pydantic import model_validator

from app.generation.prompt import PromptBudget
from app.search.relevance_gate import EvidenceGate


class Settings(ServiceSettings):
    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"
    answer_jobs_topic: str = "chat.answer.jobs"
    answer_jobs_dlt_topic: str = "chat.answer.jobs.DLT"
    # rag-service 전용 그룹 이름을 쓴다. 할당 전략이 다른 클라이언트(Spring Kafka 의
    # RangeAssignor)가 같은 그룹을 쓴 적이 있으면 그 메타데이터가 __consumer_offsets 에
    # 남아, aiokafka(RoundRobinAssignor)의 join 이 InconsistentGroupProtocolError 로 거부된다.
    kafka_consumer_group: str = "rag-answer-workers"
    kafka_dlt_consumer_group: str = "rag-answer-workers-dlt"
    # 메타데이터 재조회 주기. 컨슈머가 토픽을 "리더 없음" 상태로 본 채 그룹에 들어가면 다음 재조회까지
    # 파티션을 받지 못한다 — aiokafka 기본값(300,000ms)이면 콜드 스타트 첫 답변이 정확히 5분 늦는다.
    # 짧게 둬 최악의 지연을 이 값으로 묶는다(재조회 요청은 가볍다)
    kafka_metadata_max_age_ms: int = 30_000

    # 내부 서비스 인증
    internal_api_key: str = ""
    auth_service_url: str = "http://localhost:8081"
    # 권한 범위 캐시. TTL 은 권한 변경이 답변에 반영되기까지의 최대 지연이자 auth-service 호출 빈도다.
    # 이 이상 서로 다른 사용자가 동시에 뜨겁게 조회될 일은 없다 — 넘치면 오래된 것부터 밀려난다
    permission_cache_ttl_seconds: float = 10.0
    permission_cache_max_entries: int = 10_000

    # 검색. 후보는 벡터·키워드 검색이 각각 가져오고 병합 뒤 남기는 수다(리랭커가 켜져 있으면 전부 리랭커로 간다).
    # 근거 수는 리랭커가 남기는 수이자 프롬프트에 넣는 최대 수다 — 둘이 다르면 큰 쪽이 조용히 잘린다
    search_candidates: int = 20
    evidence_count: int = 5
    # 키워드 일치가 전혀 없고 벡터로만 걸린 청크는 코사인 유사도가 이 미만이면 버린다
    search_min_vector_similarity: float = 0.5

    # 근거 판정(app/search/relevance_gate.py). 점수가 기준을 넘는 청크가 하나라도 있으면 통과, 없으면 질문 낱말 겹침 비율로 본다
    # 리랭커 점수 기준은 모델마다 평가셋으로 다시 잰다(python -m evaluation.retrieval_eval 의 gate_sweep).
    # 현재 평가셋 기준: 0.003~0.5 사이에서는 결과가 같다 — 판정 결과를 가르는 것은 낱말 겹침 갈래다
    evidence_gate_rerank_score: float = 0.01
    evidence_gate_vector_score: float = 0.65
    evidence_gate_min_coverage: float = 0.34
    # 검색 질의 계획(app/search/query_planner.py, graph plan_query 노드). 경량 모델 한 번으로 대화 맥락 재작성·
    # 갈래(simple/complex/abstract)·하위 질의를 받는다. 첫 질문에도 호출이 생기므로(약 300토큰) 한도가 빠듯하면 끈다 —
    # 끄면 맥락이 있을 때만 재작성하고 원래 질문 하나로 검색한다(HyDE·하위 질의 없음)
    query_planner_enabled: bool = True

    # 근거 충분성 판정(app/search/evidence_judge.py, graph judge_evidence 노드).
    # ① 근거의 벡터 유사도 최댓값이 evidence_min_vector_similarity 미만이면 바로 거절(LLM 을 부르지 않는다)
    # ② 나머지는 경량 모델(fallback_model)에게 근거에 답이 있는지 묻는다
    # ③ 판정기가 꺼져 있거나 판정에 실패하면 위 낱말·점수 1차 거름(EVIDENCE_GATE_*)으로 정한다
    # 0.62 는 평가셋 dev 에서 잰 값이다: 답 있는 질문 최저 0.651, 그 아래는 모두 답 없는 질문(0.561~0.614 는 확실히 거절)
    evidence_judge_enabled: bool = True
    evidence_min_vector_similarity: float = 0.62
    # 판정 한 번의 입력. 근거 3건 × 400자면 약 500토큰이다(Groq 무료: 경량 모델 하루 200,000토큰을 HyDE·분해·자가비판과 나눠 쓴다)
    evidence_judge_max_evidence: int = 3
    evidence_judge_max_chars: int = 400
    # 같은 질문·같은 근거 청크(문서 판 포함)의 판정을 다시 묻지 않는다
    evidence_judge_cache_ttl_seconds: int = 86_400

    # 보안 규칙(app/security/rules/injection.yaml·secrets.yaml)을 덮어쓸 디렉터리. 비우면 이미지에 든 기본 규칙
    security_rules_path: str = ""

    # 리랭커 (Cross-Encoder 2차 재순위화). 추론은 reranker-service 가 하고(TEI /rerank 와 같은 API), 모델도 거기서 정한다.
    # 호출이 실패하거나 이 시간 안에 답이 없으면 1차 검색 순서로 답한다(rerank_fallback_total)
    reranker_enabled: bool = True
    reranker_url: str = "http://localhost:8090"
    # reranker-service(CPU 1개) 후보 20건 추론이 약 2~3초다(관측치). 넘기면 재순위화 없이 답한다
    reranker_timeout_seconds: float = 8.0
    reranker_min_score: float = 0.0
    # 최종 순위 = 리랭커 점수 × weight + 1차 검색 순위 점수 × (1 - weight).
    # 리랭커 점수 폭(로짓 기준)이 saturation_spread 미만이면(포화, 변별력 없음) saturated_weight 를 쓴다
    rerank_weight: float = 0.7
    rerank_saturated_weight: float = 0.3
    rerank_saturation_spread: float = 1.0

    # 생성(Groq) — 비어 있으면 근거 발췌 방식으로 답한다
    groq_api_key: str = ""
    groq_endpoint: str = "https://api.groq.com"
    primary_model: str = "openai/gpt-oss-120b"
    fallback_model: str = "openai/gpt-oss-20b"
    max_output_tokens: int = 1500
    # 경량 호출(fallback_model)의 추론 강도. gpt-oss 같은 추론 모델은 추론 토큰도 max_tokens 에서
    # 깎이므로 낮춰야 본문이 남는다. 추론 모델이 아니면(이 인자를 거부하면) 빈 값으로 끈다
    light_reasoning_effort: str = "low"
    # 경량 호출의 temperature. 질의 계획 갈래·근거 판정처럼 분류에 쓰는 호출이 많아, 같은 질문에 다른 답이 나오면
    # 검색 경로와 판정이 흔들린다. 0.2 에서는 같은 근거 판정 11건을 두 번 물어 정확도가 0.818 / 0.727 로 갈렸고,
    # 0 에서는 두 번 모두 같은 판정이 나왔다. 질의 계획 갈래 흔들림은 evaluation.plan_eval 로 잰다
    light_temperature: float = 0.0

    # 프롬프트 토큰 예산(app/generation/prompt.py). 넘치면 순위가 낮은 근거부터 덜어낸다
    prompt_system_budget: int = 400
    prompt_summary_budget: int = 300
    prompt_recent_turns_budget: int = 600
    prompt_recent_turn_budget: int = 200
    prompt_evidence_budget: int = 2500
    prompt_question_budget: int = 100
    # 요청 하나(입력 예산 합 + max_output_tokens)가 넘으면 안 되는 한도. Groq 무료 티어는 분당 8,000 토큰(TPM)이라
    # 요청 하나가 이를 넘으면 413 으로 막힌다
    llm_request_token_limit: int = 8000

    # 자가 비판 신뢰도가 이 미만이면 답변 상태를 LOW_CONFIDENCE 로 낮춘다
    critique_min_confidence: float = 0.7

    # 대화 메모리. 최근 N턴을 맥락으로 쓰고, 대화가 M턴을 넘으면 그 이전을 요약해 캐시한다
    memory_recent_turns: int = 4
    memory_summary_after_turns: int = 5
    memory_summary_ttl_seconds: int = 7 * 24 * 3600
    # 권한 범위별 답변 캐시 수명
    answer_cache_ttl_seconds: int = 3600

    # GraphRAG (Neo4j 지식 그래프 결합 검색)
    graphrag_enabled: bool = True
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password123"
    graphrag_max_hops: int = 2
    graphrag_max_subgraph_nodes: int = 25

    @model_validator(mode="after")
    def _check_tuning(self) -> "Settings":
        if not 0 < self.evidence_count <= self.search_candidates:
            raise ValueError(
                f"evidence_count({self.evidence_count}) 는 1 이상, search_candidates({self.search_candidates}) 이하여야 한다"
            )
        for name in ("rerank_weight", "rerank_saturated_weight", "critique_min_confidence",
                     "evidence_gate_min_coverage", "search_min_vector_similarity",
                     "evidence_min_vector_similarity"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} 는 0~1 이어야 한다: {value}")
        total = self.prompt_budget().input_total() + self.max_output_tokens
        if total > self.llm_request_token_limit:
            raise ValueError(
                f"프롬프트 예산 합({total} = 입력 {self.prompt_budget().input_total()} + 출력 {self.max_output_tokens})이 "
                f"llm_request_token_limit({self.llm_request_token_limit}) 를 넘는다"
            )
        return self

    def prompt_budget(self) -> PromptBudget:
        return PromptBudget(
            system=self.prompt_system_budget,
            summary=self.prompt_summary_budget,
            recent_turns=self.prompt_recent_turns_budget,
            recent_turn=self.prompt_recent_turn_budget,
            evidence=self.prompt_evidence_budget,
            question=self.prompt_question_budget,
            evidence_count=self.evidence_count,
        )

    def evidence_gate(self) -> EvidenceGate:
        return EvidenceGate(
            rerank_score=self.evidence_gate_rerank_score,
            vector_score=self.evidence_gate_vector_score,
            min_coverage=self.evidence_gate_min_coverage,
        )

    @property
    def has_groq_key(self) -> bool:
        return bool(self.groq_api_key and self.groq_api_key.strip())


settings = Settings()
