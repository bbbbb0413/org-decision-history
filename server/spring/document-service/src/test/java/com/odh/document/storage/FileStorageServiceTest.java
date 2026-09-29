package com.odh.document.storage;

import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.poi.xslf.usermodel.XMLSlideShow;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.apache.poi.xwpf.usermodel.XWPFDocument;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import software.amazon.awssdk.services.s3.S3Client;
import software.amazon.awssdk.services.s3.model.PutObjectRequest;
import software.amazon.awssdk.services.s3.presigner.S3Presigner;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

/**
 * 업로드 형식 검사.
 *
 * 검사는 <b>확장자와 시그니처가 맞는지</b>까지 본다. 둘을 따로 보면 PDF 를 {@code .md} 로 바꿔
 * 올렸을 때 통과하고, 색인기가 PDF 바이트를 마크다운으로 읽어 깨진 글자를 조용히 색인한다.
 * 이 테스트가 그 짝맞춤을 고정한다.
 */
class FileStorageServiceTest {

    private final S3Client s3 = mock(S3Client.class);
    private final StorageProperties properties = new StorageProperties(
            "http://minio:9000", null, "documents", "key", "secret", null, Duration.ofSeconds(60), 50L * 1024 * 1024);
    private final FileStorageService storage = new FileStorageService(s3, mock(S3Presigner.class), properties);

    @ParameterizedTest(name = "{0}")
    @CsvSource({
            "note.md, md",
            "note.txt, txt",
            "decision.pdf, pdf",
            "decision.docx, docx",
            "deck.pptx, pptx",
            "log.xlsx, xlsx",
    })
    void storesEveryAllowedFormat(String filename, String kind) {
        var stored = storage.store(7L, 2, bytesOf(kind), filename);

        assertThat(stored.storageKey()).startsWith("documents/7/v2/");
        assertThat(stored.checksum()).hasSize(64);
        verify(s3).putObject(any(PutObjectRequest.class), any(software.amazon.awssdk.core.sync.RequestBody.class));
    }

    @ParameterizedTest(name = "{1} 바이트를 {0} 로 올리면 거부한다")
    @CsvSource({
            "trick.md, pdf",
            "trick.txt, pdf",
            "trick.pdf, txt",
            "trick.docx, pdf",
    })
    void rejectsWhenExtensionDoesNotMatchSignature(String filename, String kind) {
        assertThatThrownBy(() -> storage.store(7L, 1, bytesOf(kind), filename))
                .isInstanceOf(FileStorageService.UnsupportedFileTypeException.class);

        verify(s3, never()).putObject(any(PutObjectRequest.class), any(software.amazon.awssdk.core.sync.RequestBody.class));
    }

    @Test
    void rejectsUnknownExtension() {
        assertThatThrownBy(() -> storage.store(7L, 1, bytesOf("txt"), "legacy.hwp"))
                .isInstanceOf(FileStorageService.UnsupportedFileTypeException.class)
                .satisfies(e -> assertThat(((FileStorageService.UnsupportedFileTypeException) e).extension).isEqualTo("hwp"));
    }

    @Test
    void ooxmlSubtypesAreNotToldApart() {
        // tika-core 는 zip 컨테이너 안을 열지 않아 pptx 를 .docx 로 바꿔 올리면 docx 로 판정한다.
        // 저장은 되고 색인 단계에서 "docx 를 읽을 수 없습니다"로 실패한다 — 작업 이력에 남는
        // 명시적 실패다. 숨은 한계라서 테스트로 적어 둔다. 고치려면 컨테이너를 직접 열어야 한다.
        var stored = storage.store(7L, 1, bytesOf("pptx"), "renamed.docx");

        assertThat(stored.mimeType())
                .isEqualTo("application/vnd.openxmlformats-officedocument.wordprocessingml.document");
    }

    @Test
    void rejectsFileOverSizeLimit() {
        var small = new StorageProperties("http://minio:9000", null, "documents", "k", "s", null, Duration.ofSeconds(60), 10);
        var limited = new FileStorageService(s3, mock(S3Presigner.class), small);

        assertThatThrownBy(() -> limited.store(7L, 1, bytesOf("txt"), "note.txt"))
                .isInstanceOf(FileStorageService.FileTooLargeException.class);
    }

    /** 형식마다 Tika 가 실제로 알아보는 최소 바이트. 시그니처를 손으로 적지 않으려고 라이브러리로 만든다. */
    private byte[] bytesOf(String kind) {
        try {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            switch (kind) {
                case "md" -> out.write("# 제목\n본문입니다.\n".getBytes(StandardCharsets.UTF_8));
                case "txt" -> out.write("본문만 있는 메모입니다.\n".getBytes(StandardCharsets.UTF_8));
                case "pdf" -> {
                    try (PDDocument pdf = new PDDocument()) {
                        pdf.addPage(new PDPage());
                        pdf.save(out);
                    }
                }
                case "docx" -> {
                    try (XWPFDocument docx = new XWPFDocument()) {
                        docx.createParagraph().createRun().setText("본문");
                        docx.write(out);
                    }
                }
                case "pptx" -> {
                    try (XMLSlideShow pptx = new XMLSlideShow()) {
                        pptx.createSlide();
                        pptx.write(out);
                    }
                }
                case "xlsx" -> {
                    try (XSSFWorkbook xlsx = new XSSFWorkbook()) {
                        xlsx.createSheet("시트").createRow(0).createCell(0).setCellValue("값");
                        xlsx.write(out);
                    }
                }
                default -> throw new IllegalArgumentException(kind);
            }
            return out.toByteArray();
        } catch (Exception e) {
            throw new IllegalStateException(kind + " 바이트를 만들지 못했다", e);
        }
    }
}
