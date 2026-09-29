package com.odh.chat.web;

import com.odh.chat.conversation.ConversationDtos;
import com.odh.chat.conversation.ConversationStore;
import com.odh.chat.conversation.SourceResolver;
import com.odh.common.internal.PermissionScopeClient;
import com.odh.chat.search.SearchHit;
import com.odh.common.PermissionScope;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * 저장된 답변의 출처를 권한 범위로 다시 확인해 돌려준다({@code GET /api/messages/{id}/sources}).
 * 답변 생성 자체는 rag-service(Python/LangGraph)가 한다.
 */
@Service
public class AskService {

    private final PermissionScopeClient scopes;
    private final ConversationStore store;
    private final SourceResolver sourceResolver;

    public AskService(PermissionScopeClient scopes, ConversationStore store, SourceResolver sourceResolver) {
        this.scopes = scopes;
        this.store = store;
        this.sourceResolver = sourceResolver;
    }

    /**
     * 저장된 답변의 출처를 다시 확인해 돌려준다.
     * 조회 시점에 권한을 다시 판정하므로, 그 사이 권한이 회수된 출처는 빠진다.
     */
    public List<ConversationDtos.Source> sourcesOf(long userId, long messageId) {
        PermissionScope scope = scopes.scopeOf(userId);
        List<SearchHit> stored = store.sourcesOfMessage(messageId);
        return sourceResolver.resolve(stored, scope);
    }
}
