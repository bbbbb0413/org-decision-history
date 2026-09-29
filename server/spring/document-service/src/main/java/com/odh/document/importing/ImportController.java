package com.odh.document.importing;

import com.odh.common.InternalAuth;
import com.odh.document.domain.DocumentType;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.List;
import java.util.UUID;

@RestController
public class ImportController {

    private final ImportService service;

    public ImportController(ImportService service) {
        this.service = service;
    }

    @PostMapping(value = "/api/document-imports", consumes = "multipart/form-data")
    public ResponseEntity<ImportDtos.Job> create(
            @RequestHeader(InternalAuth.USER_ID_HEADER) Long userId,
            @RequestParam("files") List<MultipartFile> files,
            @RequestParam("defaultDocumentType") DocumentType defaultDocumentType,
            @RequestParam("organizationId") Long organizationId,
            @RequestParam(value = "teamId", required = false) Long teamId) throws IOException {

        ImportDtos.Job job = service.create(files, defaultDocumentType, organizationId, teamId, userId);
        return ResponseEntity.status(HttpStatus.ACCEPTED).body(job);
    }

    @GetMapping("/api/document-imports/{jobKey}")
    public ImportDtos.JobDetail get(@PathVariable UUID jobKey) {
        return service.getDetail(jobKey);
    }

    @PostMapping("/api/document-imports/{jobKey}/retry")
    public ResponseEntity<ImportDtos.Job> retry(@PathVariable UUID jobKey) {
        return ResponseEntity.accepted().body(service.retry(jobKey));
    }
}
