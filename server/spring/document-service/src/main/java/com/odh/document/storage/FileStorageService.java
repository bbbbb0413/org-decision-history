package com.odh.document.storage;

import org.apache.tika.Tika;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;
import software.amazon.awssdk.core.sync.RequestBody;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.DeleteObjectRequest;
import software.amazon.awssdk.services.s3.model.GetObjectRequest;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;
import software.amazon.awssdk.services.s3.presigner.model.GetObjectPresignRequest;

import java.io.IOException;
import java.io.InputStream;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 원본 파일 보관과 다운로드 경로 발급.
 *
 * 업로드한 형태 그대로 보관하고 변환하지 않는다.
 * 형식 검사는 확장자가 아니라 파일 시그니처로 한다. 확장자만 보면
 * 이름만 바꾼 파일이 통과하기 때문이다.
 *
 * <p>검사는 <b>확장자와 판정 결과가 맞는지</b>까지 본다. 둘을 따로 보면 PDF 를 {@code .md} 로
 * 바꿔 올렸을 때 통과하고, 색인기가 PDF 바이트를 마크다운으로 읽어 깨진 글자를 조용히 색인한다.
 * 오류도 뜨지 않는 종류의 고장이라 짝을 맞춰 본다.
 *
 * <p><b>다만 OOXML 끼리는 구분하지 못한다.</b> tika-core 는 zip 컨테이너 안을 열지 않아
 * pptx 를 {@code .docx} 로 바꿔 올리면 docx 로 판정한다. 그 파일은 저장은 되고 색인 단계에서
 * "docx 를 읽을 수 없습니다"로 실패한다 — 작업 이력에 남는 명시적 실패라 조용히 망가지지는 않는다.
 */
@Service
public class FileStorageService {

    /** Tika 가 zip 컨테이너 안을 못 볼 때 주는 값. 확장자로 형식을 가른다 */
    private static final Set<String> OOXML_GENERIC = Set.of("application/x-tika-ooxml", "application/zip");

    /**
     * 확장자별로 허용하는 판정 결과.
     *
     * md 와 txt 가 서로의 타입을 받아 주는 것은 둘을 가르는 것이 의미가 없기 때문이다 —
     * 어느 쪽이든 글자이고, 파싱 방법은 확장자가 정한다.
     */
    private static final Map<String, Set<String>> ALLOWED = Map.of(
            "txt", Set.of("text/plain", "text/markdown"),
            "md", Set.of("text/markdown", "text/plain"),
            "pdf", Set.of("application/pdf"),
            "docx", ooxml("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "pptx", ooxml("application/vnd.openxmlformats-officedocument.presentationml.presentation"),
            "xlsx", ooxml("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    );

    /** 사용자에게 보여 주는 순서대로. 오류 문구와 화면 안내가 이 값을 쓴다 */
    public static final List<String> ALLOWED_EXTENSIONS = List.of("md", "txt", "pdf", "docx", "pptx", "xlsx");

    private static Set<String> ooxml(String specific) {
        Set<String> types = new HashSet<>(OOXML_GENERIC);
        types.add(specific);
        return Set.copyOf(types);
    }

    private final S3Client s3;
    private final S3Presigner presigner;
    private final StorageProperties properties;
    private final Tika tika = new Tika();

    public FileStorageService(S3Client s3, S3Presigner presigner, StorageProperties properties) {
        this.s3 = s3;
        this.presigner = presigner;
        this.properties = properties;
    }

    /**
     * 업로드한 파일을 그대로 보관한다.
     *
     * @return 저장 키, 판정된 MIME 타입, 크기, SHA-256 체크섬
     */
    public StoredFile store(Long documentId, int version, MultipartFile file) throws IOException {
        if (file.getSize() > properties.maxFileSize()) {
            throw new FileTooLargeException(properties.maxFileSize());
        }
        return store(documentId, version, file.getBytes(), file.getOriginalFilename());
    }

    /** 일괄 가져오기처럼 바이트 배열을 이미 들고 있을 때 쓴다. */
    public StoredFile store(Long documentId, int version, byte[] bytes, String originalFilename) {
        if (bytes.length > properties.maxFileSize()) {
            throw new FileTooLargeException(properties.maxFileSize());
        }
        String extension = extensionOf(originalFilename);
        String detected = tika.detect(bytes, originalFilename);

        if (!ALLOWED.getOrDefault(extension, Set.of()).contains(detected)) {
            throw new UnsupportedFileTypeException(detected, extension);
        }

        String checksum = sha256(bytes);
        String key = "documents/%d/v%d/%s".formatted(documentId, version, checksum.substring(0, 16));
        putRaw(key, bytes, detected);
        return new StoredFile(key, detected, bytes.length, checksum);
    }

    /**
     * 형식 검사 없이 그대로 올린다. 일괄 가져오기에서 재시도 가능한 실패 건의 원본을
     * 임시 보관할 때 쓴다 — 실패 사유가 형식 문제일 수도 있어 검사를 통과하지 못할 수 있다.
     */
    public String stage(String key, byte[] bytes) {
        putRaw(key, bytes, "application/octet-stream");
        return key;
    }

    /** 재시도에 성공했거나 더 이상 재시도하지 않기로 한 임시 보관 파일을 지운다. */
    public void unstage(String key) {
        delete(key);
    }

    public byte[] readAll(String storageKey) throws IOException {
        try (InputStream in = open(storageKey)) {
            return in.readAllBytes();
        }
    }

    private void putRaw(String key, byte[] bytes, String contentType) {
        s3.putObject(PutObjectRequest.builder()
                        .bucket(properties.bucket())
                        .key(key)
                        .contentType(contentType)
                        .contentLength((long) bytes.length)
                        .build(),
                RequestBody.fromBytes(bytes));
    }

    /**
     * 유효 시간이 제한된 서명 경로를 발급한다.
     * 목록이나 답변 응답에 미리 담지 않는다. 화면을 읽는 동안 유효 시간이 소진되고
     * 발급 시점의 권한 재확인이 무의미해지기 때문이다.
     */
    public IssuedUrl presignedUrl(String storageKey, String originalFilename) {
        // 한글 파일명을 그대로 보존하려면 RFC 5987 형식이 필요하다
        String encoded = URLEncoder.encode(originalFilename, StandardCharsets.UTF_8).replace("+", "%20");
        String disposition = "attachment; filename*=UTF-8''" + encoded;

        var presigned = presigner.presignGetObject(GetObjectPresignRequest.builder()
                .signatureDuration(properties.urlTtl())
                .getObjectRequest(GetObjectRequest.builder()
                        .bucket(properties.bucket())
                        .key(storageKey)
                        .responseContentDisposition(disposition)
                        .build())
                .build());

        return new IssuedUrl(presigned.url().toString(),
                Instant.now().plus(properties.urlTtl()));
    }

    public InputStream open(String storageKey) {
        return s3.getObject(GetObjectRequest.builder()
                .bucket(properties.bucket()).key(storageKey).build());
    }

    public void delete(String storageKey) {
        s3.deleteObject(DeleteObjectRequest.builder()
                .bucket(properties.bucket()).key(storageKey).build());
    }

    private String extensionOf(String filename) {
        if (filename == null) return "";
        int dot = filename.lastIndexOf('.');
        return dot < 0 ? "" : filename.substring(dot + 1).toLowerCase();
    }

    private String sha256(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (Exception e) {
            throw new IllegalStateException("체크섬 계산에 실패했다", e);
        }
    }

    public record StoredFile(String storageKey, String mimeType, long sizeBytes, String checksum) {
    }

    public record IssuedUrl(String downloadUrl, Instant expiresAt) {
    }

    public static class FileTooLargeException extends RuntimeException {
        public final long limit;
        public FileTooLargeException(long limit) {
            this.limit = limit;
        }
    }

    public static class UnsupportedFileTypeException extends RuntimeException {
        public final String detected;
        public final String extension;
        public UnsupportedFileTypeException(String detected, String extension) {
            this.detected = detected;
            this.extension = extension;
        }
    }
}
