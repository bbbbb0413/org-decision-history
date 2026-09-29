package com.odh.document.repository;

import com.odh.document.domain.DocumentVersion;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface DocumentVersionRepository extends JpaRepository<DocumentVersion, Long> {
    List<DocumentVersion> findByDocumentIdOrderByVersionDesc(Long documentId);

    /** 특정 버전의 본문까지 읽는다. */
    Optional<DocumentVersion> findByDocumentIdAndVersion(Long documentId, int version);
}
