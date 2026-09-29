package com.odh.document.domain;

import jakarta.persistence.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

import java.time.Instant;

/** 문서 버전 이력. 수정 시 직전 상태를 여기에 남긴다. */
@Entity
@Table(name = "document_version")
public class DocumentVersion {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "document_id", nullable = false)
    private Long documentId;

    @Column(nullable = false)
    private int version;

    @Column(nullable = false, length = 200)
    private String title;

    @Column(nullable = false, columnDefinition = "text")
    private String content;

    @JdbcTypeCode(SqlTypes.NAMED_ENUM)
    @Enumerated(EnumType.STRING)
    @Column(name = "decision_status", nullable = false, columnDefinition = "decision_status")
    private DecisionStatus decisionStatus;

    @Column(name = "change_note", length = 300)
    private String changeNote;

    @Column(name = "created_by")
    private Long createdBy;

    @Column(name = "created_at", nullable = false, insertable = false, updatable = false)
    private Instant createdAt;

    protected DocumentVersion() {
    }

    public static DocumentVersion snapshot(Document d, String changeNote, Long createdBy) {
        DocumentVersion v = new DocumentVersion();
        v.documentId = d.getId();
        v.version = d.getCurrentVersion();
        v.title = d.getTitle();
        v.content = d.getContent();
        v.decisionStatus = d.getDecisionStatus();
        v.changeNote = changeNote;
        v.createdBy = createdBy;
        return v;
    }

    public int getVersion() { return version; }
    public String getTitle() { return title; }
    /** 그 버전 시점의 본문 전문. 버전 목록에는 싣지 않고 단건 조회에서만 내려준다. */
    public String getContent() { return content; }
    public DecisionStatus getDecisionStatus() { return decisionStatus; }
    public String getChangeNote() { return changeNote; }
    public Long getCreatedBy() { return createdBy; }
    public Instant getCreatedAt() { return createdAt; }
}
