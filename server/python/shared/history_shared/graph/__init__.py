"""Decision 지식 그래프(Neo4j) 동기화·조회 공용 모듈.

rag-service(GraphRAG 조회)와 governance-worker(그래프 동기화)가 함께 쓴다. 라벨·관계 타입 허용 목록
(Cypher Injection 방어)을 한 곳에 두어 한쪽만 패치되는 일이 없게 한다.
"""
