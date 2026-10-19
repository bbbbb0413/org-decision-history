"""의존성 조립. Spring의 @Configuration 빈 등록에 해당하는 부분을 한 곳에 모았다."""
import redis.asyncio as redis
from history_shared.db import create_pool
from history_shared.embeddings import LOCAL_MODEL_NAME, Embedder, create_embedder

from app.answer_cache import AnswerCache
from app.auth_client import AuthClient
from app.db import Db
from app.generation.factory import build_generator
from history_shared.graph.client import Neo4jGraphClient
from app.graph.build import build_graph
from app.graph.nodes import GraphNodes
from app.memory import ConversationMemory
from app.search.hybrid import HybridSearchService
from app.search.keyword_retriever import KeywordSearchClient
from app.search.evidence_judge import EvidenceJudge, JudgeCache
from app.search.query_planner import QueryPlanner
from app.security.content_validator import ContentValidator
from app.security.pii_scanner import SecretPiiScanner
from app.security.rule_loader import load_rules
from app.search.reranker import CrossEncoderReranker, HttpRerankClient
from app.search.source_resolver import SourceResolver
from app.search.vector_retriever import VectorSearchClient
from app.settings import Settings, settings


def similarity_floor(value: float, embedder: Embedder) -> float:
    """벡터 유사도 기준(검색 하한·근거 판정 하한)을 이 임베딩에 적용할지 정한다.

    기준값은 실제 임베딩 모델(Gemini)로 평가셋에서 잰 값이다. 키가 없을 때 쓰는 대체 임베딩(글자 3-gram 해시)은
    정답 문서와 질문 사이도 0.2~0.3 이라, 기준을 그대로 두면 모든 질문이 근거 없음으로 끝난다. 대체 임베딩이면
    기준을 끄고 리랭커·낱말 겹침 거름에 맡긴다.
    """
    return 0.0 if embedder.model_name == LOCAL_MODEL_NAME else value


class Dependencies:
    def __init__(self, cfg: Settings = settings):
        self.settings = cfg
        self.pool = None
        self.redis = None
        self.db = None
        self.auth = None
        self.embedder = None
        self.vector_client = None
        self.keyword_client = None
        self.hybrid_search = None
        self.source_resolver = None
        self.answer_cache = None
        self.generator = None
        self.memory = None
        self.query_planner = None
        self.graph = None
        self.graph_client = None
        self.rerank_client = None
        self.graph_extractor = None
        self.graph_sync = None

    async def start(self) -> "Dependencies":
        self.pool = await create_pool(self.settings)
        self.redis = redis.Redis(
            host=self.settings.redis_host,
            port=self.settings.redis_port,
            password=self.settings.redis_password or None,
            decode_responses=True,
        )
        self.db = Db(self.pool)
        self.auth = AuthClient(self.settings)
        # 키가 없으면 index-worker 와 같은 대체 임베딩을 쓴다(contracts/indexing/README.md §10)
        self.embedder = create_embedder(self.settings)
        self.vector_client = VectorSearchClient(self.settings)
        self.keyword_client = KeywordSearchClient(self.pool)
        self.hybrid_search = HybridSearchService(
            self.embedder,
            self.vector_client,
            self.keyword_client,
            candidates=self.settings.search_candidates,
            min_vector_similarity=similarity_floor(self.settings.search_min_vector_similarity, self.embedder),
        )
        self.source_resolver = SourceResolver(self.pool)
        self.answer_cache = AnswerCache(self.redis, ttl_seconds=self.settings.answer_cache_ttl_seconds)
        self.generator = build_generator(self.settings)
        self.memory = ConversationMemory(
            self.redis,
            self.generator,
            self.db,
            recent_turns=self.settings.memory_recent_turns,
            summary_after_turns=self.settings.memory_summary_after_turns,
            summary_ttl_seconds=self.settings.memory_summary_ttl_seconds,
        )
        self.rerank_client = HttpRerankClient(
            self.settings.reranker_url,
            api_key=self.settings.internal_api_key,
            timeout_seconds=self.settings.reranker_timeout_seconds,
        )
        self.reranker = CrossEncoderReranker(
            self.rerank_client,
            enabled=self.settings.reranker_enabled,
            weight=self.settings.rerank_weight,
            saturated_weight=self.settings.rerank_saturated_weight,
            saturation_spread=self.settings.rerank_saturation_spread,
        )

        if self.settings.graphrag_enabled:
            self.graph_client = Neo4jGraphClient(
                uri=self.settings.neo4j_uri,
                user=self.settings.neo4j_user,
                password=self.settings.neo4j_password,
            )
            await self.graph_client.connect()

        self.query_planner = QueryPlanner(self.generator) if self.settings.query_planner_enabled else None
        self.nodes = GraphNodes(
            db=self.db,
            auth=self.auth,
            hybrid_search=self.hybrid_search,
            source_resolver=self.source_resolver,
            answer_cache=self.answer_cache,
            memory=self.memory,
            generator=self.generator,
            pii_scanner=SecretPiiScanner(load_rules("secrets", self.settings.security_rules_path)),
            content_validator=ContentValidator(load_rules("injection", self.settings.security_rules_path)),
            reranker=self.reranker,
            candidate_count=self.settings.search_candidates,
            reranker_min_score=self.settings.reranker_min_score,
            prompt_budget=self.settings.prompt_budget(),
            evidence_gate=self.settings.evidence_gate(),
            critique_min_confidence=self.settings.critique_min_confidence,
            graph_client=self.graph_client,
            graphrag_enabled=self.settings.graphrag_enabled,
            graphrag_max_hops=self.settings.graphrag_max_hops,
            evidence_judge=(
                EvidenceJudge(
                    self.generator,
                    max_evidence=self.settings.evidence_judge_max_evidence,
                    max_chars=self.settings.evidence_judge_max_chars,
                    cache=JudgeCache(self.redis, ttl_seconds=self.settings.evidence_judge_cache_ttl_seconds),
                )
                if self.settings.evidence_judge_enabled
                else None
            ),
            min_vector_similarity=similarity_floor(self.settings.evidence_min_vector_similarity, self.embedder),
            query_planner=self.query_planner,
        )
        self.graph = build_graph(self.nodes)
        return self

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
        if self.redis:
            await self.redis.aclose()
        if self.auth:
            await self.auth.close()
        if self.embedder:
            await self.embedder.close()
        if self.vector_client:
            await self.vector_client.close()
        if self.graph_client:
            await self.graph_client.close()
        if self.rerank_client:
            await self.rerank_client.close()
