package com.odh.common.contract;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.fail;

/**
 * 서비스가 지금 내는 OpenAPI 스펙을 `contracts/openapi/<서비스>.yml` 과 맞춰 본다
 *
 * <p>`contracts/indexing/golden/` 의 골든 스냅샷과 같은 방침이다 — 로컬에서는 어긋나면
 * 새로 써서 diff 로 보여주고, CI 에서는 고치지 않고 실패시킨다. CI 가 조용히 덮어쓰면
 * 계약 변경이 리뷰를 건너뛴다.
 */
public final class OpenApiSnapshots {

    private OpenApiSnapshots() {
    }

    public static void assertMatches(String service, String actual) {
        Path snapshot = snapshotPath(service);
        String normalized = normalize(actual);
        String expected = Files.exists(snapshot) ? read(snapshot) : null;

        if (normalized.equals(expected)) {
            return;
        }

        // CI 에서는 절대 손대지 않는다 — 작업 트리를 고쳐 놓으면 그 변경이 리뷰 없이 묻힌다.
        if (isCi()) {
            assertThat(normalized)
                    .as("%s 의 OpenAPI 스펙이 커밋된 %s 와 다르다. 로컬에서 테스트를 돌려 갱신한 뒤 커밋해라",
                            service, snapshot)
                    .isEqualTo(expected);
            return;
        }

        write(snapshot, normalized);
        fail("%s 의 OpenAPI 스펙이 %s 와 달라 파일을 새로 썼다(%s). 변경 내용을 확인하고 커밋해라"
                .formatted(service, snapshot, expected == null ? "없던 파일을 생성" : "기존 파일 갱신"));
    }

    /** 줄 끝 공백·개행 표기를 고정한다. 플랫폼에 따라 달라지면 매번 diff 가 난다. */
    private static String normalize(String yaml) {
        String body = yaml.replace("\r\n", "\n").stripTrailing();
        return body + "\n";
    }

    private static Path snapshotPath(String service) {
        return repoRoot().resolve("contracts/openapi").resolve(service + ".yml");
    }

    /**
     * 테스트의 작업 디렉터리는 Gradle 이 각 모듈 디렉터리로 잡아 준다. 저장소 루트는
     * `contracts/` 가 있는 첫 상위 디렉터리로 찾는다 — 모듈이 어디로 옮겨져도 따라간다.
     */
    private static Path repoRoot() {
        Path dir = Path.of("").toAbsolutePath();
        while (dir != null) {
            if (Files.isDirectory(dir.resolve("contracts"))) {
                return dir;
            }
            dir = dir.getParent();
        }
        throw new IllegalStateException("contracts 디렉터리를 찾지 못했다. 작업 디렉터리: "
                + Path.of("").toAbsolutePath());
    }

    private static boolean isCi() {
        String ci = System.getenv("CI");
        return ci != null && !ci.isBlank() && !"false".equalsIgnoreCase(ci);
    }

    private static String read(Path path) {
        try {
            return Files.readString(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private static void write(Path path, String content) {
        try {
            Files.createDirectories(path.getParent());
            Files.writeString(path, content, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
