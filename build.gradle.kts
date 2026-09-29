plugins {
    java
    alias(libs.plugins.springBoot) apply false
}

// 버전 카탈로그 접근자(libs)는 subprojects 블록 안에서는 생성되지 않는다.
// 최상위에서 미리 값을 붙잡아 하위 프로젝트 설정에 넘긴다.
val javaToolchainVersion = libs.versions.java.get().toInt()
val springBootBom = libs.springBootBom
val springCloudBom = libs.springCloudBom

allprojects {
    group = "com.odh"
    version = "0.1.0"
}

subprojects {
    apply(plugin = "java")

    repositories {
        mavenCentral()
    }

    extensions.configure<JavaPluginExtension> {
        toolchain {
            languageVersion.set(JavaLanguageVersion.of(javaToolchainVersion))
        }
    }

    dependencies {
        // BOM 두 개로 모든 스프링 의존성의 버전을 고정한다.
        add("implementation", platform(springBootBom))
        add("implementation", platform(springCloudBom))
        add("testImplementation", platform(springBootBom))
    }

    // java-test-fixtures 를 쓰는 모듈(common)에도 같은 BOM 을 얹는다. 이게 없으면
    // testFixtures 쪽 의존성만 버전이 안 정해져 "Could not find ...:" 로 죽는다.
    plugins.withId("java-test-fixtures") {
        dependencies {
            add("testFixturesImplementation", platform(springBootBom))
        }
    }

    tasks.withType<Test>().configureEach {
        useJUnitPlatform()
    }

    tasks.withType<JavaCompile>().configureEach {
        options.encoding = "UTF-8"
        options.compilerArgs.add("-parameters")
    }
}
