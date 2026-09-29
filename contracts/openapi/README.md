# HTTP API 구현 스펙 (생성물)

각 Spring 서비스가 **지금 실제로 여는** API다. springdoc이 컨트롤러·DTO에서 뽑고,
서비스별 `OpenApiSnapshotTest`가 이 파일과 비교한다.

| 파일 | 서비스 | 생성 테스트 |
|---|---|---|
| `auth-service.yml` | auth-service | `com.odh.auth.OpenApiSnapshotTest` |
| `document-service.yml` | document-service | `com.odh.document.OpenApiSnapshotTest` |
| `chat-service.yml` | chat-service | `com.odh.chat.OpenApiSnapshotTest` |

`gateway`는 자기 컨트롤러가 없어(라우팅만 한다) 스펙이 없다. Python 워커 중 HTTP API 가 있는 것은
index-worker(`/api/index-jobs`) 하나이고, 그 계약은 `server/python/index-worker/tests/test_api.py` 가 지킨다.

## 손으로 고치지 않는다

이 파일들은 코드에서 뽑은 것이다. API를 바꾸려면 컨트롤러·DTO를 고치고 테스트를 돌린다.

```
./gradlew :server:spring:auth-service:test --tests '*OpenApiSnapshotTest*'
```

어긋나면 테스트가 파일을 새로 쓰고 **실패한다**. diff를 확인하고 함께 커밋한다.
CI(`CI=true`)에서는 고쳐 쓰지 않고 실패만 시킨다 — CI가 조용히 덮어쓰면 계약 변경이
리뷰를 건너뛴다.


## 노출 범위

`/v3/api-docs(.yaml)`는 내부 키 필터 뒤에 있어 게이트웨이를 통해서는 닿지 않는다.
Swagger UI는 켜지 않는다(`springdoc.swagger-ui.enabled: false`, 의존성도 `-api` 아티팩트다).
