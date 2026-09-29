package com.odh.document.service;

import com.odh.common.PermissionScope;
import com.odh.common.web.PageRequest;
import com.odh.common.web.PageResponse;
import com.odh.document.domain.DecisionStatus;
import com.odh.document.domain.DocumentType;
import com.odh.document.scope.ScopePredicate;
import com.odh.document.web.DocumentDtos;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.*;

/**
 * 문서 목록 조회.
 *
 * 권한 조건을 질의에 넣는다. 결과를 받아 거르면 페이지 건수와 유형별 집계가
 * 권한 밖 문서까지 세게 되어 "전체 114건" 같은 숫자로 존재가 새어 나간다.
 */
@Service
@Transactional(readOnly = true)
public class DocumentQueryService {

    private final JdbcClient jdbc;

    public DocumentQueryService(JdbcClient jdbc) {
        this.jdbc = jdbc;
    }

    /**
     * @param authorId 이 사용자가 쓴 문서만. null 이면 거르지 않는다.
     *                 컨트롤러가 {@code mine=true} 를 요청자 id 로 바꿔 넘긴다 — 임의 id 를
     *                 받지 않으므로 남의 작성 이력을 훑는 데 쓸 수 없다.
     */
    public DocumentDtos.Page search(PermissionScope scope, String q,
                                    List<DocumentType> types, List<DecisionStatus> statuses,
                                    Long organizationId, Long teamId, Long authorId,
                                    int page, int size) {

        PageRequest paging = PageRequest.of(page, size);
        ScopePredicate.Clause allow = ScopePredicate.of(scope);
        StringBuilder where = new StringBuilder("d.deleted_at IS NULL AND ").append(allow.sql());
        Map<String, Object> params = new HashMap<>(allow.params());

        if (q != null && !q.isBlank()) {
            where.append(" AND (d.title ILIKE :q OR d.author_name ILIKE :q OR d.project_name ILIKE :q)");
            params.put("q", "%" + q.trim() + "%");
        }
        if (types != null && !types.isEmpty()) {
            where.append(" AND d.document_type::text IN (:types)");
            params.put("types", types.stream().map(Enum::name).toList());
        }
        if (statuses != null && !statuses.isEmpty()) {
            where.append(" AND d.decision_status::text IN (:statuses)");
            params.put("statuses", statuses.stream().map(Enum::name).toList());
        }
        if (organizationId != null) {
            where.append(" AND d.organization_id = :organizationId");
            params.put("organizationId", organizationId);
        }
        if (teamId != null) {
            where.append(" AND d.team_id = :teamId");
            params.put("teamId", teamId);
        }
        if (authorId != null) {
            where.append(" AND d.author_id = :authorId");
            params.put("authorId", authorId);
        }

        String listSql = """
                SELECT d.id, d.title, d.document_type::text AS document_type,
                       d.decision_status::text AS decision_status,
                       d.organization_id, d.team_id, d.project_name, d.author_id, d.author_name,
                       d.authored_at, d.current_version, d.updated_at,
                       EXISTS (SELECT 1 FROM document_file f
                               WHERE f.document_id = d.id AND f.version = d.current_version) AS has_file
                FROM document d
                WHERE %s
                ORDER BY d.updated_at DESC
                LIMIT :limit OFFSET :offset
                """.formatted(where);

        var spec = jdbc.sql(listSql).param("limit", paging.size()).param("offset", paging.offset());
        params.forEach(spec::param);

        List<DocumentDtos.Summary> content = spec.query((rs, i) -> new DocumentDtos.Summary(
                rs.getLong("id"),
                rs.getString("title"),
                DocumentType.valueOf(rs.getString("document_type")),
                DecisionStatus.valueOf(rs.getString("decision_status")),
                rs.getLong("organization_id"),
                (Long) rs.getObject("team_id"),
                rs.getString("project_name"),
                (Long) rs.getObject("author_id"),
                rs.getString("author_name"),
                rs.getDate("authored_at").toLocalDate(),
                rs.getInt("current_version"),
                rs.getTimestamp("updated_at").toInstant(),
                rs.getBoolean("has_file"))).list();

        var countSpec = jdbc.sql("SELECT count(*) FROM document d WHERE " + where);
        params.forEach(countSpec::param);
        long total = countSpec.query(Long.class).single();

        return new DocumentDtos.Page(content, paging.page(), paging.size(), total,
                PageResponse.totalPagesOf(total, paging.size()), typeCounts(scope));
    }

    /** 좌측 필터에 표시할 유형별 건수. 권한 범위 안에서만 센다. */
    private Map<String, Integer> typeCounts(PermissionScope scope) {
        ScopePredicate.Clause allow = ScopePredicate.of(scope);
        var spec = jdbc.sql("""
                SELECT d.document_type::text AS document_type, count(*) AS cnt
                FROM document d
                WHERE d.deleted_at IS NULL AND %s
                GROUP BY d.document_type
                """.formatted(allow.sql()));
        allow.params().forEach(spec::param);

        Map<String, Integer> counts = new LinkedHashMap<>();
        for (DocumentType t : DocumentType.values()) {
            counts.put(t.name(), 0);
        }
        spec.query((rs, i) -> {
            counts.put(rs.getString("document_type"), rs.getInt("cnt"));
            return null;
        }).list();
        return counts;
    }

    /** 권한 범위 안에 있는 문서인지 확인한다. 상세·다운로드 전에 호출한다. */
    public boolean isVisible(PermissionScope scope, Long documentId) {
        ScopePredicate.Clause allow = ScopePredicate.of(scope);
        var spec = jdbc.sql("""
                SELECT count(*) FROM document d
                WHERE d.id = :id AND d.deleted_at IS NULL AND %s
                """.formatted(allow.sql())).param("id", documentId);
        allow.params().forEach(spec::param);
        return spec.query(Long.class).single() > 0;
    }
}
