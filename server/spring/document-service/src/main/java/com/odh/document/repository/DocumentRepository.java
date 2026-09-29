package com.odh.document.repository;

import com.odh.document.domain.Document;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface DocumentRepository extends JpaRepository<Document, Long> {

    /** 삭제되지 않은 문서만 조회한다. 권한 판정은 호출부에서 별도로 한다. */
    Optional<Document> findByIdAndDeletedAtIsNull(Long id);
}
