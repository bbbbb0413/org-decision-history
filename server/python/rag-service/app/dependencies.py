"""의존성 조립. Spring의 @Configuration 빈 등록에 해당하는 부분을 한 곳에 모았다."""
import redis.asyncio as redis
from history_shared.db import create_pool
from history_shared.embeddings import create_embedder

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
from app.search.reranker import CrossEncoderReranker
from app.search.source_resolver import SourceResolver
from app.search.vector_retriever import VectorSearchClient
from app.settings import Settings, settings


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
        self.graph = None
        self.notification_dispatcher = None
        self.graph_client = None
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
        self.hybrid_search = HybridSearchService(self.embedder, self.vector_client, self.keyword_client)
        self.source_resolver = SourceResolver(self.pool)
        self.answer_cache = AnswerCache(self.redis)
        self.generator = build_generator(self.settings)
        self.memory = ConversationMemory(self.redis, self.generator, self.db)
        self.reranker = CrossEncoderReranker(
            model_name=self.settings.reranker_model,
            enabled=self.settings.reranker_enabled,
        )

        if self.settings.graphrag_enabled:
            self.graph_client = Neo4jGraphClient(
                uri=self.settings.neo4j_uri,
                user=self.settings.neo4j_user,
                password=self.settings.neo4j_password,
            )
            await self.graph_client.connect()

        self.nodes = GraphNodes(
            db=self.db,
            auth=self.auth,
            hybrid_search=self.hybrid_search,
            source_resolver=self.source_resolver,
            answer_cache=self.answer_cache,
            memory=self.memory,
            generator=self.generator,
            reranker=self.reranker,
            candidate_count=self.settings.reranker_candidates_limit,
            reranker_top_k=self.settings.reranker_top_k,
            reranker_min_score=self.settings.reranker_min_score,
            graph_client=self.graph_client,
            graphrag_enabled=self.settings.graphrag_enabled,
            graphrag_max_hops=self.settings.graphrag_max_hops,
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
