package com.odh.document.service;

import com.odh.common.PermissionScope;
import com.odh.common.web.PageRequest;
import com.odh.common.web.PageResponse;
import com.odh.document.scope.ScopePredicate;
import com.odh.document.web.ConflictDtos.ConflictItem;
import com.odh.document.web.ConflictDtos.ResolveConflictRequest;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.Array;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 상충 목록·상세·해소.
 *
 * source/target 문서가 둘 다 요청자의 권한 범위 안에 있을 때만 보여준다 —
 * 한쪽만 범위 밖이어도 상대 문서의 제목·조항·상충 사유가 새어 나가기 때문이다.
 * 없는 상충과 범위 밖 상충을 구분하지 않는다({@link ConflictNotFoundException}).
 */
@Service
@Transactional(readOnly = true)
public class ConflictQueryService {

    /** PATCH로 사용자가 직접 지정할 수 있는 상태값. 프론트(ConflictActionModal)가 주는 값과 같다 */
    private static final Set<String> ALLOWED_RESOLUTION_STATUSES = Set.of("RESOLVED", "DISMISSED");

    private final JdbcClient jdbc;

    public ConflictQueryService(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * @param authorId 이 사용자가 쓴 문서가 한쪽에라도 걸린 상충만.
     *                 null 이면 거르지 않는다. 내가 내린 결정이 뒤집혔는지 보는 뷰다.
     */
    public PageResponse<ConflictItem> listConflicts(String status, Long authorId,
                                                    int page, int size, PermissionScope scope) {
        PageRequest paging = PageRequest.of(page, size);

        ScopePredicate.Clause sourceAllow = ScopePredicate.of(scope, "sd");
        ScopePredicate.Clause targetAllow = ScopePredicate.of(scope, "td");

        StringBuilder where = new StringBuilder(
                "sd.deleted_at IS NULL AND td.deleted_at IS NULL AND ")
                .append(sourceAllow.sql()).append(" AND ").append(targetAllow.sql());
        Map<String, Object> params = new HashMap<>(sourceAllow.params());
        params.putAll(targetAllow.params());

        if (status != null && !status.isBlank()) {
            where.append(" AND c.status = :status");
            params.put("status", status.trim().toUpperCase());
        }
        if (authorId != null) {
            // 한쪽만 내 문서여도 보여준다 — 내 결정을 뒤집은 상대 문서를 찾는 것이 목적이다.
            where.append(" AND (sd.author_id = :authorId OR td.author_id = :authorId)");
            params.put("authorId", authorId);
        }

        String countSql = """
                SELECT count(*) FROM decision_conflicts c
                JOIN document sd ON c.source_doc_id = sd.id
                JOIN document td ON c.target_doc_id = td.id
                WHERE\s""" + where;
        var countSpec = jdbc.sql(countSql);
        params.forEach(countSpec::param);
        long total = countSpec.query(Long.class).single();

        String selectSql = """
                SELECT c.id, c.source_doc_id, sd.title AS source_title,
                       c.target_doc_id, td.title AS target_title,
                       c.source_clause, c.target_clause, c.conflict_type,
                       c.severity_score, c.reasoning, c.affected_teams,
                       c.status, c.resolved_by, c.resolution_note,
                       c.resolved_at, c.created_at
                FROM decision_conflicts c
                JOIN document sd ON c.source_doc_id = sd.id
                JOIN document td ON c.target_doc_id = td.id
                WHERE\s""" + where + """

                ORDER BY c.created_at DESC
                LIMIT :limit OFFSET :offset
                """;

        var querySpec = jdbc.sql(selectSql);
        params.forEach(querySpec::param);
        querySpec.param("limit", paging.size()).param("offset", paging.offset());

        List<ConflictItem> items = querySpec.query((rs, rowNum) -> mapRow(rs)).list();
        return PageResponse.of(items, paging.page(), paging.size(), total);
    }

    @Transactional
    public ConflictItem resolveConflict(long conflictId, ResolveConflictRequest req, Long userId, PermissionScope scope) {
        // 존재 확인 겸 권한 재확인. 없는 것과 범위 밖인 것을 구분하지 않는다
        findVisible(conflictId, scope);

        String newStatus = (req.status() != null && !req.status().isBlank())
                ? req.status().trim().toUpperCase()
                : "RESOLVED";
        if (!ALLOWED_RESOLUTION_STATUSES.contains(newStatus)) {
            throw new InvalidStatusException(newStatus);
        }

        String resolver = (userId != null) ? "user-" + userId : "system";

        jdbc.sql("""
                UPDATE decision_conflicts
                SET status = :status,
                    resolution_note = :note,
                    resolved_by = :resolvedBy,
                    resolved_at = now()
                WHERE id = :id
                """)
                .param("status", newStatus)
                .param("note", req.resolutionNote() != null ? req.resolutionNote() : "")
                .param("resolvedBy", resolver)
                .param("id", conflictId)
                .update();

        return findVisible(conflictId, scope);
    }

    /** 권한 범위 안에 있는 상충만 돌려준다. 범위 밖이거나 없으면 404로 처리한다. */
    public ConflictItem findVisible(long conflictId, PermissionScope scope) {
        ScopePredicate.Clause sourceAllow = ScopePredicate.of(scope, "sd");
        ScopePredicate.Clause targetAllow = ScopePredicate.of(scope, "td");
        String sql = """
                SELECT c.id, c.source_doc_id, sd.title AS source_title,
                       c.target_doc_id, td.title AS target_title,
                       c.source_clause, c.target_clause, c.conflict_type,
                       c.severity_score, c.reasoning, c.affected_teams,
                       c.status, c.resolved_by, c.resolution_note,
                       c.resolved_at, c.created_at
                FROM decision_conflicts c
                JOIN document sd ON c.source_doc_id = sd.id
                JOIN document td ON c.target_doc_id = td.id
                WHERE c.id = :id AND sd.deleted_at IS NULL AND td.deleted_at IS NULL
                  AND %s AND %s
                """.formatted(sourceAllow.sql(), targetAllow.sql());

        var spec = jdbc.sql(sql).param("id", conflictId);
        sourceAllow.params().forEach(spec::param);
        targetAllow.params().forEach(spec::param);

        return spec.query((rs, rowNum) -> mapRow(rs)).optional()
                .orElseThrow(ConflictNotFoundException::new);
    }

    public static class ConflictNotFoundException extends RuntimeException {
    }

    public static class InvalidStatusException extends RuntimeException {
        public final String status;

        public InvalidStatusException(String status) {
            super("허용되지 않는 상태값: " + status);
            this.status = status;
        }
    }

    private ConflictItem mapRow(ResultSet rs) throws SQLException {
        Array arr = rs.getArray("affected_teams");
        List<String> teams = new ArrayList<>();
        if (arr != null && arr.getArray() != null) {
            String[] raw = (String[]) arr.getArray();
            teams.addAll(Arrays.asList(raw));
        }

        var resolvedAtTs = rs.getTimestamp("resolved_at");
        var createdAtTs = rs.getTimestamp("created_at");

        return new ConflictItem(
                rs.getLong("id"),
                rs.getLong("source_doc_id"),
                rs.getString("source_title"),
                rs.getLong("target_doc_id"),
                rs.getString("target_title"),
                rs.getString("source_clause"),
                rs.getString("target_clause"),
                rs.getString("conflict_type"),
                rs.getDouble("severity_score"),
                rs.getString("reasoning"),
                teams,
                rs.getString("status"),
                rs.getString("resolved_by"),
                rs.getString("resolution_note"),
                resolvedAtTs != null ? resolvedAtTs.toInstant() : null,
                createdAtTs != null ? createdAtTs.toInstant() : null
        );
    }
}
