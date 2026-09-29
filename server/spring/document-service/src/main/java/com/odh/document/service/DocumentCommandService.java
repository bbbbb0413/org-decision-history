package com.odh.document.service;

import com.odh.document.domain.*;
import com.odh.document.repository.*;
import com.odh.document.storage.FileStorageService;
import com.odh.document.web.DocumentDtos;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;

/** 문서 등록·수정·삭제와 원본 보관. */
@Service
@Transactional
public class DocumentCommandService {

    private final DocumentRepository documents;
    private final DocumentVersionRepository versions;
    private final DocumentFileRepository files;
    private final FileStorageService storage;
    private final OutboxPublisher outbox;

    public DocumentCommandService(DocumentRepository documents,
                                  DocumentVersionRepository versions,
                                  DocumentFileRepository files,
                                  FileStorageService storage,
                                  OutboxPublisher outbox) {
        this.documents = documents;
        this.versions = versions;
        this.files = files;
        this.storage = storage;
        this.outbox = outbox;
    }

    public Document create(DocumentDtos.CreateRequest request, Long authorId, String authorName) {
        Document document = documents.save(Document.create(
                request.title(), request.content(), request.documentType(), request.decisionStatus(),
                request.organizationId(), request.teamId(), request.projectName(),
                authorId, authorName, request.authoredAt()));

        versions.save(DocumentVersion.snapshot(document, "최초 작성", authorId));
        outbox.record(document, OutboxEventType.DOCUMENT_CREATED);
        return document;
    }

    public Document update(Long documentId, DocumentDtos.UpdateRequest request, Long actorId) {
        Document document = documents.findByIdAndDeletedAtIsNull(documentId)
                .orElseThrow(DocumentNotFoundException::new);

        // 낙관적 잠금. 다른 사용자가 먼저 수정했으면 덮어쓰지 않는다.
        if (document.getCurrentVersion() != request.currentVersion()) {
            throw new VersionConflictException(document.getCurrentVersion());
        }

        document.update(request.title(), request.content(), request.decisionStatus(),
                request.teamId(), request.projectName());
        versions.save(DocumentVersion.snapshot(document, request.changeNote(), actorId));
        outbox.record(document, OutboxEventType.DOCUMENT_UPDATED);
        return document;
    }

    /**
     * 소프트 삭제. 검색·답변·다운로드에서 제외한다.
     * 원본 파일은 지우지 않는다. 되돌릴 수 없는 작업을 사용자 조작 한 번에 붙이지 않기 위해서다.
     */
    public void delete(Long documentId) {
        Document document = documents.findByIdAndDeletedAtIsNull(documentId)
                .orElseThrow(DocumentNotFoundException::new);
        document.softDelete();
        outbox.record(document, OutboxEventType.DOCUMENT_DELETED);
    }

    /**
     * 현재 버전의 원본 파일을 보관한다. 같은 버전에 다시 올리면 교체한다.
     *
     * 판을 올리지 않는다 — 올린 파일은 지금 판의 원본이다. 대신 **재색인을 걸어야 한다.**
     * 아웃박스 이벤트를 남기지 않으면 새 원본을 올려도 색인은 이전 내용을 그대로 들고 있다.
     * 화면에는 새 파일이 보이는데 검색·인용은 이전 내용을 가리키는, 눈에 안 띄는 불일치가 된다.
     */
    public DocumentFile storeFile(Long documentId, MultipartFile upload, Long uploaderId) throws IOException {
        Document document = documents.findByIdAndDeletedAtIsNull(documentId)
                .orElseThrow(DocumentNotFoundException::new);

        var stored = storage.store(documentId, document.getCurrentVersion(), upload);

        files.findByDocumentIdAndVersion(documentId, document.getCurrentVersion())
                .ifPresent(existing -> {
                    storage.delete(existing.getStorageKey());
                    files.delete(existing);
                    files.flush();
                });

        DocumentFile saved = files.save(DocumentFile.of(
                documentId, document.getCurrentVersion(), stored.storageKey(),
                upload.getOriginalFilename(), stored.mimeType(), stored.sizeBytes(),
                stored.checksum(), uploaderId));

        outbox.record(document, OutboxEventType.DOCUMENT_UPDATED);
        return saved;
    }

    public static class DocumentNotFoundException extends RuntimeException {
    }

    public static class VersionConflictException extends RuntimeException {
        public final int currentVersion;
        public VersionConflictException(int currentVersion) {
            this.currentVersion = currentVersion;
        }
    }
}
