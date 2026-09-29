package com.odh.document.web;

import com.odh.common.InternalAuth;
import com.odh.common.PermissionScope;
import com.odh.document.domain.DecisionStatus;
import com.odh.document.domain.Document;
import com.odh.document.domain.DocumentFile;
import com.odh.document.domain.DocumentType;
import com.odh.document.domain.DocumentVersion;
import com.odh.document.repository.DocumentFileRepository;
import com.odh.document.repository.DocumentRepository;
import com.odh.document.repository.DocumentVersionRepository;
import com.odh.common.internal.AuditClient;
import com.odh.common.internal.PermissionScopeClient;
import com.odh.document.service.DocumentCommandService;
import com.odh.document.service.DocumentQueryService;
import com.odh.document.storage.FileStorageService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.List;

@RestController
public class DocumentController {

    private final DocumentQueryService query;
    private final DocumentCommandService command;
    private final DocumentRepository documents;
    private final DocumentVersionRepository versions;
    private final DocumentFileRepository files;
    private final FileStorageService storage;
    private final PermissionScopeClient scopes;
    private final AuditClient auditClient;

    public DocumentController(DocumentQueryService query, DocumentCommandService command,
                              DocumentRepository documents, DocumentVersionRepository versions,
                              DocumentFileRepository files, FileStorageService storage,
                              PermissionScopeClient scopes, AuditClient auditClient) {
        this.query = query;
        this.command = command;
        this.documents = documents;
        this.versions = versions;
        this.files = files;
        this.storage = storage;
        this.scopes = scopes;
        this.auditClient = auditClient;
    }

    @GetMapping("/api/documents")
    public DocumentDtos.Page list(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @RequestParam(required = false) String q,
            @RequestParam(required = false) List<DocumentType> documentType,
            @RequestParam(required = false) List<DecisionStatus> decisionStatus,
            @RequestParam(required = false) Long organizationId,
            @RequestParam(required = false) Long teamId,
            @RequestParam(defaultValue = "false") boolean mine,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size) {

        // 작성자 id 를 파라미터로 받지 않고 mine 플래그만 받는다 — 임의 id 를 받으면
        // 권한 범위 안에서 특정 인물의 작성 이력만 골라 훑는 데 쓸 수 있다.
        Long authorId = mine ? userId : null;
        return query.search(scopes.scopeOf(userId), q, documentType, decisionStatus,
                organizationId, teamId, authorId, page, size);
    }

    @PostMapping("/api/documents")
    public ResponseEntity<DocumentDtos.Detail> create(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @Valid @RequestBody DocumentDtos.CreateRequest request) {

        Document created = command.create(request, userId, "사용자 " + userId);
        return ResponseEntity.status(HttpStatus.CREATED).body(toDetail(created, null));
    }

    /**
     * 문서가 없는 경우와 권한이 없는 경우를 구분하지 않는다. 존재 자체가 정보이기 때문이다.
     */
    @GetMapping("/api/documents/{documentId}")
    public DocumentDtos.Detail detail(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                                      @PathVariable Long documentId) {
        // 열람 접근/차단 이력을 남긴다. 존재하지 않는 문서와
        // 권한 밖 문서를 구분하지 않는 정책(아래 requireVisible)과 같은 이유로,
        // 여기서도 사유를 세분화하지 않는다 — 세분화하면 그 응답 자체가 존재 여부를 흘린다.
        boolean visible = isVisible(userId, documentId);
        auditClient.record(userId, "DOCUMENT_VIEW", "DOCUMENT", documentId, visible,
                visible ? "문서 열람" : "권한 범위 밖 문서 열람 시도");
        if (!visible) {
            throw new DocumentCommandService.DocumentNotFoundException();
        }
        Document document = documents.findByIdAndDeletedAtIsNull(documentId)
                .orElseThrow(DocumentCommandService.DocumentNotFoundException::new);
        return toDetail(document, effectiveFile(document));
    }

    @PutMapping("/api/documents/{documentId}")
    public DocumentDtos.Detail update(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                                      @PathVariable Long documentId,
                                      @Valid @RequestBody DocumentDtos.UpdateRequest request) {
        requireVisible(userId, documentId);
        Document updated = command.update(documentId, request, userId);
        return toDetail(updated, effectiveFile(updated));
    }

    /**
     * 화면에 보여줄 원본 파일. 그 판 이하에서 가장 최근에 올라온 것을 쓴다.
     *
     * 버전 이력(`/versions`)은 이 규칙을 쓰지 않는다 — 그 화면의 질문은 "이 판에 무엇이
     * 올라왔나"이고, 상속시키면 올리지 않은 판에도 파일이 있었던 것처럼 보인다.
     */
    private DocumentFile effectiveFile(Document document) {
        return files.findFirstByDocumentIdAndVersionLessThanEqualOrderByVersionDesc(
                document.getId(), document.getCurrentVersion()).orElse(null);
    }

    @DeleteMapping("/api/documents/{documentId}")
    public ResponseEntity<Void> delete(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                                       @PathVariable Long documentId) {
        requireVisible(userId, documentId);
        command.delete(documentId);
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/api/documents/{documentId}/versions")
    public List<DocumentDtos.VersionInfo> versions(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                                                   @PathVariable Long documentId) {
        requireVisible(userId, documentId);
        var byVersion = files.findByDocumentIdOrderByVersionDesc(documentId).stream()
                .collect(java.util.stream.Collectors.toMap(DocumentFile::getVersion, DocumentFile::getId, (a, b) -> a));

        return versions.findByDocumentIdOrderByVersionDesc(documentId).stream()
                .map(v -> new DocumentDtos.VersionInfo(
                        v.getVersion(), v.getTitle(), v.getDecisionStatus(), v.getChangeNote(),
                        v.getCreatedBy(), v.getCreatedAt(), byVersion.get(v.getVersion())))
                .toList();
    }

    /**
     * 특정 버전의 본문 조회. 버전 간 비교(diff)의 재료다.
     * 문서 접근 권한은 현재 문서 기준으로 판정한다 — 과거 버전만 따로 열람 권한을 두지 않는다.
     */
    @GetMapping("/api/documents/{documentId}/versions/{version}")
    public DocumentDtos.VersionDetail version(@RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
                                              @PathVariable Long documentId,
                                              @PathVariable int version) {
        requireVisible(userId, documentId);
        DocumentVersion v = versions.findByDocumentIdAndVersion(documentId, version)
                .orElseThrow(DocumentCommandService.DocumentNotFoundException::new);

        return new DocumentDtos.VersionDetail(
                v.getVersion(), v.getTitle(), v.getDecisionStatus(), v.getChangeNote(),
                v.getCreatedBy(), v.getCreatedAt(), v.getContent());
    }

    @PostMapping("/api/documents/{documentId}/file")
    public ResponseEntity<DocumentDtos.FileInfo> upload(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @PathVariable Long documentId,
            @RequestParam("file") MultipartFile file) throws IOException {

        requireVisible(userId, documentId);
        DocumentFile stored = command.storeFile(documentId, file, userId);
        return ResponseEntity.status(HttpStatus.CREATED).body(toFileInfo(stored));
    }

    /**
     * POST 인 이유는 이 요청이 권한 재확인과 감사 기록이라는 부수 효과를 갖기 때문이다.
     * GET 은 캐시와 프리페치 대상이 되어 의도치 않은 발급을 만든다.
     */
    @PostMapping("/api/document-files/{fileId}/download-url")
    public ResponseEntity<DocumentDtos.DownloadUrl> downloadUrl(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @PathVariable Long fileId) {

        DocumentFile file = files.findById(fileId)
                .orElseThrow(DocumentCommandService.DocumentNotFoundException::new);
        requireVisible(userId, file.getDocumentId());

        var issued = storage.presignedUrl(file.getStorageKey(), file.getOriginalFilename());
        // 권한을 회수해도 이미 발급된 경로는 유효 시간 동안 살아 있다.
        // 그 구간을 사후 추적할 수 있게 발급 사실을 남긴다.
        auditClient.record(userId, "FILE_DOWNLOAD_ISSUE", "DOCUMENT", file.getDocumentId(),
                true, file.getOriginalFilename() + " 다운로드 경로 발급");

        return ResponseEntity.status(HttpStatus.CREATED).body(new DocumentDtos.DownloadUrl(
                issued.downloadUrl(), issued.expiresAt(),
                file.getOriginalFilename(), file.getMimeType(), file.getSizeBytes()));
    }

    private void requireVisible(Long userId, Long documentId) {
        if (!isVisible(userId, documentId)) {
            throw new DocumentCommandService.DocumentNotFoundException();
        }
    }

    private boolean isVisible(Long userId, Long documentId) {
        PermissionScope scope = scopes.scopeOf(userId);
        return query.isVisible(scope, documentId);
    }

    private DocumentDtos.Detail toDetail(Document d, DocumentFile file) {
        return new DocumentDtos.Detail(
                d.getId(), d.getTitle(), d.getContent(), d.getDocumentType(), d.getDecisionStatus(),
                d.getOrganizationId(), d.getTeamId(), d.getProjectName(), d.getAuthorId(), d.getAuthorName(),
                d.getAuthoredAt(), d.getOrigin(), d.getCurrentVersion(), d.getCreatedAt(), d.getUpdatedAt(),
                file == null ? null : toFileInfo(file));
    }

    private DocumentDtos.FileInfo toFileInfo(DocumentFile f) {
        return new DocumentDtos.FileInfo(f.getId(), f.getDocumentId(), f.getVersion(),
                f.getOriginalFilename(), f.getMimeType(), f.getSizeBytes(),
                f.getChecksumSha256(), f.getUploadedAt());
    }
}
