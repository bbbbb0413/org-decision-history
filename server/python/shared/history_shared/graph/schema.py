"""Neo4j 그래프 스키마 허용 목록.

라벨·관계 타입·키 필드는 Cypher에서 파라미터 바인딩이 안 돼(`$var`) 쿼리 문자열에
직접 넣어야 한다(client.py). LLM이 뽑아낸 값을 검증 없이 여기 쓰면 Cypher Injection이
된다 — 문서 본문에 프롬프트 인젝션을 심어 `rel_type`을 `AFFECTS}) DETACH DELETE n //`
같은 값으로 유도하면 임의 Cypher가 실행될 수 있다. 그래서 이 목록 밖의 값은 절대
통과시키지 않는다.
"""

NODE_LABELS = frozenset({"Decision", "System", "Team", "Policy"})

REL_TYPES = frozenset({
    "AFFECTS", "DEPRECATES", "SUPERSEDES", "DEPENDS_ON", "DECIDED_BY", "IMPLEMENTS",
    # LLM이 뽑지 않고 내부에서만 붙이는 관계(sync.py:sync_conflict_relation)도 같은
    # 경로(client.py의 f-string 삽입)를 타므로 여기 같이 둔다
    "CONFLICTS_WITH",
})

# 문서 본문에서 LLM 이 뽑아 만드는 관계. 다시 동기화할 때 이것만 지우고 새로 만든다(sync.py)
EXTRACTED_REL_TYPES = REL_TYPES - {"CONFLICTS_WITH"}

# MERGE (n:{label} {{key_field: $key_value}}) 의 key_field. 지금은 항상 "id" 또는
# "name"만 쓰지만(sync.py), 여기도 같은 이유로 허용 목록을 둔다
KEY_FIELDS = frozenset({"id", "name"})
