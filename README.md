# GitLab MR 자동 AI 코드 리뷰

MR(Merge Request) 생성 또는 업데이트 시 AI Gateway를 통해 자동으로 코드 리뷰를 수행하고, 결과를 MR 코멘트로 등록하는 GitLab CI 파이프라인입니다.

---

## 동작 흐름

```
MR 생성/업데이트
       │
       ▼
GitLab CI 트리거 (merge_request_event)
       │
       ▼
gitlab_code_review.py 실행
  1. GitLab API → MR diff 조회
  2. AI Gateway → 코드 리뷰 요청 (SSE 스트림)
  3. GitLab API → MR 코멘트 등록
```

---

## 파일 구성

| 파일 | 설명 |
|------|------|
| `.gitlab-ci.yml` | CI 파이프라인 정의 |
| `gitlab_code_review.py` | 코드 리뷰 실행 스크립트 |

---

## GitLab CI 설정 (`gitlab-ci.yml`)

### 스테이지

```yaml
stages:
  - code-review
```

`code-review` 단일 스테이지만 사용합니다.

### 트리거 조건

```yaml
rules:
  - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
```

MR이 생성되거나 새 커밋이 푸시될 때만 파이프라인이 실행됩니다. 일반 브랜치 푸시에는 동작하지 않습니다.

특정 브랜치(예: `dev`)로의 MR만 대상으로 제한하려면 아래처럼 조건을 변경합니다.

```yaml
rules:
  - if: '$CI_PIPELINE_SOURCE == "merge_request_event" && $CI_MERGE_REQUEST_TARGET_BRANCH_NAME == "dev"'
```

### 실행 환경

```yaml
image: python:3.11-slim
```

경량 Python 3.11 이미지를 사용하며, 실행 시 `requests` 라이브러리를 설치합니다.

```yaml
script:
  - pip install requests --quiet
  - python gitlab_code_review.py
```

### 주요 옵션

| 옵션 | 값 | 설명 |
|------|----|------|
| `GIT_STRATEGY` | `none` | 소스코드를 체크아웃하지 않음. diff는 GitLab API로 직접 조회 |
| `allow_failure` | `true` | 리뷰 실패 시에도 MR 머지를 차단하지 않음 |

---

## 필수 CI/CD 변수 등록

**GitLab 프로젝트 → Settings → CI/CD → Variables** 에서 아래 변수를 등록합니다.

| 변수명 | 필수 | Masked | 설명 |
|--------|------|--------|------|
| `GATEWAY_URL` | ✅ | | AI Gateway 내부 URL (예: `http://aigateway:9001`) |
| `GATEWAY_API_KEY` | ✅ | ✅ | AI Gateway 인증 키 |
| `GITLAB_INTERNAL_URL` | ✅ | | GitLab 내부 접근 URL (예: `http://gitlab.internal`) |
| `GITLAB_TOKEN` | | ✅ | GitLab Project/Personal Access Token (`api` scope). 미설정 시 `CI_JOB_TOKEN` 자동 사용 |

> `CI_JOB_TOKEN`은 GitLab이 파이프라인 실행 시 자동으로 주입하므로 별도 발급이 필요 없습니다.
> 단, `CI_JOB_TOKEN`은 동일 프로젝트 API 접근 권한만 가지므로, 외부 프로젝트에 접근이 필요한 경우 `GITLAB_TOKEN`을 별도 등록하세요.

### 선택 변수 (기본값으로 동작)

| 변수명 | 기본값 | 설명 |
|--------|--------|------|
| `AI_SERVICE_NAME` | `claude` | AI Gateway에서 사용할 서비스명 |
| `AI_MODEL_CODE` | `claude-sonnet-4-5` | 사용할 모델 코드 |
| `MAX_DIFF_CHARS` | `20000` | diff 최대 길이 (초과 시 잘림) |

---

## 코드 리뷰 항목

AI가 아래 5가지 항목을 기준으로 리뷰를 수행합니다.

1. **버그 및 오류 가능성** — NPE, 경계값 오류, 예외 처리 누락
2. **보안 취약점** — SQL 인젝션, 인증/인가 누락, OWASP Top 10
3. **코드 품질** — 가독성, 네이밍, 중복 코드
4. **성능** — N+1 쿼리, 메모리 누수, 비효율적 자료구조
5. **개선 제안** — 코드 예시를 포함한 구체적 대안 제시

리뷰 완료 후 MR에 코멘트가 자동 등록되며, 마지막에 **10점 만점 코드 품질 점수**가 포함됩니다.

---

## 사용 흐름 예시

1. 개발자가 MR을 생성합니다.
2. GitLab CI가 `code-review` 잡을 자동 실행합니다.
3. 스크립트가 MR diff를 읽어 AI Gateway에 리뷰를 요청합니다.
4. 결과가 MR 코멘트로 등록됩니다.

```
## AI 코드 리뷰

### 1. 버그 및 오류 가능성
✅ 문제 없음

### 2. 보안 취약점
⚠️ line 42: 사용자 입력이 SQL 쿼리에 직접 삽입되고 있습니다. ...

...

**전체 코드 품질 점수: 7 / 10**

---
*AI Gateway (aiconnect / claude) 자동 리뷰*
```

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| `401 Unauthorized` | 토큰이 유효하지 않음 | `GITLAB_TOKEN` 또는 `CI_JOB_TOKEN` 확인 |
| `403 Forbidden` | 토큰 스코프 부족 | `GITLAB_TOKEN`에 `api` scope 부여 |
| `404 Not Found` | GitLab 내부 URL 오류 | `GITLAB_INTERNAL_URL` 값 확인 |
| AI 응답 없음 | Gateway 연결 실패 | `GATEWAY_URL`, `GATEWAY_API_KEY` 확인 |
| diff 일부 누락 | diff가 MAX_DIFF_CHARS 초과 | `MAX_DIFF_CHARS` 값 증가 |
