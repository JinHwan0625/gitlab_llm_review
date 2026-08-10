#!/usr/bin/env python3
"""GitLab MR 생성/업데이트 시 AI Gateway를 통해 자동 코드 리뷰를 수행하고 MR에 코멘트를 등록합니다."""

import json
import os
import sys

import requests

# ── GitLab CI 내장 환경 변수 ─────────────────────────────────────────────────
PROJECT_ID = os.environ["CI_PROJECT_ID"]
MR_IID = os.environ["CI_MERGE_REQUEST_IID"]
MR_TITLE = os.environ.get("CI_MERGE_REQUEST_TITLE", "")
SOURCE_BRANCH = os.environ.get("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME", "")
TARGET_BRANCH = os.environ.get("CI_MERGE_REQUEST_TARGET_BRANCH_NAME", "")

# ── GitLab CI 자동 주입 토큰 (별도 발급 불필요) ──────────────────────────────
CI_JOB_TOKEN = os.environ["CI_JOB_TOKEN"]

# ── CI Variables (GitLab Settings > CI/CD > Variables) ───────────────────────
# 상수값으로 처리해도 무방
AI_GATEWAY_URL = os.environ["GATEWAY_URL"].rstrip("/")
AI_GATEWAY_API_KEY = os.environ["GATEWAY_API_KEY"]
AI_SERVICE_NAME = os.environ.get("AI_SERVICE_NAME", "claude")
AI_MODEL_CODE = os.environ.get("AI_MODEL_CODE", "claude-sonnet-4-5")
MAX_DIFF_CHARS = int(os.environ.get("MAX_DIFF_CHARS", "20000"))

_raw_internal_url = os.environ.get("GITLAB_INTERNAL_URL").rstrip("/")
GITLAB_INTERNAL_URL = _raw_internal_url if _raw_internal_url.startswith(("https://", "http://")) else f"http://{_raw_internal_url}"
GITLAB_API = f"{GITLAB_INTERNAL_URL}/api/v4"
# GITLAB_TOKEN: Project/Personal Access Token (api scope), CI Variables에 등록 필요
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN") or CI_JOB_TOKEN
GITLAB_HEADERS = {"PRIVATE-TOKEN": GITLAB_TOKEN} if os.environ.get("GITLAB_TOKEN") else {"JOB-TOKEN": CI_JOB_TOKEN}


def _handle_gitlab_api_error(resp: requests.Response, context: str) -> None:
    if resp.status_code == 401:
        token_type = "PRIVATE-TOKEN" if os.environ.get("GITLAB_TOKEN") else "JOB-TOKEN"
        print(f"[ERROR] GitLab 인증 실패 ({context}): {token_type}이 유효하지 않습니다.", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 403:
        print(f"[ERROR] GitLab 권한 없음 ({context}): 토큰에 api 스코프가 필요합니다.", file=sys.stderr)
        sys.exit(1)
    if resp.status_code == 404:
        print(f"[ERROR] GitLab 리소스 없음 ({context}): URL={resp.url}", file=sys.stderr)
        sys.exit(1)
    resp.raise_for_status()


def fetch_mr_diff() -> str:
    """GitLab API로 MR diff를 가져옵니다."""
    url = f"{GITLAB_API}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/diffs"
    resp = requests.get(url, headers=GITLAB_HEADERS, timeout=30)
    _handle_gitlab_api_error(resp, "fetch_mr_diff")

    lines = []
    for file_diff in resp.json():
        if file_diff.get("diff"):
            lines.append(f"--- {file_diff.get('old_path', '')}")
            lines.append(f"+++ {file_diff.get('new_path', '')}")
            lines.append(file_diff["diff"])

    diff = "\n".join(lines)
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n\n... (diff가 너무 길어 일부 생략됨)"
    return diff


def build_prompt(diff: str) -> str:
    return f"""당신은 숙련된 소프트웨어 엔지니어입니다. 아래 GitLab Merge Request의 코드 변경사항을 리뷰해주세요.

**MR 제목**: {MR_TITLE}
**브랜치**: `{SOURCE_BRANCH}` → `{TARGET_BRANCH}`

---

{diff}

---

다음 항목을 중심으로 리뷰해주세요:

1. **버그 및 오류 가능성**: 잠재적인 버그, NPE, 경계값 오류, 예외 처리 누락 등
2. **보안 취약점**: SQL 인젝션, 인증/인가 누락, 민감 데이터 노출, OWASP Top 10 항목
3. **코드 품질**: 가독성, 네이밍 컨벤션, 중복 코드, 불필요한 복잡성
4. **성능**: N+1 쿼리, 불필요한 루프, 메모리 누수 가능성, 비효율적인 자료구조 사용
5. **개선 제안**: 더 나은 구현 방법이 있다면 코드 예시와 함께 구체적으로 제안

각 항목에서 발견된 내용이 없으면 `✅ 문제 없음`으로 표시해주세요.
마지막에 전체 코드 품질 점수를 10점 만점으로 평가해주세요."""


def call_ai_gateway(prompt: str) -> str:
    """AI Gateway SSE 스트림을 호출하고 전체 응답 텍스트를 반환합니다."""
    url = f"{AI_GATEWAY_URL}/api/ai/{AI_SERVICE_NAME}/chat/stream"

    payload = {
        "content": prompt,
        "modelCode": AI_MODEL_CODE,
    }

    headers = {
        "Content-Type": "application/json",
        "x-api-key": AI_GATEWAY_API_KEY,
    }

    collected = []
    with requests.post(url, json=payload, headers=headers, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if not line.startswith("data:"):
                continue
            data_str = line[5:].strip()
            if not data_str:
                continue
            try:
                chunk = json.loads(data_str)
                content = chunk.get("content") or ""
                if content:
                    collected.append(content)
                if chunk.get("isLast"):
                    input_tokens = chunk.get("inputTokens", 0)
                    output_tokens = chunk.get("outputTokens", 0)
                    model = chunk.get("model", "")
                    print(f"[INFO] 토큰 사용량 - model={model}, input={input_tokens}, output={output_tokens}")
                    break
            except json.JSONDecodeError:
                continue

    return "".join(collected)


def post_mr_comment(body: str) -> None:
    """MR에 코드 리뷰 코멘트를 등록합니다."""
    url = f"{GITLAB_API}/projects/{PROJECT_ID}/merge_requests/{MR_IID}/notes"
    comment = (
        f"## AI 코드 리뷰\n\n"
        f"{body}\n\n"
        f"---\n"
        f"*AI Gateway (aiconnect / {AI_SERVICE_NAME}) 자동 리뷰*"
    )
    resp = requests.post(url, headers=GITLAB_HEADERS, json={"body": comment}, timeout=30)
    _handle_gitlab_api_error(resp, "post_mr_comment")
    print(f"[OK] MR 코멘트 등록 완료 (note_id={resp.json().get('id')})")


def main():
    print(f"[INFO] MR !{MR_IID} 코드 리뷰 시작 (service={AI_SERVICE_NAME})")

    diff = fetch_mr_diff()
    if not diff:
        print("[SKIP] 변경된 파일이 없습니다.")
        sys.exit(0)

    print(f"[INFO] diff 크기: {len(diff)} chars")

    prompt = build_prompt(diff)
    print(f"[INFO] AI Gateway 호출 중...")

    review = call_ai_gateway(prompt)
    if not review:
        print("[ERROR] AI Gateway 응답이 비어있습니다.")
        sys.exit(1)

    post_mr_comment(review)
    print("[INFO] 코드 리뷰 완료")


if __name__ == "__main__":
    main()
