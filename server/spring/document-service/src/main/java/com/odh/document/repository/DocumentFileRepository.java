package com.odh.document.repository;

import com.odh.document.domain.DocumentFile;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;

public interface DocumentFileRepository extends JpaRepository<DocumentFile, Long> {

    /** 그 판에 **실제로 올라온** 파일. 버전 이력 화면과 교체 처리가 쓴다. */
    Optional<DocumentFile> findByDocumentIdAndVersion(Long documentId, int version);

    List<DocumentFile> findByDocumentIdOrderByVersionDesc(Long documentId);

    /**
     * "지금 이 문서의 원본"을 찾는다 — 그 판 이하에서 가장 최근에 올라온 파일이다.
     *
     * 본문만 고쳐도 판이 올라가는데(`Document.update`) 파일은 올린 판에 묶여 있다. 정확한
     * 판으로만 찾으면 오타 하나만 고쳐도 원본이 사라지고, 색인은 본문 기반으로 돌아가
     * PDF 좌표를 잃고, 지표 2(출처 검증 가능 비율)까지 떨어진다.
     *
     * 행을 복사하지 않고 조회 규칙으로 푼 이유: `document_file.storage_key` 가 UNIQUE 라
     * 같은 객체를 두 판에 넣을 수 없고, 객체를 복사하면 바이트가 중복된다. 그리고 원본이
     * 바뀌어 다시 올리면 그 판에 행이 생겨 자동으로 이 조회에서 우선한다 — 따로 이어붙이는
     * 처리가 필요 없다.
     */
    Optional<DocumentFile> findFirstByDocumentIdAndVersionLessThanEqualOrderByVersionDesc(
            Long documentId, int version);
}
