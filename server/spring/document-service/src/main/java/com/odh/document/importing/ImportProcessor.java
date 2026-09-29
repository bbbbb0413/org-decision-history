package com.odh.document.importing;

import com.odh.document.domain.DocumentType;
import com.odh.document.storage.FileStorageService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.util.List;
import java.util.UUID;

/**
 * 가져오기 작업을 백그라운드에서 처리한다.
 *
 * HTTP 스레드는 파일을 다 받은 뒤 202 를 곧바로 돌려주고, 실제 등록은 여기서 이어간다.
 * 이 프로젝트 규모에서는 별도 메시지 브로커 없이 스레드 풀로 충분하다 — 색인처럼
 * 여러 서비스가 나눠 소비할 필요가 없는, document-service 안에서 끝나는 작업이기 때문이다.
 */
@Component
public class ImportProcessor {

    private static final Logger log = LoggerFactory.getLogger(ImportProcessor.class);

    private final ImportItemService items;
    private final FileStorageService storage;

    public ImportProcessor(ImportItemService items, FileStorageService storage) {
        this.items = items;
        this.storage = storage;
    }

    public record PendingFile(String filename, byte[] bytes) {
    }

    @Async("importExecutor")
    public void run(UUID jobKey, Long jobId, List<PendingFile> files, DocumentType defaultType,
                    Long organizationId, Long teamId, Long userId) {
        items.markRunning(jobId);
        try {
            for (PendingFile file : files) {
                processOne(jobKey, jobId, file, defaultType, organizationId, teamId, userId);
            }
        } finally {
            // 파일 하나가 무엇을 하든 나머지 파일과 작업 종료는 보장한다.
            // 여기서 예외가 새면 작업이 RUNNING 에 영원히 머문다.
            items.markFinished(jobId);
        }
    }

    /**
     * 실패 기록 자체(재시도용 임시 보관 포함)도 저장소·DB 호출이라 또 실패할 수 있다.
     * 그 실패가 바깥 루프를 끊고 작업을 RUNNING 에 묶어 두지 않도록 이 메서드 안에서 전부 삼킨다.
     */
    private void processOne(UUID jobKey, Long jobId, PendingFile file, DocumentType defaultType,
                            Long organizationId, Long teamId, Long userId) {
        try {
            Long documentId = items.attemptImport(file.bytes(), file.filename(), defaultType, organizationId, teamId, userId);
            items.recordSuccess(jobId, file.filename(), documentId);
        } catch (Exception e) {
            Outcome outcome = classify(e);
            log.info("가져오기 실패. job={} file={} 사유={}", jobKey, file.filename(), outcome.message());
            String stagingKey = null;
            if (outcome.retryable()) {
                stagingKey = tryStage(jobKey, file);
            }
            try {
                items.recordFailure(jobId, file.filename(), outcome.message(), outcome.retryable() && stagingKey != null, stagingKey);
            } catch (Exception recordFailed) {
                log.error("실패 기록 자체가 실패했다. job={} file={}", jobKey, file.filename(), recordFailed);
            }
        }
    }

    /** 임시 보관도 실패하면(같은 저장소 장애) 재시도 불가로 남긴다. null 은 재시도 불가를 뜻한다. */
    private String tryStage(UUID jobKey, PendingFile file) {
        try {
            return storage.stage(stagingKeyOf(jobKey, file.filename()), file.bytes());
        } catch (Exception e) {
            log.warn("실패 건 임시 보관에도 실패했다. 이 건은 재시도할 수 없다. job={} file={}", jobKey, file.filename());
            return null;
        }
    }

    @Async("importExecutor")
    public void runRetry(UUID jobKey, Long jobId, DocumentType defaultType,
                         Long organizationId, Long teamId, Long userId) {
        items.markRunning(jobId);
        try {
            for (ImportItemService.RetryCandidate candidate : items.retryCandidates(jobId)) {
                retryOne(jobKey, jobId, candidate, defaultType, organizationId, teamId, userId);
            }
        } finally {
            items.markFinished(jobId);
        }
    }

    private void retryOne(UUID jobKey, Long jobId, ImportItemService.RetryCandidate candidate, DocumentType defaultType,
                          Long organizationId, Long teamId, Long userId) {
        try {
            byte[] bytes = storage.readAll(candidate.stagingKey());
            Long documentId = items.attemptImport(bytes, candidate.filename(), defaultType, organizationId, teamId, userId);
            storage.unstage(candidate.stagingKey());
            items.recordRetrySuccess(jobId, candidate.itemId(), documentId);
        } catch (Exception e) {
            Outcome outcome = classify(e);
            log.info("재시도 실패. job={} file={} 사유={}", jobKey, candidate.filename(), outcome.message());
            try {
                if (outcome.retryable()) {
                    // 내용이 그대로이니 다시 올릴 필요 없이 같은 임시 보관 키를 유지한다
                    items.recordRetryFailure(candidate.itemId(), outcome.message(), true, candidate.stagingKey());
                } else {
                    storage.unstage(candidate.stagingKey());
                    items.recordRetryFailure(candidate.itemId(), outcome.message(), false, null);
                }
            } catch (Exception recordFailed) {
                log.error("재시도 실패 기록 자체가 실패했다. job={} file={}", jobKey, candidate.filename(), recordFailed);
            }
        }
    }

    private String stagingKeyOf(UUID jobKey, String filename) {
        return "imports/%s/%s-%s".formatted(jobKey, UUID.randomUUID(), filename);
    }

    private Outcome classify(Exception e) {
        if (e instanceof DocumentMetadataExtractor.ExtractionException ex) {
            return new Outcome(ex.getMessage(), ex.retryable);
        }
        if (e instanceof FileStorageService.UnsupportedFileTypeException ex) {
            return new Outcome("지원하지 않는 파일 형식입니다: " + ex.extension, false);
        }
        if (e instanceof FileStorageService.FileTooLargeException ex) {
            return new Outcome("파일 크기 상한 %d MB 를 초과했습니다.".formatted(ex.limit / (1024 * 1024)), false);
        }
        if (e instanceof IOException) {
            return new Outcome("원본 파일을 읽지 못했습니다: " + e.getMessage(), true);
        }
        return new Outcome("예기치 못한 오류로 처리하지 못했습니다: " + e.getMessage(), true);
    }

    private record Outcome(String message, boolean retryable) {
    }
}
