package com.odh.chat.timeline;

import com.odh.common.PermissionScope;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/**
 * 의사결정 타임라인.
 *
 * 사건은 문서에서 파생한다. 작성일 기준으로 정렬하고 결정 상태를 함께 준다.
 * 최신 문서를 최종 결론으로 단정하지 않는다. 시간 순서와 상태를 보여줄 뿐이고
 * 어느 결정이 유효한지는 사용자가 출처를 보고 판단한다.
 */
@Service
public class TimelineService {

    /** 상충 분석 캐시 유효 시간. 문서가 자주 바뀌지 않아 하루면 충분하다. */
    private static final Duration CONFLICT_TTL = Duration.ofHours(24);
    private static final int MAX_EVENTS = 30;

    private final JdbcClient jdbc;
    private final ConflictDetector detector;

    public TimelineService(JdbcClient jdbc, ConflictDetector detector) {
        this.jdbc = jdbc;
        this.detector = detector;
    }

    // @Transactional을 붙이지 않는다 — conflictsOf가 캐시 미스일 때 ConflictDetector.detect가
    // 문서 쌍마다 동기로 LLM을 호출하는데, 트랜잭션으로 묶으면 그동안 DB 커넥션을 쥐고
    // 있게 된다. 아래 조회·쓰기는 서로 원자성이 필요 없어 각자 짧은 커넥션으로 끝내는 게 낫다
    public TimelineDtos.Response timelineOf(String topic, PermissionScope scope) {
        List<TimelineSource> sources = findByTopic(topic, scope);

        List<TimelineDtos.Event> events = sources.stream()
                .map(s -> new TimelineDtos.Event(
                        s.documentId(), s.title(), s.documentType(), s.authoredAt(),
                        // 결정 상태를 판별할 수 없으면 미확정으로 표시한다
                        s.decisionStatus() == null ? "UNDECIDED" : s.decisionStatus(),
                        s.projectName(), s.decisionExcerpt(), s.fileId()))
                .toList();

        return new TimelineDtos.Response(topic, events, conflictsOf(topic, scope, sources));
    }

    /**
     * 주제와 관련된 문서를 찾는다.
     * 제목·프로젝트명·본문을 함께 본다. 프로젝트명이 있으면 같은 프로젝트 문서가 묶인다.
     */
    private List<TimelineSource> findByTopic(String topic, PermissionScope scope) {
        if (scope.isEmpty()) {
            return List.of();
        }

        StringBuilder where = new StringBuilder("""
                d.deleted_at IS NULL
                AND (d.title ILIKE :pattern OR d.project_name ILIKE :pattern OR d.content ILIKE :pattern)
                """);
        Map<String, Object> params = new HashMap<>();
        params.put("pattern", "%" + topic.strip() + "%");
        params.put("limit", MAX_EVENTS);

        if (!scope.systemAdmin()) {
            where.append(" AND (d.organization_id IN (:orgIds) OR d.team_id IN (:teamIds) OR d.id IN (:docIds))");
            params.put("orgIds", orEmpty(scope.organizationIds()));
            params.put("teamIds", orEmpty(scope.teamIds()));
            params.put("docIds", orEmpty(scope.documentIds()));
        }
        if (!scope.deniedDocumentIds().isEmpty()) {
            where.append(" AND d.id NOT IN (:deniedIds)");
            params.put("deniedIds", scope.deniedDocumentIds());
        }

        String sql = """
                SELECT d.id, d.title, d.document_type::text AS document_type, d.authored_at,
                       d.decision_status::text AS decision_status, d.project_name,
                       (SELECT c.content FROM indexing.document_chunk c
                         WHERE c.document_id = d.id
                           AND c.document_version = d.current_version
                           AND c.chunk_tag = 'DECISION'
                         ORDER BY c.chunk_no LIMIT 1) AS decision_excerpt,
                       (SELECT f.id FROM document.document_file f
                         WHERE f.document_id = d.id AND f.version = d.current_version) AS file_id
                FROM document.document d
                WHERE %s
                ORDER BY d.authored_at, d.id
                LIMIT :limit
                """.formatted(where);

        var spec = jdbc.sql(sql);
        for (var entry : params.entrySet()) {
            spec = spec.param(entry.getKey(), entry.getValue());
        }
        return spec.query((rs, i) -> new TimelineSource(
                rs.getLong("id"),
                rs.getString("title"),
                rs.getString("document_type"),
                rs.getDate("authored_at") == null ? null : rs.getDate("authored_at").toString(),
                rs.getString("decision_status"),
                rs.getString("project_name"),
                shorten(rs.getString("decision_excerpt")),
                (Long) rs.getObject("file_id"))).list();
    }

    /** 판정한 적 없다는 것과 "판정했는데 상충 0건"을 구분하는 sentinel. 실제 문서 ID로 나올 수 없다 */
    private static final long NO_CONFLICT_MARKER = -1L;

    /** 캐시가 살아 있으면 그대로 쓰고, 없으면 판정해 넣는다. */
    private List<TimelineDtos.Conflict> conflictsOf(String topic, PermissionScope scope,
                                                    List<TimelineSource> sources) {
        String normalized = normalize(topic);
        String scopeHash = hash(String.join(",", scope.toScopeKeys()));

        List<TimelineDtos.Conflict> cached = readCache(normalized, scopeHash, sources);
        if (cached != null) {
            return cached;
        }

        List<TimelineDtos.Conflict> detected = detector.detect(sources);
        writeCache(normalized, scopeHash, detected);
        return detected;
    }

    /**
     * @return 캐시에 이 topic+scope로 판정한 기록이 아예 없으면 null(=판정해야 한다).
     *         판정 기록이 있으면 리스트를 돌려준다(상충이 0건이면 빈 리스트 — 그래도 "캐시 있음"이다).
     *         0건을 구분하지 않으면 상충 없는 주제는 요청마다 동기 LLM 판정을 다시 하게 된다.
     */
    private List<TimelineDtos.Conflict> readCache(String normalized, String scopeHash,
                                                  List<TimelineSource> sources) {
        Map<Long, TimelineSource> byId = new HashMap<>();
        sources.forEach(s -> byId.put(s.documentId(), s));

        List<TimelineDtos.Conflict> rows = jdbc.sql("""
                        SELECT document_a_id, document_b_id, difference
                        FROM chat.timeline_conflict
                        WHERE topic_normalized = :topic AND scope_hash = :scopeHash AND expires_at > now()
                        """)
                .param("topic", normalized).param("scopeHash", scopeHash)
                .query((rs, i) -> {
                    long aId = rs.getLong("document_a_id");
                    if (aId == NO_CONFLICT_MARKER) {
                        return null;   // sentinel — 상충 0건으로 이미 판정됨, 표시할 내용은 없다
                    }
                    TimelineSource a = byId.get(aId);
                    TimelineSource b = byId.get(rs.getLong("document_b_id"));
                    if (a == null || b == null) {
                        return null;   // 캐시 이후 문서가 사라졌거나 권한이 바뀌었다
                    }
                    return new TimelineDtos.Conflict(
                            a.documentId(), a.title(), a.authoredAt(), a.decisionStatus(),
                            b.documentId(), b.title(), b.authoredAt(), b.decisionStatus(),
                            rs.getString("difference"));
                })
                .list();

        if (rows.isEmpty()) {
            return null;
        }
        return rows.stream().filter(Objects::nonNull).toList();
    }

    private void writeCache(String normalized, String scopeHash, List<TimelineDtos.Conflict> conflicts) {
        Instant expiresAt = Instant.now().plus(CONFLICT_TTL);
        if (conflicts.isEmpty()) {
            jdbc.sql("""
                            INSERT INTO chat.timeline_conflict
                                (topic_normalized, scope_hash, document_a_id, document_b_id, difference, expires_at)
                            VALUES (:topic, :scopeHash, :marker, :marker, '', :expiresAt)
                            """)
                    .param("topic", normalized).param("scopeHash", scopeHash)
                    .param("marker", NO_CONFLICT_MARKER)
                    .param("expiresAt", java.sql.Timestamp.from(expiresAt))
                    .update();
            return;
        }
        for (TimelineDtos.Conflict conflict : conflicts) {
            jdbc.sql("""
                            INSERT INTO chat.timeline_conflict
                                (topic_normalized, scope_hash, document_a_id, document_b_id, difference, expires_at)
                            VALUES (:topic, :scopeHash, :aId, :bId, :difference, :expiresAt)
                            """)
                    .param("topic", normalized).param("scopeHash", scopeHash)
                    .param("aId", conflict.documentAId()).param("bId", conflict.documentBId())
                    .param("difference", conflict.difference())
                    .param("expiresAt", java.sql.Timestamp.from(expiresAt))
                    .update();
        }
    }

    /** 결정 대목은 화면과 프롬프트에 쓸 만큼만 남긴다. 청크 헤더는 걷어낸다. */
    private String shorten(String excerpt) {
        if (excerpt == null) return null;
        String body = excerpt.replaceFirst("(?s)^\\[문서].*?\\n\\n", "").strip();
        return body.length() > 200 ? body.substring(0, 200) + "…" : body;
    }

    private String normalize(String topic) {
        return topic.toLowerCase().replaceAll("\\s+", " ").strip();
    }

    private String hash(String value) {
        try {
            return HexFormat.of().formatHex(
                    MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception e) {
            return String.format("%064d", Math.abs(value.hashCode()));
        }
    }

    /** IN 절에 빈 목록을 넣으면 SQL 이 깨진다. 절대 맞지 않는 값을 넣어 조건을 비운다. */
    private List<Long> orEmpty(java.util.Set<Long> ids) {
        return ids.isEmpty() ? List.of(-1L) : List.copyOf(ids);
    }
}
