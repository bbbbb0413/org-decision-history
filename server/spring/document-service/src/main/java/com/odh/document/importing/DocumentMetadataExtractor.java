package com.odh.document.importing;

import org.apache.pdfbox.Loader;
import org.apache.pdfbox.pdmodel.PDDocument;
import org.apache.pdfbox.pdmodel.PDDocumentInformation;
import org.apache.pdfbox.pdmodel.encryption.InvalidPasswordException;
import org.apache.pdfbox.text.PDFTextStripper;
import org.apache.poi.ooxml.POIXMLProperties;
import org.apache.poi.ss.usermodel.Cell;
import org.apache.poi.ss.usermodel.DataFormatter;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.ss.usermodel.Sheet;
import org.apache.poi.xslf.usermodel.XMLSlideShow;
import org.apache.poi.xslf.usermodel.XSLFGroupShape;
import org.apache.poi.xslf.usermodel.XSLFShape;
import org.apache.poi.xslf.usermodel.XSLFSlide;
import org.apache.poi.xslf.usermodel.XSLFTable;
import org.apache.poi.xslf.usermodel.XSLFTableCell;
import org.apache.poi.xslf.usermodel.XSLFTableRow;
import org.apache.poi.xslf.usermodel.XSLFTextShape;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;
import org.apache.poi.xwpf.usermodel.IBodyElement;
import org.apache.poi.xwpf.usermodel.XWPFDocument;
import org.apache.poi.xwpf.usermodel.XWPFParagraph;
import org.apache.poi.xwpf.usermodel.XWPFTable;
import org.apache.poi.xwpf.usermodel.XWPFTableCell;
import org.apache.poi.xwpf.usermodel.XWPFTableRow;
import org.springframework.stereotype.Component;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Calendar;
import java.util.List;
import java.util.Locale;

/**
 * 일괄 가져오기에서 본문과 제목·작성자·작성일을 추출한다.
 *
 * PDF·docx·pptx·xlsx 는 문서 자체의 메타데이터(제목, 작성자, 생성일)를 우선 쓴다.
 * 메타데이터가 비어 있으면 본문 첫 줄을 제목으로 쓰고 작성자·작성일은 비워 둔다 —
 * 본문을 정규식으로 추측하면 잘못 짚었을 때 조용히 틀린 값이 들어간다.
 *
 * <p>여기서 뽑은 본문은 {@code document.content} 로 저장돼 화면에 보이고, 원본 파일이 없는
 * 문서의 색인 대상이 된다. 색인기(Python {@code parse.py})는 원본 파일을 따로 파싱하므로
 * 두 구현이 글자 하나까지 같을 필요는 없지만, <b>표를 남기는 방식은 맞춰 둔다</b> —
 * 화면에 보이는 본문과 검색에 걸리는 본문이 다르면 사용자가 그 차이를 설명할 수 없다.
 */
@Component
public class DocumentMetadataExtractor {

    /**
     * 스프레드시트에서 읽을 행 수 상한.
     *
     * <p>PDF·pptx 는 쪽·장 수가 지나치면 거절하지만(그만큼 긴 문서는 이 시스템이 다루는 종류가
     * 아니다) 스프레드시트는 정당한 문서도 행이 수만 줄일 수 있다. 거절하면 그 문서는 지식으로
     * 아예 들어오지 못하므로 앞부분만 읽고 <b>잘랐다는 사실을 본문에 남긴다.</b>
     * 색인기(Python {@code parse.py})와 같은 값이어야 한다 — 계약 원본은 {@code contracts/indexing/limits.json} 이고
     * 테스트가 대조한다. 운영 중 바꿀 값이 아니라 두 단계의 약속이라 설정으로 열지 않는다.
     */
    static final int MAX_XLSX_ROWS = 2000;
    private static final String XLSX_TRUNCATED = "〈행이 너무 많아 이후 내용은 색인하지 않았습니다〉";
    static final int MAX_CELL_CHARS = 500;

    public Extracted extract(byte[] bytes, String filename) {
        String extension = extensionOf(filename);
        return switch (extension) {
            case "md" -> fromPlainText(new String(bytes, StandardCharsets.UTF_8), true);
            case "txt" -> fromPlainText(new String(bytes, StandardCharsets.UTF_8), false);
            case "pdf" -> fromPdf(bytes);
            case "docx" -> fromDocx(bytes);
            case "pptx" -> fromPptx(bytes);
            case "xlsx" -> fromXlsx(bytes);
            default -> throw new ExtractionException("지원하지 않는 파일 형식입니다: " + extension, false);
        };
    }

    private Extracted fromPlainText(String text, boolean markdown) {
        String title = firstNonBlankLine(text, markdown);
        return new Extracted(text, title, null, null);
    }

    private Extracted fromPdf(byte[] bytes) {
        try (PDDocument pdf = Loader.loadPDF(bytes)) {
            String text = new PDFTextStripper().getText(pdf);
            PDDocumentInformation info = pdf.getDocumentInformation();
            String title = blankToNull(info == null ? null : info.getTitle());
            String author = blankToNull(info == null ? null : info.getAuthor());
            LocalDate createdAt = info == null ? null : toLocalDate(info.getCreationDate());
            return new Extracted(text, title != null ? title : firstNonBlankLine(text, false), author, createdAt);
        } catch (InvalidPasswordException e) {
            throw new ExtractionException("암호화된 파일이라 본문을 추출할 수 없습니다", false);
        } catch (Exception e) {
            throw new ExtractionException("PDF 를 읽을 수 없습니다: " + e.getMessage(), false);
        }
    }

    private Extracted fromDocx(byte[] bytes) {
        try (XWPFDocument docx = new XWPFDocument(new ByteArrayInputStream(bytes))) {
            StringBuilder text = new StringBuilder();
            // getBodyElements 는 문단과 표를 놓인 순서대로 준다. getParagraphs 는 표를 건너뛴다
            for (IBodyElement element : docx.getBodyElements()) {
                if (element instanceof XWPFParagraph paragraph) {
                    text.append(paragraph.getText()).append('\n');
                } else if (element instanceof XWPFTable table) {
                    for (XWPFTableRow row : table.getRows()) {
                        appendRow(text, row.getTableCells().stream().map(XWPFTableCell::getText).toList());
                    }
                }
            }
            return withCoreProperties(text.toString(), docx.getProperties());
        } catch (Exception e) {
            throw new ExtractionException("docx 를 읽을 수 없습니다: " + e.getMessage(), false);
        }
    }

    private Extracted fromPptx(byte[] bytes) {
        try (XMLSlideShow pptx = new XMLSlideShow(new ByteArrayInputStream(bytes))) {
            StringBuilder text = new StringBuilder();
            for (XSLFSlide slide : pptx.getSlides()) {
                appendShapes(text, slide.getShapes(), slide.getTitle());
            }
            return withCoreProperties(text.toString(), pptx.getProperties());
        } catch (Exception e) {
            throw new ExtractionException("pptx 를 읽을 수 없습니다: " + e.getMessage(), false);
        }
    }

    /** 제목 도형은 슬라이드마다 맨 앞에 한 번만 적는다. 나머지 도형은 놓인 순서대로 잇는다. */
    private void appendShapes(StringBuilder text, List<XSLFShape> shapes, String title) {
        if (title != null && !title.isBlank()) {
            text.append(title.strip()).append('\n');
        }
        for (XSLFShape shape : shapes) {
            if (shape instanceof XSLFGroupShape group) {
                appendShapes(text, group.getShapes(), null);  // 묶은 도형 안에 제목은 없다
            } else if (shape instanceof XSLFTable table) {
                for (XSLFTableRow row : table.getRows()) {
                    appendRow(text, row.getCells().stream().map(XSLFTableCell::getText).toList());
                }
            } else if (shape instanceof XSLFTextShape textShape) {
                String body = textShape.getText();
                if (body != null && !body.isBlank() && !body.strip().equals(title == null ? null : title.strip())) {
                    text.append(body.strip()).append('\n');
                }
            }
        }
    }

    private Extracted fromXlsx(byte[] bytes) {
        try (XSSFWorkbook xlsx = new XSSFWorkbook(new ByteArrayInputStream(bytes))) {
            DataFormatter formatter = new DataFormatter(Locale.KOREA);
            StringBuilder text = new StringBuilder();
            int budget = MAX_XLSX_ROWS;
            for (Sheet sheet : xlsx) {
                text.append(sheet.getSheetName()).append('\n');
                for (Row row : sheet) {
                    if (budget <= 0) break;
                    List<String> values = new ArrayList<>();
                    for (int c = 0; c < row.getLastCellNum(); c++) {
                        Cell cell = row.getCell(c);
                        // 수식은 엑셀이 저장한 계산 결과를 쓴다. 수식 문자열을 색인하면 검색이 오염된다
                        values.add(cell == null ? "" : formatter.formatCellValue(cell));
                    }
                    if (appendRow(text, values)) budget--;
                }
                if (budget <= 0) {
                    text.append(XLSX_TRUNCATED).append('\n');
                    break;
                }
            }
            return withCoreProperties(text.toString(), xlsx.getProperties());
        } catch (Exception e) {
            throw new ExtractionException("xlsx 를 읽을 수 없습니다: " + e.getMessage(), false);
        }
    }

    /**
     * 표 한 행을 마크다운 파이프 행으로 적는다. 셀이 전부 비면 아무것도 적지 않는다.
     *
     * <p>파이프 행을 쓰는 이유는 색인 계약이 이미 그 모양을 알기 때문이다 — 청커가
     * {@code |...|} 줄을 길이 분할에서 끊지 않아 표 한 행이 두 청크로 갈리지 않는다
     * (contracts/indexing/README.md §4.2). 머리글 구분선은 붙이지 않는다. 첫 행이 머리글인지
     * 파일에서 알 수 없고, 아니라면 데이터 한 줄을 머리글로 읽게 만든다.
     *
     * @return 실제로 적었으면 true
     */
    private boolean appendRow(StringBuilder text, List<String> cells) {
        List<String> rendered = new ArrayList<>(cells.size());
        for (String cell : cells) {
            // 셀 안 줄바꿈이 남으면 한 행이 여러 줄로 갈려 파이프 행이 아니게 된다
            String value = cell == null ? "" : cell.replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip();
            rendered.add(value.length() > MAX_CELL_CHARS ? value.substring(0, MAX_CELL_CHARS) : value);
        }
        while (!rendered.isEmpty() && rendered.get(rendered.size() - 1).isEmpty()) {
            rendered.remove(rendered.size() - 1);  // 오른쪽 빈 열은 버린다
        }
        if (rendered.isEmpty()) return false;
        text.append("| ").append(String.join(" | ", rendered)).append(" |\n");
        return true;
    }

    private Extracted withCoreProperties(String body, POIXMLProperties properties) {
        POIXMLProperties.CoreProperties core = properties.getCoreProperties();
        String title = blankToNull(core.getTitle());
        String author = blankToNull(core.getCreator());
        LocalDate createdAt = core.getCreated() == null ? null
                : core.getCreated().toInstant().atZone(ZoneOffset.UTC).toLocalDate();
        return new Extracted(body, title != null ? title : firstNonBlankLine(body, false), author, createdAt);
    }

    private String firstNonBlankLine(String text, boolean markdown) {
        for (String line : text.split("\n")) {
            String trimmed = line.strip();
            if (markdown) trimmed = trimmed.replaceFirst("^#+\\s*", "");
            if (!trimmed.isBlank()) {
                return trimmed.length() > 200 ? trimmed.substring(0, 200) : trimmed;
            }
        }
        return null;
    }

    private LocalDate toLocalDate(Calendar calendar) {
        return calendar == null ? null : calendar.toInstant().atZone(ZoneOffset.UTC).toLocalDate();
    }

    private String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s.strip();
    }

    private String extensionOf(String filename) {
        if (filename == null) return "";
        int dot = filename.lastIndexOf('.');
        return dot < 0 ? "" : filename.substring(dot + 1).toLowerCase();
    }

    /**
     * @param title  추출한 제목. 없으면 null 이다 — 호출자가 파일명으로 대체한다
     * @param author 추출한 작성자. 없으면 null 이다
     * @param authoredAt 추출한 작성일. 없으면 null 이다 — 호출자가 가져오기 시각으로 대체한다
     */
    public record Extracted(String content, String title, String author, LocalDate authoredAt) {
    }

    /** 파싱 자체가 실패한 경우. retryable 은 재시도해도 결과가 같은지를 가른다. */
    public static class ExtractionException extends RuntimeException {
        public final boolean retryable;
        public ExtractionException(String message, boolean retryable) {
            super(message);
            this.retryable = retryable;
        }
    }
}
