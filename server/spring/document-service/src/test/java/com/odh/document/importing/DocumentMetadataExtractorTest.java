package com.odh.document.importing;

import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDDocumentInformation;
import org.apache.pdfbox.pdmodel.PDPage;
import org.apache.pdfbox.pdmodel.PDPageContentStream;
import org.apache.pdfbox.pdmodel.encryption.AccessPermission;
import org.apache.pdfbox.pdmodel.encryption.StandardProtectionPolicy;
import org.apache.pdfbox.pdmodel.font.PDType1Font;
import org.apache.pdfbox.pdmodel.font.Standard14Fonts;
import org.apache.poi.sl.usermodel.Placeholder;
import org.apache.poi.xslf.usermodel.XMLSlideShow;
import org.apache.poi.xslf.usermodel.XSLFSlide;
import org.apache.poi.xslf.usermodel.XSLFTable;
import org.apache.poi.xslf.usermodel.XSLFTextBox;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.apache.poi.xwpf.usermodel.XWPFDocument;
import org.apache.poi.xwpf.usermodel.XWPFParagraph;
import org.apache.poi.xwpf.usermodel.XWPFRun;
import org.apache.poi.xwpf.usermodel.XWPFTable;
import org.junit.jupiter.api.Test;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.util.Calendar;
import java.util.TimeZone;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 일괄 가져오기의 파일 형식별 본문·메타데이터 추출.
 *
 * 지원하지 않는 확장자를 재시도 가능한 오류로 잘못 분류했던 회귀를 포함한다 —
 * ImportProcessor 가 이 retryable 플래그로 재시도 여부를 가른다.
 */
class DocumentMetadataExtractorTest {

    private final DocumentMetadataExtractor extractor = new DocumentMetadataExtractor();

    @Test
    void markdown_stripsHeadingMarkerFromTitle() {
        String text = "# 회의록 제목\n\n본문 내용입니다.";

        var result = extractor.extract(text.getBytes(StandardCharsets.UTF_8), "notes.md");

        assertThat(result.title()).isEqualTo("회의록 제목");
        assertThat(result.content()).isEqualTo(text);
        assertThat(result.author()).isNull();
        assertThat(result.authoredAt()).isNull();
    }

    @Test
    void plainText_usesFirstNonBlankLineAsTitleWithoutStrippingMarkers() {
        // txt 는 markdown=false 라 '#' 을 헤딩 마커로 취급하지 않고 그대로 둔다
        String text = "\n\n# 이건 본문 첫 줄\n둘째 줄";

        var result = extractor.extract(text.getBytes(StandardCharsets.UTF_8), "notes.txt");

        assertThat(result.title()).isEqualTo("# 이건 본문 첫 줄");
    }

    @Test
    void plainText_titleLongerThan200Chars_isTruncated() {
        String longLine = "가".repeat(250);

        var result = extractor.extract(longLine.getBytes(StandardCharsets.UTF_8), "notes.txt");

        assertThat(result.title()).hasSize(200);
    }

    @Test
    void unsupportedExtension_throwsNonRetryableError() {
        assertThatThrownBy(() -> extractor.extract("data".getBytes(StandardCharsets.UTF_8), "photo.jpg"))
                .isInstanceOf(DocumentMetadataExtractor.ExtractionException.class)
                .satisfies(e -> assertThat(((DocumentMetadataExtractor.ExtractionException) e).retryable).isFalse());
    }

    @Test
    void pdf_usesDocumentInformationMetadataWhenPresent() throws Exception {
        Calendar created = Calendar.getInstance(TimeZone.getTimeZone("UTC"));
        created.set(2024, Calendar.MARCH, 15, 0, 0, 0);

        // PDFBox 의 기본 Helvetica 폰트는 한글을 인코딩하지 못하므로 페이지 본문은 ASCII 로 쓴다.
        // 제목·작성자는 PDDocumentInformation 메타데이터일 뿐 페이지에 그려지지 않으므로 한글이어도 된다.
        byte[] pdf = buildPdf("First line of body.", "결정 회의록", "박서준", created);

        var result = extractor.extract(pdf, "decision.pdf");

        assertThat(result.title()).isEqualTo("결정 회의록");
        assertThat(result.author()).isEqualTo("박서준");
        assertThat(result.authoredAt()).isEqualTo(LocalDate.of(2024, 3, 15));
        assertThat(result.content()).contains("First line of body.");
    }

    @Test
    void pdf_fallsBackToFirstLineWhenTitleMetadataIsBlank() throws Exception {
        byte[] pdf = buildPdf("This becomes the title instead", null, null, null);

        var result = extractor.extract(pdf, "no-title.pdf");

        assertThat(result.title()).isEqualTo("This becomes the title instead");
        assertThat(result.author()).isNull();
        assertThat(result.authoredAt()).isNull();
    }

    @Test
    void encryptedPdf_throwsNonRetryableError() throws Exception {
        byte[] pdf = buildEncryptedPdf();

        assertThatThrownBy(() -> extractor.extract(pdf, "secret.pdf"))
                .isInstanceOf(DocumentMetadataExtractor.ExtractionException.class)
                .satisfies(e -> assertThat(((DocumentMetadataExtractor.ExtractionException) e).retryable).isFalse());
    }

    @Test
    void docx_usesCorePropertiesMetadataWhenPresent() throws Exception {
        byte[] docx = buildDocx("본문 단락입니다.", "결정 회의록 docx", "이도윤", java.util.Date.from(
                java.time.Instant.parse("2024-06-01T00:00:00Z")));

        var result = extractor.extract(docx, "decision.docx");

        assertThat(result.title()).isEqualTo("결정 회의록 docx");
        assertThat(result.author()).isEqualTo("이도윤");
        assertThat(result.authoredAt()).isEqualTo(LocalDate.of(2024, 6, 1));
        assertThat(result.content()).contains("본문 단락입니다.");
    }

    @Test
    void docx_fallsBackToFirstLineWhenCorePropertiesAreBlank() throws Exception {
        byte[] docx = buildDocx("이것이 대신 제목이 된다", null, null, null);

        var result = extractor.extract(docx, "no-title.docx");

        assertThat(result.title()).isEqualTo("이것이 대신 제목이 된다");
        assertThat(result.author()).isNull();
    }

    @Test
    void docx_keepsTableRowsAsPipeRowsInDocumentOrder() throws Exception {
        // 표를 버리면 "누가 얼마로 결정했는지"가 본문에 남지 않는다. 승인 이력·비용 비교가
        // 표에 들어 있는 문서가 흔해서 검색 결과가 조용히 비었다.
        byte[] docx = buildDocxWithTable();

        var result = extractor.extract(docx, "decision.docx");

        assertThat(result.content().lines().toList()).containsExactly(
                "대안 비교",
                "| 구분 | A안 | B안 |",
                "| 월 비용 | 120만원 | 80만원 |",
                "결정: B안");
    }

    @Test
    void pptx_usesSlideTitleThenBodyAndTable() throws Exception {
        byte[] pptx = buildPptx();

        var result = extractor.extract(pptx, "deck.pptx");

        assertThat(result.title()).isEqualTo("접근권한 개선");
        assertThat(result.author()).isEqualTo("정보보안팀");
        assertThat(result.content().lines().toList()).containsExactly(
                "현황",
                "분기 검토를 수동으로 한다",
                "| 구분 | A안 | B안 |",
                "| 기간 | 4개월 | 6주 |");
    }

    @Test
    void xlsx_writesCellsAsPipeRowsWithSheetNamesAndSkipsEmptyRows() throws Exception {
        byte[] xlsx = buildXlsx();

        var result = extractor.extract(xlsx, "log.xlsx");

        assertThat(result.content().lines().toList()).containsExactly(
                "검토이력",
                "| 검토일 | 결과 |",
                "| 2026-03-02 | 승인 |",
                // 빈 행은 버리고, 오른쪽 빈 열도 버린다. 셀 안 줄바꿈과 파이프는 한 줄로 만든다
                "| 2026-03-09 | 반려 3건 \\| 재검토 |");
    }

    @Test
    void corruptedPptx_throwsNonRetryableError() {
        assertThatThrownBy(() -> extractor.extract("not a pptx".getBytes(StandardCharsets.UTF_8), "broken.pptx"))
                .isInstanceOf(DocumentMetadataExtractor.ExtractionException.class)
                .satisfies(e -> assertThat(((DocumentMetadataExtractor.ExtractionException) e).retryable).isFalse());
    }

    @Test
    void corruptedXlsx_throwsNonRetryableError() {
        assertThatThrownBy(() -> extractor.extract("not an xlsx".getBytes(StandardCharsets.UTF_8), "broken.xlsx"))
                .isInstanceOf(DocumentMetadataExtractor.ExtractionException.class)
                .satisfies(e -> assertThat(((DocumentMetadataExtractor.ExtractionException) e).retryable).isFalse());
    }

    @Test
    void corruptedPdf_throwsNonRetryableError() {
        byte[] garbage = "이건 PDF 가 아니다".getBytes(StandardCharsets.UTF_8);

        assertThatThrownBy(() -> extractor.extract(garbage, "broken.pdf"))
                .isInstanceOf(DocumentMetadataExtractor.ExtractionException.class)
                .satisfies(e -> assertThat(((DocumentMetadataExtractor.ExtractionException) e).retryable).isFalse());
    }

    private byte[] buildPdf(String bodyLine, String title, String author, Calendar created) throws Exception {
        try (PDDocument pdf = new PDDocument()) {
            PDPage page = new PDPage();
            pdf.addPage(page);
            try (PDPageContentStream cs = new PDPageContentStream(pdf, page)) {
                cs.beginText();
                cs.setFont(new PDType1Font(Standard14Fonts.FontName.HELVETICA), 12);
                cs.newLineAtOffset(50, 700);
                cs.showText(bodyLine);
                cs.endText();
            }
            if (title != null || author != null || created != null) {
                PDDocumentInformation info = new PDDocumentInformation();
                if (title != null) info.setTitle(title);
                if (author != null) info.setAuthor(author);
                if (created != null) info.setCreationDate(created);
                pdf.setDocumentInformation(info);
            }
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            pdf.save(out);
            return out.toByteArray();
        }
    }

    private byte[] buildEncryptedPdf() throws Exception {
        try (PDDocument pdf = new PDDocument()) {
            pdf.addPage(new PDPage());
            AccessPermission permission = new AccessPermission();
            StandardProtectionPolicy policy = new StandardProtectionPolicy("owner-pw", "user-pw", permission);
            policy.setEncryptionKeyLength(128);
            pdf.protect(policy);

            ByteArrayOutputStream out = new ByteArrayOutputStream();
            pdf.save(out);
            return out.toByteArray();
        }
    }

    private byte[] buildDocx(String bodyLine, String title, String author, java.util.Date created) throws Exception {
        try (XWPFDocument docx = new XWPFDocument()) {
            XWPFParagraph paragraph = docx.createParagraph();
            XWPFRun run = paragraph.createRun();
            run.setText(bodyLine);

            // POI 는 새 문서에 기본 creator("Apache POI") 를 채워 두므로, "메타데이터 없음"을
            // 재현하려면 명시적으로 빈 문자열로 지워야 한다.
            var core = docx.getProperties().getCoreProperties();
            core.setTitle(title == null ? "" : title);
            core.setCreator(author == null ? "" : author);
            if (created != null) core.setCreated(java.util.Optional.of(created));

            ByteArrayOutputStream out = new ByteArrayOutputStream();
            docx.write(out);
            return out.toByteArray();
        }
    }

    private byte[] buildDocxWithTable() throws Exception {
        try (XWPFDocument docx = new XWPFDocument()) {
            docx.createParagraph().createRun().setText("대안 비교");

            XWPFTable table = docx.createTable(2, 3);
            String[][] rows = {{"구분", "A안", "B안"}, {"월 비용", "120만원", "80만원"}};
            for (int r = 0; r < rows.length; r++) {
                for (int c = 0; c < rows[r].length; c++) {
                    table.getRow(r).getCell(c).setText(rows[r][c]);
                }
            }

            docx.createParagraph().createRun().setText("결정: B안");

            var core = docx.getProperties().getCoreProperties();
            core.setTitle("");
            core.setCreator("");

            ByteArrayOutputStream out = new ByteArrayOutputStream();
            docx.write(out);
            return out.toByteArray();
        }
    }

    private byte[] buildPptx() throws Exception {
        try (XMLSlideShow pptx = new XMLSlideShow()) {
            XSLFSlide slide = pptx.createSlide();

            XSLFTextBox title = slide.createTextBox();
            title.setPlaceholder(Placeholder.TITLE);
            title.setText("현황");

            slide.createTextBox().setText("분기 검토를 수동으로 한다");

            XSLFTable table = slide.createTable(2, 3);
            String[][] rows = {{"구분", "A안", "B안"}, {"기간", "4개월", "6주"}};
            for (int r = 0; r < rows.length; r++) {
                for (int c = 0; c < rows[r].length; c++) {
                    table.getCell(r, c).setText(rows[r][c]);
                }
            }

            var core = pptx.getProperties().getCoreProperties();
            core.setTitle("접근권한 개선");
            core.setCreator("정보보안팀");

            ByteArrayOutputStream out = new ByteArrayOutputStream();
            pptx.write(out);
            return out.toByteArray();
        }
    }

    private byte[] buildXlsx() throws Exception {
        try (XSSFWorkbook xlsx = new XSSFWorkbook()) {
            var sheet = xlsx.createSheet("검토이력");
            String[][] rows = {
                    {"검토일", "결과", ""},
                    {"2026-03-02", "승인", ""},
                    {"", "", ""},                              // 빈 행
                    {"2026-03-09", "반려 3건\n| 재검토", ""},    // 줄바꿈·파이프
            };
            for (int r = 0; r < rows.length; r++) {
                var row = sheet.createRow(r);
                for (int c = 0; c < rows[r].length; c++) {
                    row.createCell(c).setCellValue(rows[r][c]);
                }
            }

            var core = xlsx.getProperties().getCoreProperties();
            core.setTitle("");
            core.setCreator("");

            ByteArrayOutputStream out = new ByteArrayOutputStream();
            xlsx.write(out);
            return out.toByteArray();
        }
    }
}
