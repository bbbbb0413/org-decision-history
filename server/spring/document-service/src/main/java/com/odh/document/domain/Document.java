package com.odh.document.domain;

import jakarta.persistence.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;
import java.time.LocalDate;

/**
 * 문서의 현재 상태.
 * 과거 버전은 DocumentVersion 에, 원본 파일은 DocumentFile 에 둔다.
 */
@Entity
@Table(name = "document")
public class Document {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, length = 200)
    private String title;

    /** 파싱된 일반 텍스트. 원본 형식의 서식은 재현하지 않는다. */
    @Column(nullable = false, columnDefinition = "text")
    private String content;

    @JdbcTypeCode(SqlTypes.NAMED_ENUM)
    @Enumerated(EnumType.STRING)
    @Column(name = "document_type", nullable = false, columnDefinition = "document_type", updatable = false)
    private DocumentType documentType;

    @JdbcTypeCode(SqlTypes.NAMED_ENUM)
    @Enumerated(EnumType.STRING)
    @Column(name = "decision_status", nullable = false, columnDefinition = "decision_status")
    private DecisionStatus decisionStatus;

    @Column(name = "organization_id", nullable = false)
    private Long organizationId;

    @Column(name = "team_id")
    private Long teamId;

    @Column(name = "project_name", length = 100)
    private String projectName;

    @Column(name = "author_id")
    private Long authorId;

    @Column(name = "author_name", nullable = false, length = 100)
    private String authorName;

    /** 문서 자체의 작성일. 제품 등록 시각과 다르다. 타임라인 정렬 기준이다. */
    @Column(name = "authored_at", nullable = false)
    private LocalDate authoredAt;

    @JdbcTypeCode(SqlTypes.NAMED_ENUM)
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, columnDefinition = "document_origin")
    private DocumentOrigin origin;

    @Column(name = "current_version", nullable = false)
    private int currentVersion;

    @Column(name = "created_at", nullable = false, insertable = false, updatable = false)
    private Instant createdAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    /** 소프트 삭제. null 이 아니면 검색·답변·다운로드에서 제외한다. */
    @Column(name = "deleted_at")
    private Instant deletedAt;

    protected Document() {
    }

    public static Document create(String title, String content, DocumentType type, DecisionStatus status,
                                  Long organizationId, Long teamId, String projectName,
                                  Long authorId, String authorName, LocalDate authoredAt) {
        Document d = new Document();
        d.title = title;
        d.content = content;
        d.documentType = type;
        d.decisionStatus = status;
        d.organizationId = organizationId;
        d.teamId = teamId;
        d.projectName = projectName;
        d.authorId = authorId;
        d.authorName = authorName;
        d.authoredAt = authoredAt;
        d.origin = DocumentOrigin.MANUAL;
        d.currentVersion = 1;
        d.updatedAt = Instant.now();
        return d;
    }

    /** 일괄 가져오기로 등록한다. 결정 상태는 원본에서 알 수 없어 미확정으로 둔다. */
    public static Document createImported(String title, String content, DocumentType type,
                                          Long organizationId, Long teamId,
                                          Long authorId, String authorName, LocalDate authoredAt) {
        Document d = create(title, content, type, DecisionStatus.UNDECIDED,
                organizationId, teamId, null, authorId, authorName, authoredAt);
        d.origin = DocumentOrigin.IMPORT;
        return d;
    }

    /** 수정은 새 버전을 만든다. documentType 은 등록 후 변경할 수 없다. */
    public void update(String title, String content, DecisionStatus status,
                       Long teamId, String projectName) {
        this.title = title;
        this.content = content;
        this.decisionStatus = status;
        this.teamId = teamId;
        this.projectName = projectName;
        this.currentVersion += 1;
        this.updatedAt = Instant.now();
    }

    public void softDelete() {
        this.deletedAt = Instant.now();
        this.updatedAt = this.deletedAt;
    }

    public boolean isDeleted() {
        return deletedAt != null;
    }

    public Long getId() { return id; }
    public String getTitle() { return title; }
    public String getContent() { return content; }
    public DocumentType getDocumentType() { return documentType; }
    public DecisionStatus getDecisionStatus() { return decisionStatus; }
    public Long getOrganizationId() { return organizationId; }
    public Long getTeamId() { return teamId; }
    public String getProjectName() { return projectName; }
    public Long getAuthorId() { return authorId; }
    public String getAuthorName() { return authorName; }
    public LocalDate getAuthoredAt() { return authoredAt; }
    public DocumentOrigin getOrigin() { return origin; }
    public int getCurrentVersion() { return currentVersion; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
    public Instant getDeletedAt() { return deletedAt; }
}
