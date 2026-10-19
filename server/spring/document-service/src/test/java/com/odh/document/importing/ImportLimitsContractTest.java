package com.odh.document.importing;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 가져오기 단계의 xlsx 상한이 색인 단계(index-worker parse.py)와 같은지 계약 파일로 확인한다.
 * 한쪽만 바꾸면 메타데이터 추출과 색인이 서로 다른 범위를 본다.
 */
class ImportLimitsContractTest {

    @Test
    void xlsxLimitsMatchContract() throws IOException {
        JsonNode limits = new ObjectMapper().readTree(contractsDir().resolve("indexing/limits.json").toFile());

        assertThat(DocumentMetadataExtractor.MAX_XLSX_ROWS).isEqualTo(limits.get("xlsxMaxRows").asInt());
        assertThat(DocumentMetadataExtractor.MAX_CELL_CHARS).isEqualTo(limits.get("xlsxMaxCellChars").asInt());
    }

    private static Path contractsDir() {
        Path dir = Path.of("").toAbsolutePath();
        while (dir != null && !Files.isDirectory(dir.resolve("contracts"))) {
            dir = dir.getParent();
        }
        if (dir == null) {
            throw new IllegalStateException("contracts 디렉터리를 찾지 못했다. 작업 디렉터리: " + Path.of("").toAbsolutePath());
        }
        return dir.resolve("contracts");
    }
}
