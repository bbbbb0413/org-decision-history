package com.odh.chat.conversation;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.odh.chat.search.SearchHit;
import com.odh.common.PermissionScope;
import org.springframework.jdbc.core.simple.JdbcClient;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.List;

/**
 * 출처에 문서 정보를 붙인다.
 *
 * 전송 직전에 원본 데이터베이스에서 문서 권한을 다시 확인한다.
 * 검색 시점과 전송 시점 사이에 권한이 회수될 수 있고, 그 사이가 곧 유출 창이다.
 * 권한이 사라진 출처는 안내 없이 목록에서 뺀다. 안내하면 문서의 존재가 드러난다.
 */
@Component
public class SourceResolver {

    private final JdbcClient jdbc;
    private final ObjectMapper objectMapper;

    public SourceResolver(JdbcClient jdbc, ObjectMapper objectMapper) {
        this.jdbc = jdbc;
        this.objectMapper = objectMapper;
    }

    @Transactional(readOnly = true)
    public List<ConversationDtos.Source> resolve(List<SearchHit> hits, PermissionScope scope) {
        List<ConversationDtos.Source> sources = new ArrayList<>();
        int rank = 1;

        for (SearchHit hit : hits) {
            var row = jdbc.sql("""
                            SELECT d.title, d.document_type::text AS document_type, d.authored_at,
                                   d.organization_id, d.team_id,
                                   (SELECT f.id FROM document.document_file f
                                     WHERE f.document_id = d.id AND f.version = d.current_version) AS file_id,
                                   c.page_start, c.page_end, c.bboxes::text AS bboxes
                            FROM document.document d
                            LEFT JOIN indexing.document_chunk c
                                   ON c.document_id = d.id AND c.document_version = :version AND c.chunk_no = :chunkNo
                            WHERE d.id = :id AND d.deleted_at IS NULL
                            """)
                    .param("id", hit.documentId())
                    .param("version", hit.documentVersion())
                    .param("chunkNo", hit.chunkNo())
                    .query((rs, i) -> new Row(
                            rs.getString("title"), rs.getString("document_type"),
                            rs.getDate("authored_at") == null ? null : rs.getDate("authored_at").toString(),
                            (Long) rs.getObject("organization_id"), (Long) rs.getObject("team_id"),
                            (Long) rs.getObject("file_id"),
                            (Integer) rs.getObject("page_start"), (Integer) rs.getObject("page_end"),
                            rs.getString("bboxes")))
                    .optional();

            if (row.isEmpty() || !visible(row.get(), hit.documentId(), scope)) {
                continue;
            }
            Row r = row.get();
            sources.add(new ConversationDtos.Source(
                    hit.documentId(), r.title(), r.documentType(), r.authoredAt(),
                    hit.documentVersion(), hit.chunkNo(), hit.sectionPath(), hit.chunkTag(),
                    hit.vectorSimilarity() == null ? null : round(hit.vectorSimilarity()), rank++, r.fileId(),
                    r.pageStart(), r.pageEnd(), parseBboxes(r.bboxes())));
        }
        return sources;
    }

    /** 현재 권한 범위에 드는지 다시 판정한다. */
    private boolean visible(Row row, long documentId, PermissionScope scope) {
        if (scope.deniedDocumentIds().contains(documentId)) {
            return false;
        }
        if (scope.systemAdmin()) {
            return true;
        }
        return scope.documentIds().contains(documentId)
                || (row.organizationId() != null && scope.organizationIds().contains(row.organizationId()))
                || (row.teamId() != null && scope.teamIds().contains(row.teamId()));
    }

    private double round(double value) {
        return Math.round(Math.min(0.9999, Math.max(0, value)) * 10000) / 10000.0;
    }

    /** bboxes 컬럼은 JSONB를 문자열로 읽어 그대로 응답 필드에 싣는다(백엔드는 내용을 해석하지 않는다). */
    private JsonNode parseBboxes(String json) {
        if (json == null) {
            return null;
        }
        try {
            return objectMapper.readTree(json);
        } catch (Exception e) {
            return null;
        }
    }

    private record Row(String title, String documentType, String authoredAt,
                       Long organizationId, Long teamId, Long fileId,
                       Integer pageStart, Integer pageEnd, String bboxes) {
    }
}
