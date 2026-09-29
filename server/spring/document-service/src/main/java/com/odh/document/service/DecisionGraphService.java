package com.odh.document.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.odh.common.PermissionScope;
import com.odh.document.scope.ScopePredicate;
import com.odh.document.web.DecisionGraphDtos.GraphEdge;
import com.odh.document.web.DecisionGraphDtos.GraphNode;
import com.odh.document.web.DecisionGraphDtos.GraphResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.client.RestClient;

import java.nio.charset.StandardCharsets;
import java.util.*;

@Service
@Transactional(readOnly = true)
public class DecisionGraphService {

    private static final Logger log = LoggerFactory.getLogger(DecisionGraphService.class);

    private final JdbcClient jdbc;
    private final RestClient restClient;
    private final ObjectMapper objectMapper;
    private final String neo4jHttpUrl;
    private final String neo4jAuth;

    public DecisionGraphService(
            JdbcClient jdbc,
            ObjectMapper objectMapper,
            @Value("${neo4j.http-url:http://neo4j:7474}") String neo4jHttpUrl,
            @Value("${neo4j.user:neo4j}") String neo4jUser,
            @Value("${neo4j.password:password123}") String neo4jPassword) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
        this.neo4jHttpUrl = neo4jHttpUrl.replaceAll("/+$", "");
        String credentials = neo4jUser + ":" + neo4jPassword;
        this.neo4jAuth = "Basic " + Base64.getEncoder().encodeToString(credentials.getBytes(StandardCharsets.UTF_8));
        this.restClient = RestClient.builder().build();
    }

    /**
     * @param scope 요청자의 권한 범위. 범위 밖 문서 ID는 존재하지 않는 것과 구분하지 않고
     *              조용히 걸러낸다(문서 조회와 같은 정책). 상충 엣지는 양쪽 문서가 모두
     *              범위 안에 있을 때만 반환한다 — 한쪽만 범위 밖이어도 상대 문서의 제목·
     *              상충 사유가 새어 나가기 때문이다.
     */
    public GraphResponse getGraphForDocuments(List<Long> documentIds, PermissionScope scope) {
        if (documentIds == null || documentIds.isEmpty()) {
            return new GraphResponse(List.of(), List.of());
        }

        List<Long> visibleIds = visibleDocumentIds(documentIds, scope);
        if (visibleIds.isEmpty()) {
            return new GraphResponse(List.of(), List.of());
        }

        Map<String, GraphNode> nodeMap = new LinkedHashMap<>();
        Map<String, GraphEdge> edgeMap = new LinkedHashMap<>();

        // 1. PostgreSQL에서 기본 문서 노드 및 상충(Conflict) 엣지 로드
        loadFromPostgreSQL(visibleIds, scope, nodeMap, edgeMap);

        // 2. Neo4j에서 인과/종속/대체 관계망 서브그래프 로드. 씨앗 ID를 이미 범위로
        // 걸러뒀으므로 여기서 시작하는 순회도 범위 밖 문서를 노출하지 않는다.
        // 순회는 seed에서 딱 1-hop만 나간다(OPTIONAL MATCH (d)-[r]-(target), 가변
        // 길이 아님) — 그 target이 또 다른 Decision 문서일 수 있으므로, 새로 발견된
        // Decision 노드는 다시 Postgres 스코프로 걸러낸다(아래 filterUnauthorizedDecisions)
        loadFromNeo4j(visibleIds, nodeMap, edgeMap);
        filterUnauthorizedDecisions(visibleIds, scope, nodeMap, edgeMap);

        return new GraphResponse(new ArrayList<>(nodeMap.values()), new ArrayList<>(edgeMap.values()));
    }

    /** 요청한 문서 ID 중 지금 권한 범위 안에 있는 것만 남긴다. */
    private List<Long> visibleDocumentIds(List<Long> docIds, PermissionScope scope) {
        ScopePredicate.Clause allow = ScopePredicate.of(scope, "d");
        var spec = jdbc.sql("""
                SELECT d.id FROM document d
                WHERE d.id IN (:docIds) AND d.deleted_at IS NULL AND %s
                """.formatted(allow.sql()))
                .param("docIds", docIds);
        allow.params().forEach(spec::param);
        return spec.query(Long.class).list();
    }

    /**
     * Neo4j 순회로 새로 등장한 Decision 노드 중 seed로 이미 검증한 문서가 아닌 것들을
     * 다시 Postgres 스코프로 확인해, 범위 밖이면 노드·관련 엣지를 통째로 제거한다.
     */
    private void filterUnauthorizedDecisions(
            List<Long> seedIds, PermissionScope scope, Map<String, GraphNode> nodeMap, Map<String, GraphEdge> edgeMap
    ) {
        Set<Long> seedSet = new HashSet<>(seedIds);
        List<Long> extraDecisionIds = new ArrayList<>();
        for (GraphNode node : nodeMap.values()) {
            if (!"Decision".equals(node.label())) continue;
            try {
                Long id = Long.valueOf(node.id());
                if (!seedSet.contains(id)) extraDecisionIds.add(id);
            } catch (NumberFormatException ignored) {
                // Neo4j에서 id 없는 대상은 Decision 라벨을 붙이지 않으므로 여기 안 옴
            }
        }
        if (extraDecisionIds.isEmpty()) return;

        Set<String> allowedExtra = new HashSet<>();
        visibleDocumentIds(extraDecisionIds, scope).forEach(id -> allowedExtra.add(String.valueOf(id)));

        Set<String> deniedIds = new HashSet<>();
        for (Long id : extraDecisionIds) {
            String key = String.valueOf(id);
            if (!allowedExtra.contains(key)) {
                deniedIds.add(key);
                nodeMap.remove(key);
            }
        }
        if (deniedIds.isEmpty()) return;

        edgeMap.values().removeIf(e -> deniedIds.contains(e.source()) || deniedIds.contains(e.target()));
    }

    private void loadFromPostgreSQL(
            List<Long> docIds, PermissionScope scope, Map<String, GraphNode> nodeMap, Map<String, GraphEdge> edgeMap
    ) {
        try {
            // 문서 기본 메타데이터 조회. docIds는 이미 범위로 걸러진 상태다
            String docSql = """
                    SELECT id, title, decision_status, authored_at, document_type
                    FROM document
                    WHERE id IN (:docIds) AND deleted_at IS NULL
                    """;
            List<Map<String, Object>> rows = jdbc.sql(docSql)
                    .param("docIds", docIds)
                    .query().listOfRows();

            for (Map<String, Object> r : rows) {
                Long id = ((Number) r.get("id")).longValue();
                String title = (String) r.get("title");
                String status = r.get("decision_status") != null ? r.get("decision_status").toString() : "CURRENT";
                String authoredAt = r.get("authored_at") != null ? r.get("authored_at").toString() : "";
                String docType = r.get("document_type") != null ? r.get("document_type").toString() : "POLICY_DOC";

                Map<String, Object> props = new HashMap<>();
                props.put("docId", id);
                props.put("status", status);
                props.put("authoredAt", authoredAt);
                props.put("documentType", docType);

                nodeMap.put(String.valueOf(id), new GraphNode(
                        String.valueOf(id),
                        "Decision",
                        title,
                        props
                ));
            }

            // 상충(Conflict) 관계 조회. source/target 문서가 둘 다 범위 안에 있어야 반환한다
            // — 한쪽만 범위 밖이어도 그 문서의 제목·상충 사유가 새어 나가기 때문이다
            // 상태값은 OPEN/RESOLVED/DISMISSED(ConflictController)와 SUPERSEDED(문서 개정 시
            // governance-worker 가 자동으로 닫는 값)다. DISMISSED 는 사람이 상충이 아니라고 판정한 것이라 그리지 않는다.
            // SUPERSEDED는 개정 전 판을 보고 내린 판정이라 그래프에 그리면 유령 간선이 된다.
            ScopePredicate.Clause sourceAllow = ScopePredicate.of(scope, "sd");
            ScopePredicate.Clause targetAllow = ScopePredicate.of(scope, "td");
            String conflictSql = """
                    SELECT c.source_doc_id, sd.title AS source_title, sd.decision_status AS source_status,
                           c.target_doc_id, td.title AS target_title, td.decision_status AS target_status,
                           c.conflict_type, c.severity_score, c.reasoning
                    FROM decision_conflicts c
                    JOIN document sd ON c.source_doc_id = sd.id
                    JOIN document td ON c.target_doc_id = td.id
                    WHERE (c.source_doc_id IN (:docIds) OR c.target_doc_id IN (:docIds))
                      AND c.status NOT IN ('DISMISSED', 'SUPERSEDED')
                      AND sd.deleted_at IS NULL AND td.deleted_at IS NULL
                      AND %s AND %s
                    """.formatted(sourceAllow.sql(), targetAllow.sql());
            var conflictSpec = jdbc.sql(conflictSql).param("docIds", docIds);
            sourceAllow.params().forEach(conflictSpec::param);
            targetAllow.params().forEach(conflictSpec::param);
            List<Map<String, Object>> conflictRows = conflictSpec.query().listOfRows();

            for (Map<String, Object> cr : conflictRows) {
                Long sId = ((Number) cr.get("source_doc_id")).longValue();
                Long tId = ((Number) cr.get("target_doc_id")).longValue();
                String sTitle = (String) cr.get("source_title");
                String tTitle = (String) cr.get("target_title");
                String sStatus = cr.get("source_status") != null ? cr.get("source_status").toString() : "CURRENT";
                String tStatus = cr.get("target_status") != null ? cr.get("target_status").toString() : "CURRENT";
                String conflictType = (String) cr.get("conflict_type");
                double severity = cr.get("severity_score") != null ? ((Number) cr.get("severity_score")).doubleValue() : 0.8;
                String reasoning = (String) cr.get("reasoning");

                nodeMap.computeIfAbsent(String.valueOf(sId), k -> new GraphNode(
                        String.valueOf(sId), "Decision", sTitle, Map.of("docId", sId, "status", sStatus)
                ));
                nodeMap.computeIfAbsent(String.valueOf(tId), k -> new GraphNode(
                        String.valueOf(tId), "Decision", tTitle, Map.of("docId", tId, "status", tStatus)
                ));

                String edgeKey = sId + "-CONFLICTS_WITH-" + tId;
                Map<String, Object> props = new HashMap<>();
                props.put("severityScore", severity);
                props.put("reasoning", reasoning);
                props.put("conflictType", conflictType);

                edgeMap.put(edgeKey, new GraphEdge(
                        String.valueOf(sId),
                        String.valueOf(tId),
                        "CONFLICTS_WITH",
                        "상충 (" + conflictType + ")",
                        props
                ));
            }
        } catch (Exception e) {
            log.warn("PostgreSQL 문서 그래프 로드 중 예외 발생: {}", e.getMessage());
        }
    }

    private void loadFromNeo4j(List<Long> docIds, Map<String, GraphNode> nodeMap, Map<String, GraphEdge> edgeMap) {
        try {
            String cypher = """
                    MATCH (d:Decision)
                    WHERE d.id IN $docIds
                    OPTIONAL MATCH (d)-[r]-(target)
                    RETURN d, type(r) AS relType, target, startNode(r) = d AS isOutgoing
                    LIMIT 100
                    """;

            Map<String, Object> payload = Map.of(
                    "statements", List.of(Map.of(
                            "statement", cypher,
                            "parameters", Map.of("docIds", docIds)
                    ))
            );

            String response = restClient.post()
                    .uri(neo4jHttpUrl + "/db/neo4j/tx/commit")
                    .header("Authorization", neo4jAuth)
                    .header("Content-Type", "application/json")
                    .body(payload)
                    .retrieve()
                    .body(String.class);

            if (response == null || response.isBlank()) return;

            JsonNode root = objectMapper.readTree(response);
            JsonNode dataArray = root.path("results").path(0).path("data");

            for (JsonNode row : dataArray) {
                JsonNode dNode = row.path("row").get(0);
                String relType = row.path("row").get(1).asText(null);
                JsonNode targetNode = row.path("row").get(2);
                boolean isOutgoing = row.path("row").get(3).asBoolean(true);

                if (dNode != null && !dNode.isNull()) {
                    long docId = dNode.path("id").asLong();
                    String title = dNode.path("title").asText("문서 #" + docId);
                    String status = dNode.path("status").asText("CURRENT");
                    String authoredAt = dNode.path("authored_at").asText("");

                    nodeMap.computeIfAbsent(String.valueOf(docId), k -> new GraphNode(
                            String.valueOf(docId),
                            "Decision",
                            title,
                            Map.of("docId", docId, "status", status, "authoredAt", authoredAt)
                    ));
                }

                if (relType != null && targetNode != null && !targetNode.isNull()) {
                    String targetName = targetNode.has("name") ? targetNode.path("name").asText() :
                            (targetNode.has("title") ? targetNode.path("title").asText() : "엔티티");
                    String targetId = targetNode.has("id") ? String.valueOf(targetNode.path("id").asLong()) :
                            "entity-" + targetName.replaceAll("\\s+", "_");

                    final String nodeLabel = (targetNode.has("id")) ? "Decision" :
                            (targetName.contains("팀") || targetName.contains("본부")) ? "Team" :
                            (targetName.contains("시스템") || targetName.contains("게이트웨이") || targetName.contains("서버")) ? "System" : "Policy";

                    nodeMap.computeIfAbsent(targetId, k -> new GraphNode(
                            targetId,
                            nodeLabel,
                            targetName,
                            Map.of("label", nodeLabel)
                    ));

                    String sourceId = isOutgoing ? String.valueOf(dNode.path("id").asLong()) : targetId;
                    String destId = isOutgoing ? targetId : String.valueOf(dNode.path("id").asLong());

                    String edgeKey = sourceId + "-" + relType + "-" + destId;
                    edgeMap.putIfAbsent(edgeKey, new GraphEdge(
                            sourceId,
                            destId,
                            relType,
                            relType,
                            Map.of("relType", relType)
                    ));
                }
            }
        } catch (Exception e) {
            log.warn("Neo4j 지식 그래프 쿼리 중 오류(무시하고 DB 결과 반환): {}", e.getMessage());
        }
    }
}
