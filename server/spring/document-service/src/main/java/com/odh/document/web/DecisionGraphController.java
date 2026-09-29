package com.odh.document.web;

import com.odh.common.InternalAuth;
import com.odh.common.internal.PermissionScopeClient;
import com.odh.document.service.DecisionGraphService;
import com.odh.document.web.DecisionGraphDtos.GraphResponse;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
public class DecisionGraphController {

    private final DecisionGraphService graphService;
    private final PermissionScopeClient scopes;

    public DecisionGraphController(DecisionGraphService graphService, PermissionScopeClient scopes) {
        this.graphService = graphService;
        this.scopes = scopes;
    }

    /** 단일 문서의 1~2 hop 의사결정 계보/인과/상충 관계망 조회 */
    @GetMapping("/api/documents/{documentId}/graph")
    public GraphResponse getDocumentGraph(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @PathVariable Long documentId) {
        return graphService.getGraphForDocuments(List.of(documentId), scopes.scopeOf(userId));
    }

    /** 여러 문서(예: 대화 답변 출처 문서들)의 통합 인과/상충 그래프 조회 */
    @GetMapping("/api/documents/graph")
    public GraphResponse getMultiDocumentGraph(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @RequestParam(required = false) List<Long> ids) {
        if (ids == null || ids.isEmpty()) {
            return new GraphResponse(List.of(), List.of());
        }
        return graphService.getGraphForDocuments(ids, scopes.scopeOf(userId));
    }
}
