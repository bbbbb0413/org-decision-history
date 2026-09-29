# 색인 골든 스냅샷

같은 입력을 넣으면 같은 청크가 나와야 한다는 계약을 파일로 고정한다. 파서·청커를 고치거나
라이브러리를 올렸을 때 검색 결과가 **조용히** 바뀌는 것을 막는 회귀 스냅샷이다. 규칙을 의도해서
바꿀 때는 아래 "다시 만들기"를 따라 사람이 검토하고 갱신한다. 규칙 설명은 [../README.md](../README.md)에 있다.

## 구성

```
golden/
├── cases.json            # 입력 정의 (원본이다. 직접 고친다)
├── fixtures/             # 파일 입력. 모두 합성 데이터다
│   ├── policy-edge-cases.md
│   ├── meeting-notes-long.txt
│   ├── ops-meeting.pdf
│   ├── infra-decision.docx
│   ├── access-review-deck.pptx
│   └── access-review-log.xlsx
└── expected/             # 기대 출력 (자동 재생성기는 없다. "다시 만들기" 참고)
    ├── chunker/<id>.json
    ├── document/<id>.json
    ├── token-estimates.json
    ├── point-ids.json
    └── local-embeddings.json
```

## 입력 (`cases.json`)

| 목록 | 검증 범위 | 필드 |
|---|---|---|
| `chunkerCases` | 청커만. 파서를 거치지 않는다 | `text`, `headings[]`(`level`, `title`, `at`), `header`(`title`, `authoredAt`, `projectName`) |
| `documentCases` | 파싱 → 청킹 → point ID | `filename`, `file` 또는 `content`, `documentId`, `version`, `organizationId`, `teamId`, `header` |
| `tokenCases` | 토큰 추정 | 문자열 목록 |
| `pointIdCases` | point ID | `documentId`, `version`, `chunkNo` |
| `localEmbeddingCases` | API 키가 없을 때의 대체 임베딩(`local-hashing-ngram`) | 문자열 목록. 기대 출력은 0이 아닌 차원만 `{"차원": 값}`으로 남긴다 |

- `headings[].at`: 헤딩 offset은 `text`에서 이 문자열이 **처음** 나오는 위치다. 숫자를 손으로 적지 않으려고 이렇게 했다.
- `documentCases`에 `content`를 주면 원본 파일이 없는 문서처럼 처리한다(파이프라인은 이때 `filename`을 `content.md`로 둔다). 오류 사례처럼 `content`와 다른 `filename`을 함께 주면 그 바이트를 해당 형식으로 파싱한다.

### 사례가 확인하는 동작

| 사례 | 확인하는 동작 |
|---|---|
| `md-edge-cases` | 도입부 절, 수준 건너뛰기, `## 제목 ##`, `#붙은글자`·`#######`는 헤딩 아님, **코드 펜스 안의 `#` 줄도 헤딩으로 인식**, 이모지·NBSP, 표, 700토큰 초과 절의 길이 분할과 겹침 |
| `txt-long-meeting` | txt는 `#` 줄이 있어도 헤딩 없음, 빈 줄이 있는 긴 본문 분할, 영문 태그(`Action item`, `discuss`) |
| `pdf-ops-meeting` | PDF 평문 추출, **글자 크기 기반 헤딩 추정**(18pt 제목 → level 1, 14pt 절 4개 → level 2, 11pt 본문), 2쪽, 표처럼 배치한 글자 |
| `docx-infra-decision` | `Heading1~3` 스타일만 헤딩, `Title` 스타일은 헤딩 아님, 빈 문단, **표가 놓인 자리에 파이프 행으로 남음** |
| `pptx-access-review` | 슬라이드마다 절이 갈림, 제목 개체 틀이 헤딩, **제목 없는 슬라이드는 `슬라이드 N`**(본문에는 적지 않음), 묶은 도형 펼치기, 글자 없는 도형(그림) 건너뛰기, 표 |
| `xlsx-access-review` | 시트 이름이 헤딩, 표의 파이프 행, 빈 행 버리기, 오른쪽 빈 열 버리기, 날짜·불리언 셀 표기, 셀 안 줄바꿈·`|` 처리, **계산된 적 없는 수식은 빈 칸** |
| `content-crlf` | 본문 입력 경로, CRLF에서 헤딩 제목에 `\r`이 붙지 않지만 청크 본문에는 `\r`이 남음 |
| `content-short-policy` | 짧은 본문, `team:` scope |
| `error-*` | 미지원 확장자·손상 PDF·손상 pptx·손상 xlsx → 재시도 불가 |
| `whitespace-strip`, `nbsp-only` | 계약의 `strip()`/`isBlank()`(Java 의미)는 NBSP를 공백으로 보지 않음 |
| `code-block-not-split` | 코드 블록은 끊지 않아 청크가 700토큰을 넘을 수 있음(1,106) |
| `long-body-split-with-overlap` | 목표 400·겹침 60 분할 |
| `tag-*` | 태그 우선순위, 영문 대소문자 무시 |

## 기대 출력

- `chunks[]`: `chunkNo`, `sectionPath`, `chunkTag`, `tokenCount`, `pointId`(document 사례만), `content`
- `parsed`: 파서 출력(`text`, `headings[]`의 `level`·`title`·`offset`). 파서 차이와 청커 차이를 따로 볼 수 있게 남긴다.
- `error`: 파싱 실패 시 `retryable`과 `messagePrefix`(메시지에서 첫 `": "` 앞부분). 뒷부분은 라이브러리 오류 문구라 계약에서 뺀다.
- `offset`은 **UTF-16 코드 단위** 위치다(계약 §7). 테스트가 Python 코드 포인트 위치로 바꿔 비교한다(BMP 밖 문자가 없으면 같다).

## 판정 기준

`server/python/index-worker/tests/test_golden.py`가 비교한다(ci-python).

| 대상 | 기준 |
|---|---|
| `chunker/*`, `token-estimates`, `point-ids` | 완전 일치 |
| `local-embeddings` | 0이 아닌 차원 집합이 같고 값 차이가 1e-6 이하(단정밀도 반올림). `server/python/shared` 테스트가 검증한다 |
| `document/md-*`, `txt-*`, `content-*`, `docx-*`, `pptx-*`, `xlsx-*`, `pdf-*` | 완전 일치 |
| `document/error-*` | `retryable`, `messagePrefix` 일치 |


## 다시 만들기

청킹 규칙을 의도해서 바꿨거나 사례를 추가하면 **Python 쪽에서 계산한 값을 검토한 뒤 `expected/`를 수동으로 갱신**한다. 자동 재생성기는 없다.

```bash
# server/python/index-worker/tests/test_golden.py 를 잠깐 고쳐 계산값을 출력하거나,
# tests/golden.py 의 헬퍼로 새 사례를 직접 돌려보고 결과를 검토한 뒤 expected/*.json에 반영한다
cd server/python/index-worker && python -m pytest tests/test_golden.py -q
git diff contracts/indexing/golden/expected   # 차이를 리뷰한다
```

파일 서식은 **Jackson `DefaultPrettyPrinter`** 서식으로 통일한다(들여쓰기 2칸, `"키" : 값`, 배열은 `[ ` … ` ]`). Python `json.dumps` 기본 서식으로 적으면 한 디렉터리에 두 서식이 섞여 내용 변경이 서식 변경에 묻힌다.

사례를 추가할 때는 `cases.json`에 항목을 넣는다(파일 입력이면 `fixtures/`에 합성 파일을 둔다).
**운영 DB의 문서 본문이나 외부 저작물은 fixture로 넣지 않는다.** 운영 데이터와 대조할 때는 해시와 수치로만 남긴다.

## fixture 메모

- `ops-meeting.pdf`: reportlab의 내장하지 않는 CID 폰트(`HYGothic-Medium`)로 만들었다. 폰트 파일이 들어가지 않아 라이선스 문제가 없다. pdfplumber 가 한글을 추출하는 것을 확인했다.
- `infra-decision.docx`: python-docx 기본 템플릿이라 스타일 ID가 `Heading1` 형식이다. 한국어 Word에서 만든 문서는 스타일 ID가 `1`, `2`처럼 지역화되어 현재 규칙으로는 헤딩이 잡히지 않는다([../README.md](../README.md#알려진-동작과-한계)).
- `access-review-deck.pptx`, `access-review-log.xlsx`: python-pptx·openpyxl 기본 템플릿으로 만들었다. 폰트 파일이 들어가지 않아 라이선스 문제가 없다. 덱 4번 슬라이드는 제목 개체 틀을 일부러 비웠고, 시트 `합계`의 마지막 칸은 일부러 수식이다 — openpyxl 은 계산 결과를 남기지 않으므로 "계산된 적 없는 수식은 빈 칸"이 그 픽스처로 고정된다.
- 이 두 파일은 **한 번 만들고 고치지 않는다.** 계약 입력이 바뀌면 기대 출력이 왜 바뀌었는지 가릴 수 없다.
