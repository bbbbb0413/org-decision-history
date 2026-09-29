from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from history_shared.logging import current_correlation_id
from history_shared.web import (
    ApiError,
    ErrorCode,
    PageResponse,
    format_instant,
    install_common,
    user_id,
    user_roles,
)


def test_format_instant_matches_jackson_iso_instant():
    utc = timezone.utc
    assert format_instant(datetime(2026, 9, 11, 7, 40, 51, 293230, tzinfo=utc)) == "2026-09-11T07:40:51.293230Z"
    assert format_instant(datetime(2026, 9, 11, 7, 40, 51, 293000, tzinfo=utc)) == "2026-09-11T07:40:51.293Z"
    assert format_instant(datetime(2026, 9, 11, 7, 40, 51, tzinfo=utc)) == "2026-09-11T07:40:51Z"
    kst = timezone(timedelta(hours=9))
    assert format_instant(datetime(2026, 9, 11, 16, 40, 51, tzinfo=kst)) == "2026-09-11T07:40:51Z"
    assert format_instant(None) is None

KEY = "internal-secret"


def make_client(internal_api_key: str = KEY) -> TestClient:
    app = FastAPI()
    install_common(app, internal_api_key=internal_api_key)

    @app.get("/api/items/{item_id}")
    async def get_item(item_id: int, request: Request):
        if item_id == 404:
            raise ApiError(404, ErrorCode.NOT_FOUND, "항목을 찾을 수 없습니다.")
        return {
            "id": item_id,
            "correlationId": current_correlation_id(),
            "userId": user_id(request),
            "roles": sorted(user_roles(request)),
        }

    @app.get("/api/pages", response_model=PageResponse[int])
    async def pages(size: int = 2):
        return PageResponse.of([1, 2], page=0, size=size, total_elements=5)

    return TestClient(app)


def test_missing_or_wrong_internal_key_is_rejected_with_correlation_id():
    client = make_client()
    for headers in ({}, {"X-Internal-Key": "wrong"}):
        response = client.get("/api/items/1", headers=headers)
        assert response.status_code == 401
        body = response.json()
        assert body["code"] == "INTERNAL_AUTH_FAILED"
        assert body["correlationId"] == response.headers["X-Correlation-Id"]
        assert body["correlationId"].startswith("req_")


def test_blank_configured_key_rejects_everything():
    response = make_client(internal_api_key="").get("/api/items/1", headers={"X-Internal-Key": ""})
    assert response.status_code == 401


def test_health_is_open_and_matches_actuator():
    response = make_client().get("/actuator/health")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


def test_health_accepts_head_like_spring_actuator():
    # compose 헬스체크(`wget --spider`)는 HEAD를 보낸다. Spring MVC는 GET 매핑에 HEAD도 자동 응답한다
    response = make_client().head("/actuator/health")
    assert response.status_code == 200


def test_metrics_is_open_and_uses_prometheus_format():
    """Spring actuator 와 같은 경로·형식으로 내고, 내부 키 없이 열린다."""
    response = make_client().get("/actuator/prometheus")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    # 노출 형식이 맞는지(주석 헤더가 있는지)만 본다. 어떤 지표가 있는지는 각 지표 쪽 테스트가 본다
    assert "# HELP" in response.text


def test_metrics_reports_service_name_when_given():
    app = FastAPI()
    install_common(app, internal_api_key=KEY, service="test-worker")

    body = TestClient(app).get("/actuator/prometheus").text

    assert 'app_info{service="test-worker"} 1.0' in body


def test_correlation_id_and_user_headers_are_propagated():
    response = make_client().get(
        "/api/items/1",
        headers={
            "X-Internal-Key": KEY,
            "X-Correlation-Id": "req_abc",
            "X-User-Id": "42",
            "X-User-Roles": "SYSTEM_ADMIN, USER",
        },
    )
    assert response.status_code == 200
    assert response.headers["X-Correlation-Id"] == "req_abc"
    assert response.json() == {"id": 1, "correlationId": "req_abc", "userId": 42, "roles": ["SYSTEM_ADMIN", "USER"]}


def test_api_error_uses_error_response_shape_without_nulls():
    response = make_client().get("/api/items/404", headers={"X-Internal-Key": KEY, "X-Correlation-Id": "req_x"})
    assert response.status_code == 404
    assert response.json() == {"code": "NOT_FOUND", "message": "항목을 찾을 수 없습니다.", "correlationId": "req_x"}


def test_validation_error_is_400_with_field_details():
    response = make_client().get("/api/items/abc", headers={"X-Internal-Key": KEY})
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"
    assert body["details"][0]["field"] == "item_id"


def test_page_response_shape():
    client = make_client()
    assert client.get("/api/pages", headers={"X-Internal-Key": KEY}).json() == {
        "content": [1, 2],
        "page": 0,
        "size": 2,
        "totalElements": 5,
        "totalPages": 3,
    }
    assert client.get("/api/pages?size=0", headers={"X-Internal-Key": KEY}).json()["totalPages"] == 0
