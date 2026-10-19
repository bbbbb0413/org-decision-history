#!/usr/bin/env python3
"""원본 파일이 붙은 데모 문서를 실제 API 로 등록하고, 그 문서를 인용하는 질문·평가를 남긴다.

## 왜 SQL 시드가 아니라 스크립트인가

`auth-service` 의 `db/seed` 는 조직·사용자·정책만 심는다. 문서는 SQL 로 심을 수 없다 —
원본 파일의 **바이트가 MinIO 에 들어가야** 하고, 색인이 돌아야 청크·벡터가 생긴다.
SQL 로 `document_file` 행만 넣으면 "원본 받기"를 눌렀을 때 저장소에 없는 키를 가리켜
서명 URL 이 404 를 준다. 실제 업로드 경로를 그대로 통과시키는 것이 목적이다.

## 일괄 가져오기(import) 경로를 쓰는 이유

`POST /api/documents` 로 만들고 나서 `POST /api/documents/{id}/file` 로 파일을 붙이면
생성 이벤트가 파일보다 먼저 처리돼 한 번은 본문(`content.md`)으로 색인된다(파일 업로드가
재색인을 다시 걸어 결국 맞춰지지만 색인이 두 번 돈다).

`POST /api/document-imports` 는 문서·파일·아웃박스 이벤트를 **한 트랜잭션**에서
만든다(`ImportItemService.attemptImport`). 색인기가 그 문서를 읽을 때는 이미
`document_file` 이 있어서 처음부터 원본 파일에서 파싱한다.

가져오기는 `decision_status` 를 `UNDECIDED` 로 두고 `project_name` 을 비운다. 수정해서 채워도
원본 파일은 유지된다 — 원본은 "현재 판 이하에서 가장 최근에 올라온 것"을 쓴다.

## md 와 pdf 를 섞은 이유

같은 "원본 파일이 붙은 문서"인데 얻는 것이 다르다. 이 대비가 데모의 내용이다.

| 원본 | section_path | 좌표(bboxes) |
|---|---|---|
| `.md`  | 있다 (헤딩이 절 경로가 된다) | 없다 |
| `.pdf` | 있다 (글자 크기로 헤딩을 추정한다) | 있다 (PDF 전용) |

## 다시 돌려도 안전하다

제목이 같은 문서가 이미 있으면 건너뛴다. 확인은 `--check` 로 한다.
"""
import argparse
import datetime
import json
import mimetypes
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8080"
PASSWORD = "password123"
DOCUMENTS = pathlib.Path(__file__).parent / "documents"

# 절 구조를 가진 문서는 item-3 의 문서 템플릿(`front/src/lib/documentTemplates.ts`) 절 이름을
# 그대로 따른다. 템플릿이 실제로 어떤 section_path·chunk_tag 를 만드는지 데모에서 보여준다.
MANIFEST = [
    {
        "file": "01_정보보안팀_접근권한_정기검토_회의록.md",
        "type": "MEETING_NOTE",
        "organization": "보안본부",
        "team": "정보보안팀",
        # member 가 올려 author_id 가 member 가 된다 — MEMBER 관점에서 "내가 쓴 문서"와
        # 작성자 개인 알림이 데모에서 동작하게 하려면 admin 이 아닌 사람이 쓴 문서가 필요하다.
        "importer": "member@company.com",
    },
    {
        "file": "02_사내데이터_외부반출_승인정책.md",
        "type": "POLICY_DOC",
        "organization": "보안본부",
        # 팀을 비우면 조직 범위(scope_key = org:{id})라 보안본부 전원이 본다.
        "team": None,
        "importer": "member@company.com",
    },
    {
        "file": "03_인증팀_다단계인증_도입_설계문서.md",
        "type": "DESIGN_DOC",
        "organization": "플랫폼본부",
        "team": "인증팀",
        "importer": "lead@company.com",
    },
    {
        "file": "04_보안로그_보관기간_결정기록.pdf",
        "type": "DECISION_RECORD",
        "organization": "보안본부",
        "team": "정보보안팀",
        "importer": "member@company.com",
    },
]

# 새로 심은 문서를 실제로 인용하게 만드는 질문들.
#
# 제품 지표 2(출처 검증 가능 비율)의 분모는 `chat.answer_source`, 즉 **답변이 실제로 인용한
# 출처**다. 문서를 심는 것만으로는 움직이지 않고, 파일이 붙은 문서를 인용하는 답변이 생겨야 한다.
#
# 질문은 답이 새 문서 안에만 있는 것으로 골랐다. `expects` 는 그 문서가 출처에 들어왔는지
# 확인하는 데만 쓰고, 안 들어와도 실패로 보지 않는다 — 검색 결과를 단정하면 임베딩이나
# 모델이 바뀔 때마다 시드가 깨진다.
QUESTIONS = [
    {
        "asker": "member@company.com",
        "question": "사내 데이터를 외부로 반출할 때 승인은 누구에게 받아야 하나요?",
        "expects": "사내 데이터 외부 반출 승인 정책 v1.2",
        "feedback": "HELPFUL",
    },
    {
        "asker": "member@company.com",
        "question": "보안 로그는 종류별로 얼마나 보관하기로 했나요?",
        "expects": "보안 로그 보관 기간 결정 기록",  # PDF — 좌표를 가진 출처가 인용된다
        "feedback": "HELPFUL",
    },
    {
        "asker": "lead@company.com",
        "question": "관리자 계정 다단계 인증을 도입할 때 어떤 대안을 검토했고 왜 TOTP를 골랐나요?",
        "expects": "인증팀 다단계 인증(MFA) 도입 설계",
        "feedback": "HELPFUL",
    },
    {
        "asker": "admin@company.com",
        "question": "퇴사자 계정 권한 회수 기한은 며칠로 정해졌나요?",
        "expects": "2026-03 정보보안팀 접근 권한 정기 검토 회의록",
        # 부정 평가도 하나 남긴다. 피드백 드릴다운은 부정 평가 목록에서 시작하므로,
        # 긍정만 심으면 그 화면이 빈 채로 남아 데모에서 아무것도 보여주지 못한다.
        "feedback": "NOT_HELPFUL",
        "reason": "기한은 맞지만 왜 3영업일인지 근거가 답변에 안 나왔다",
    },
]


# ---- HTTP ----

def encode(path: str) -> str:
    """한글이 든 경로·쿼리를 그대로 urlopen 에 넘기면 UnicodeEncodeError 가 난다."""
    if "?" not in path:
        return urllib.parse.quote(path, safe="/-_.~")
    head, query = path.split("?", 1)
    pairs = []
    for item in query.split("&"):
        if "=" in item:
            key, value = item.split("=", 1)
            pairs.append(f"{urllib.parse.quote(key, safe='')}={urllib.parse.quote(value, safe='')}")
        else:
            pairs.append(urllib.parse.quote(item, safe=""))
    return f"{urllib.parse.quote(head, safe='/-_.~')}?{'&'.join(pairs)}"


def call(method, path, token=None, body=None, raw=None, content_type=None, timeout=120):
    request = urllib.request.Request(BASE + encode(path), method=method)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        request.add_header("Content-Type", "application/json")
    elif raw is not None:
        data = raw
        request.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(request, data, timeout=timeout) as response:
            return response.status, decode(response.read())
    except urllib.error.HTTPError as e:
        return e.code, decode(e.read())


def decode(payload: bytes):
    """질문 응답은 text/event-stream 이라 JSON 이 아니다. 그대로 문자열로 넘긴다."""
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return payload.decode("utf-8", "replace")


def login(email: str) -> str:
    status, body = call("POST", "/api/auth/login", body={"email": email, "password": PASSWORD})
    if status != 200:
        sys.exit(f"로그인 실패: {email} → {status} {body}")
    return body["accessToken"]


def multipart(fields: dict[str, str], filename: str, content: bytes) -> tuple[bytes, str]:
    """표준 라이브러리에는 multipart 인코더가 없어 직접 만든다."""
    boundary = "----demoseed" + str(int(time.time() * 1000))
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    if filename.endswith(".md"):
        mime = "text/markdown"  # mimetypes 가 환경에 따라 md 를 모른다
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode()
    )
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# ---- 조회 헬퍼 ----

def resolve_scopes(token: str) -> dict:
    status, orgs = call("GET", "/api/organizations", token)
    if status != 200:
        sys.exit(f"조직 목록을 읽을 수 없다: {status} {orgs}")
    scopes = {}
    for org in orgs:
        status, teams = call("GET", f"/api/organizations/{org['id']}/teams", token)
        scopes[org["name"]] = {
            "id": org["id"],
            "teams": {t["name"]: t["id"] for t in (teams or [])} if status == 200 else {},
        }
    return scopes


def existing_titles(token: str) -> set[str]:
    titles: set[str] = set()
    page = 0
    while True:
        status, body = call("GET", f"/api/documents?page={page}&size=100", token)
        if status != 200:
            sys.exit(f"문서 목록을 읽을 수 없다: {status} {body}")
        for item in body.get("content", []):
            titles.add(item["title"])
        if page + 1 >= body.get("totalPages", 1):
            return titles
        page += 1


def poll(path: str, token: str):
    """대기 루프용 GET. 기동 직후의 연결 거부·리셋·타임아웃은 "아직 안 됨"으로 보고 다음 회차에 다시 묻는다."""
    try:
        return call("GET", path, token, timeout=10)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        print(f"  (일시 오류, 다시 시도: {e})", file=sys.stderr)
        return None, None


def wait_for_import(token: str, job_key: str, timeout_s: int = 300) -> dict | None:
    """가져오기 작업이 끝날 때까지 기다린다. 202 를 받은 직후에는 항목이 아직 없다(비동기 처리)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status, detail = poll(f"/api/document-imports/{job_key}", token)
        if status == 200 and detail.get("status") in ("COMPLETED", "FAILED"):
            return detail
        time.sleep(1)
    return None


def _instant(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def wait_for_indexing(token: str, document_ids: set[int], since: datetime.datetime, timeout_s: int = 300) -> bool:
    """문서마다 색인 항목이 끝날(COMPLETED) 때까지 기다린다.

    전역 색인 큐가 비었는지만 보면 안 된다 — 가져오기 직후에는 아웃박스 이벤트가 아직 색인기에 닿지 않아
    큐가 "비어" 있고, 그때 질문하면 검색할 청크가 없어 근거 없음으로 끝난다.
    작업은 최신순으로 오므로 since(가져오기 시작 시각) 이전 작업에서 멈춘다. 끝난 작업의 항목은 한 번만 읽는다.
    """
    finished_items: dict[str, list[dict]] = {}
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        statuses: dict[int, set[str]] = {doc: set() for doc in document_ids}
        page = 0
        while True:
            status, body = poll(f"/api/index-jobs?page={page}&size=50", token)
            if status != 200:
                break
            jobs = body.get("content", [])
            reached_older = False
            for job in jobs:
                if _instant(job["requestedAt"]) < since:
                    reached_older = True
                    break
                items = finished_items.get(job["jobKey"])
                if items is None:
                    item_status, items = poll(f"/api/index-jobs/{job['jobKey']}/items", token)
                    items = items if item_status == 200 else []
                    if item_status == 200 and job["status"] in ("COMPLETED", "FAILED"):
                        finished_items[job["jobKey"]] = items
                for item in items:
                    if item.get("documentId") in statuses:
                        statuses[item["documentId"]].add(item.get("status"))
            if reached_older or page + 1 >= body.get("totalPages", 1):
                break
            page += 1

        # 재처리·일괄 재색인으로 같은 문서에 실패 항목과 성공 항목이 함께 있을 수 있다 — 성공이 하나라도 있으면 끝난 것이다
        if all("COMPLETED" in s for s in statuses.values()):
            return True
        failed = [doc for doc, s in statuses.items() if s == {"FAILED"}]
        if failed:
            print(f"  색인 실패: 문서 {failed} — 운영 현황에서 사유를 확인해라", file=sys.stderr)
            return False
        time.sleep(3)
    return False


# ---- 작업 ----

def seed(dry_run: bool) -> int:
    admin = login("admin@company.com")
    scopes = resolve_scopes(admin)
    titles = existing_titles(admin)

    tokens: dict[str, str] = {}
    created = 0
    document_ids: set[int] = set()
    problems: list[str] = []
    # 작업 시각은 서버(DB) 시계다. 이 PC 시계와 어긋나도 놓치지 않게 여유를 둔다
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
    for entry in MANIFEST:
        path = DOCUMENTS / entry["file"]
        if not path.exists():
            sys.exit(f"원본 파일이 없다: {path}")

        org = scopes.get(entry["organization"])
        if org is None:
            sys.exit(f"조직을 찾을 수 없다: {entry['organization']} (조직 시드가 적용됐는지 확인해라)")
        team_id = org["teams"].get(entry["team"]) if entry["team"] else None
        if entry["team"] and team_id is None:
            sys.exit(f"팀을 찾을 수 없다: {entry['organization']} / {entry['team']}")

        # 제목은 가져오기가 본문 첫 줄(md)이나 PDF 메타데이터에서 뽑는다. 여기서 미리 읽어
        # 중복을 판단한다 — 등록해 보고 지우는 것보다 낫다.
        expected = expected_title(path)
        if expected in titles:
            print(f"  건너뜀 (이미 있다): {expected}")
            continue
        if dry_run:
            print(f"  등록 예정: {expected} [{entry['type']}] "
                  f"{entry['organization']}/{entry['team'] or '-'} ← {entry['importer']}")
            continue

        token = tokens.setdefault(entry["importer"], login(entry["importer"]))
        fields = {
            "defaultDocumentType": entry["type"],
            "organizationId": str(org["id"]),
        }
        if team_id is not None:
            fields["teamId"] = str(team_id)
        data, content_type = multipart(fields, path.name, path.read_bytes())
        status, body = call("POST", "/api/document-imports", token, raw=data, content_type=content_type)
        if status != 202:
            sys.exit(f"가져오기 실패: {path.name} → {status} {body}")

        # 한 파일이 늦거나 실패해도 나머지는 계속 등록한다. 끝에서 한꺼번에 알린다
        detail = wait_for_import(token, body["jobKey"])
        if detail is None:
            problems.append(f"가져오기가 시간 안에 끝나지 않았다: {path.name} (작업 {body['jobKey']})")
            continue
        items = detail.get("items", [])
        failed = [i for i in items if i.get("status") != "SUCCESS"]
        if failed or not items:
            problems.append(f"가져오기가 항목 단위로 실패했다: {path.name} → {failed or detail}")
            continue
        document_ids.add(items[0]["documentId"])
        print(f"  등록: {expected} (문서 {items[0]['documentId']})")
        created += 1

    if created and not dry_run:
        print("색인을 기다린다...")
        if wait_for_indexing(admin, document_ids, since):
            print(f"색인이 끝났다({len(document_ids)}건).")
        else:
            problems.append("색인이 시간 안에 끝나지 않았거나 실패했다. 운영 현황에서 확인해라.")
    if problems:
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        sys.exit(1)
    return created


def ask_and_rate(token: str, entry: dict) -> bool:
    """질문 한 건을 던지고 답변에 평가를 남긴다. 성공했으면 True."""
    status, conversation = call("POST", "/api/conversations", token, body={})
    if status != 201:
        print(f"  대화 생성 실패: {status} {conversation}", file=sys.stderr)
        return False
    conversation_id = conversation["id"]

    # 응답은 SSE 다. 스트림을 파싱하지 않고 끝까지 읽어 버린 뒤 메시지 목록을 다시 읽는다 —
    # 프론트도 스트림이 끝나면 목록을 재조회한다.
    status, _ = call("POST", f"/api/conversations/{conversation_id}/messages", token,
                     body={"content": entry["question"]}, timeout=240)
    if status != 200:
        print(f"  질문 실패: {status}", file=sys.stderr)
        return False

    status, messages = call("GET", f"/api/conversations/{conversation_id}/messages", token)
    answers = [m for m in (messages or []) if m.get("role") == "ASSISTANT"]
    if not answers:
        print("  답변이 생성되지 않았다", file=sys.stderr)
        return False
    answer = answers[-1]

    status, sources = call("GET", f"/api/messages/{answer['id']}/sources", token)
    titles = [s.get("documentTitle") for s in (sources or [])] if status == 200 else []
    hit = "O" if entry["expects"] in titles else "X"
    with_file = sum(1 for s in (sources or []) if s.get("fileId")) if status == 200 else 0

    body = {"rating": entry["feedback"]}
    if entry.get("reason"):
        body["reason"] = entry["reason"]
    status, _ = call("PUT", f"/api/messages/{answer['id']}/feedback", token, body=body)
    rated = "평가 남김" if status == 200 else f"평가 실패({status})"

    print(f"  [{answer.get('answerStatus')}] 의도한 문서 인용 {hit} / 출처 {len(titles)}건 중 "
          f"원본 파일 있는 것 {with_file}건 / {rated}")
    print(f"    질문: {entry['question']}")
    return True


def run_chat() -> None:
    admin = login("admin@company.com")
    _, metrics = call("GET", "/api/admin/metrics?days=365", admin)
    sources = (metrics or {}).get("sources") or {}
    if sources.get("withOriginalFile", 0) > 0 and ((metrics or {}).get("feedback") or {}).get("responded", 0) > 0:
        print("  건너뜀: 원본 파일이 붙은 인용과 평가가 이미 있다")
        return

    tokens: dict[str, str] = {}
    for entry in QUESTIONS:
        token = tokens.setdefault(entry["asker"], login(entry["asker"]))
        ask_and_rate(token, entry)


def expected_title(path: pathlib.Path) -> str:
    """가져오기의 `DocumentMetadataExtractor` 가 뽑을 제목을 미리 계산한다."""
    if path.suffix == ".pdf":
        # PDF 는 메타데이터 제목을 쓴다. make_pdf.py 가 넣은 값과 같아야 한다.
        return "보안 로그 보관 기간 결정 기록"
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:200]
    sys.exit(f"제목으로 쓸 첫 줄이 없다: {path}")


def check() -> None:
    """무엇이 심어져 있는지 사람이 읽을 수 있게 보여준다."""
    admin = login("admin@company.com")
    print("== 문서 ==")
    status, body = call("GET", "/api/documents?size=100", admin)
    for item in sorted(body.get("content", []), key=lambda d: d["id"]):
        _, detail = call("GET", f"/api/documents/{item['id']}", admin)
        # Detail 의 원본 파일 필드는 현재 버전 한 건(`file`)이다. 목록에는 없어서 상세를 읽는다.
        info = (detail or {}).get("file")
        suffix = pathlib.Path(info["originalFilename"]).suffix if info else ""
        mark = f"원본 {suffix}" if info else "원본 없음"
        print(f"  {item['id']:>3}  org={item.get('organizationId')} team={item.get('teamId') or '-':<4} "
              f"{mark:<10} {item['title'][:42]}")

    print("\n== 사용자별 보이는 문서 수 ==")
    for email in ("admin@company.com", "lead@company.com", "member@company.com"):
        token = login(email)
        _, listing = call("GET", "/api/documents?size=1", token)
        _, mine = call("GET", "/api/documents?size=1&mine=true", token)
        print(f"  {email:<20} 전체 {listing.get('totalElements', 0):>3}건 / 내 문서 {mine.get('totalElements', 0):>3}건")

    print("\n== 지표 ==")
    _, metrics = call("GET", "/api/admin/metrics?days=365", admin)
    sources = (metrics or {}).get("sources") or {}
    feedback = (metrics or {}).get("feedback") or {}
    cited = sources.get("citedSources", 0)
    with_file = sources.get("withOriginalFile", 0)
    ratio = f"{with_file / cited * 100:.1f}%" if cited else "-"
    print(f"  인용 출처 {cited}건 중 원본 파일 보유 {with_file}건 ({ratio}, 목표 95%) "
          f"/ 좌표 보유 {sources.get('withCoordinates', 0)}건")
    print(f"  평가 {feedback.get('responded', 0)}건 "
          f"(유용 {feedback.get('helpful', 0)} / 아니오 {feedback.get('notHelpful', 0)})")
    if (metrics or {}).get("degraded"):
        print(f"  읽지 못한 구획: {metrics['degraded']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="원본 파일이 붙은 데모 문서를 등록한다")
    parser.add_argument("--check", action="store_true", help="등록하지 않고 현재 상태만 보여준다")
    parser.add_argument("--dry-run", action="store_true", help="무엇을 등록할지만 출력한다")
    parser.add_argument("--no-chat", action="store_true",
                        help="질문·평가 단계를 건너뛴다 (LLM 호출을 피하고 싶을 때)")
    args = parser.parse_args()

    if args.check:
        check()
        return

    print("1단계 — 데모 문서를 등록한다 (이미 있는 제목은 건너뛴다)")
    created = seed(args.dry_run)
    if args.dry_run:
        return
    print(f"  {created}건 등록했다.")

    if args.no_chat:
        print("\n2단계 — 건너뜀 (--no-chat)")
    else:
        # 지표 2 의 분모가 "답변이 인용한 출처"라서, 파일이 붙은 문서를 인용하는 답변이 있어야 움직인다.
        print("\n2단계 — 새 문서를 인용하게 질문하고 평가를 남긴다 (모델 호출이라 몇 분 걸린다)")
        run_chat()

    print("\n== 결과 ==")
    check()


if __name__ == "__main__":
    main()
