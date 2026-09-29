package com.odh.document.domain;

import jakarta.persistence.*;

import java.time.Instant;

/**
 * 원본 파일 메타데이터.
 * 파일 실체는 객체 저장소에 있고 여기에는 저장 키만 둔다.
 * 버전마다 따로 보관하므로 과거 버전의 원본도 내려받을 수 있다.
 */
@Entity
@Table(name = "document_file")
public class DocumentFile {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "document_id", nullable = false)
    private Long documentId;

    @Column(nullable = false)
    private int version;

    @Column(name = "storage_key", nullable = false, length = 500)
    private String storageKey;

    @Column(name = "original_filename", nullable = false, length = 255)
    private String originalFilename;

    @Column(name = "mime_type", nullable = false, length = 100)
    private String mimeType;

    @Column(name = "size_bytes", nullable = false)
    private long sizeBytes;

    /** 업로드와 다운로드의 무결성 확인용 */
    @Column(name = "checksum_sha256", nullable = false, length = 64)
    private String checksumSha256;

    @Column(name = "uploaded_by")
    private Long uploadedBy;

    @Column(name = "uploaded_at", nullable = false, insertable = false, updatable = false)
    private Instant uploadedAt;

    protected DocumentFile() {
    }

    public static DocumentFile of(Long documentId, int version, String storageKey, String originalFilename,
                                  String mimeType, long sizeBytes, String checksum, Long uploadedBy) {
        DocumentFile f = new DocumentFile();
        f.documentId = documentId;
        f.version = version;
        f.storageKey = storageKey;
        f.originalFilename = originalFilename;
        f.mimeType = mimeType;
        f.sizeBytes = sizeBytes;
        f.checksumSha256 = checksum;
        f.uploadedBy = uploadedBy;
        return f;
    }

    public Long getId() { return id; }
    public Long getDocumentId() { return documentId; }
    public int getVersion() { return version; }
    public String getStorageKey() { return storageKey; }
    public String getOriginalFilename() { return originalFilename; }
    public String getMimeType() { return mimeType; }
    public long getSizeBytes() { return sizeBytes; }
    public String getChecksumSha256() { return checksumSha256; }
    public Long getUploadedBy() { return uploadedBy; }
    public Instant getUploadedAt() { return uploadedAt; }
}
