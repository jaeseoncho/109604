#!/usr/bin/env python3
"""Daily official-source change monitor for Winner Aircon.

The monitor stores only short, keyword-matched excerpts. It establishes a
baseline on the first successful run and writes an issue-ready Markdown report
only when a relevant excerpt changes or a source repeatedly becomes unreadable.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import html
import json
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 2
MAX_RESPONSE_BYTES = 7_000_000
MAX_FRAGMENT_LENGTH = 720
MAX_REPORT_ITEMS_PER_SIDE = 10
SEOUL = dt.timezone(dt.timedelta(hours=9))


@dataclasses.dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    area: str
    why: str
    actions: tuple[str, ...]
    keywords: tuple[str, ...]
    always_alert: bool = False


class VisibleTextParser(HTMLParser):
    """Extract visible text and likely same-site policy-document links."""

    SKIP_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
    BLOCK_TAGS = {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl",
        "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
        "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol",
        "p", "pre", "section", "table", "tbody", "td", "tfoot", "th", "thead",
        "tr", "ul",
    }
    FOLLOW_MARKERS = (
        "lsinfop.do",
        "admrulinfop.do",
        "admrullsinfop.do",
        "lawservice",
        "conadmrulbylspop.do",
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []
        self.follow_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {key.lower(): value or "" for key, value in attrs}

        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return

        if self._skip_depth == 0:
            if tag in self.BLOCK_TAGS:
                self._parts.append("\n")
            target = attrs_dict.get("href") or attrs_dict.get("src")
            if target:
                descriptor = " ".join(
                    [
                        target,
                        attrs_dict.get("id", ""),
                        attrs_dict.get("name", ""),
                        attrs_dict.get("class", ""),
                        attrs_dict.get("title", ""),
                        attrs_dict.get("aria-label", ""),
                    ]
                ).casefold()
                if any(marker in descriptor for marker in self.FOLLOW_MARKERS):
                    self.follow_links.append(target)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth == 0 and tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def load_config(path: Path) -> tuple[list[Source], tuple[str, ...], tuple[str, ...]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    sources: list[Source] = []
    seen_ids: set[str] = set()
    for item in raw.get("sources", []):
        source_id = str(item["id"]).strip()
        if not source_id or source_id in seen_ids:
            raise ValueError(f"duplicate or blank source id: {source_id!r}")
        seen_ids.add(source_id)
        source = Source(
            id=source_id,
            name=str(item["name"]).strip(),
            url=str(item["url"]).strip(),
            area=str(item["area"]).strip(),
            why=str(item["why"]).strip(),
            actions=tuple(str(value).strip() for value in item.get("actions", []) if str(value).strip()),
            keywords=tuple(str(value).strip() for value in item.get("keywords", []) if str(value).strip()),
            always_alert=bool(item.get("always_alert", False)),
        )
        if not source.url.startswith(("https://", "http://")):
            raise ValueError(f"invalid URL for {source.id}: {source.url}")
        if not source.keywords:
            raise ValueError(f"source has no keywords: {source.id}")
        sources.append(source)

    if not sources:
        raise ValueError("sources.json contains no sources")

    material_terms = tuple(str(value).strip() for value in raw.get("material_terms", []) if str(value).strip())
    urgent_terms = tuple(str(value).strip() for value in raw.get("urgent_terms", []) if str(value).strip())
    if not material_terms:
        raise ValueError("sources.json contains no material_terms")
    return sources, material_terms, urgent_terms


def encode_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(urllib.parse.unquote(parts.path), safe="/%:@")
    query = urllib.parse.quote(urllib.parse.unquote(parts.query), safe="=&%:+,;/?@[]")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def decode_body(body: bytes, content_type: str) -> str:
    match = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type or "", re.I)
    candidates = [match.group(1)] if match else []
    candidates.extend(["utf-8", "cp949", "euc-kr"])
    tried: set[str] = set()
    for encoding in candidates:
        if not encoding or encoding.casefold() in tried:
            continue
        tried.add(encoding.casefold())
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def fetch_html(url: str, attempts: int = 3, timeout: int = 45) -> tuple[str, str]:
    encoded = encode_url(url)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "Chrome/126.0 Safari/537.36 WinnerAirconPolicyMonitor/2.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.6,en;q=0.5",
        "Cache-Control": "no-cache",
    }
    context = ssl.create_default_context()
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(encoded, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                status = getattr(response, "status", 200)
                if status >= 400:
                    raise RuntimeError(f"HTTP {status}")
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise RuntimeError(f"response exceeded {MAX_RESPONSE_BYTES} bytes")
                document = decode_body(body, response.headers.get("Content-Type", ""))
                return response.geturl(), document
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if attempt < attempts:
                time.sleep(attempt * 2)

    raise RuntimeError(f"fetch failed after {attempts} attempts: {last_error}")


def parse_document(document: str) -> tuple[str, list[str]]:
    parser = VisibleTextParser()
    parser.feed(document)
    parser.close()
    text = html.unescape(parser.text())
    return text, parser.follow_links


def same_site(base_url: str, candidate_url: str) -> bool:
    base_host = (urllib.parse.urlsplit(base_url).hostname or "").casefold().removeprefix("www.")
    candidate_host = (urllib.parse.urlsplit(candidate_url).hostname or "").casefold().removeprefix("www.")
    return candidate_host == base_host or candidate_host.endswith("." + base_host)


def fetch_visible_text(url: str) -> tuple[str, str]:
    """Fetch a page and, for law.go.kr wrappers, follow the actual policy view."""
    final_url, document = fetch_html(url)
    text, follow_links = parse_document(document)
    pieces = [text]
    content_url = final_url

    if (urllib.parse.urlsplit(final_url).hostname or "").endswith("law.go.kr"):
        for raw_target in follow_links[:6]:
            target = urllib.parse.urljoin(final_url, raw_target)
            if not same_site(final_url, target):
                continue
            try:
                followed_url, followed_document = fetch_html(target, attempts=2)
                followed_text, _ = parse_document(followed_document)
            except Exception:
                continue
            if len(followed_text.strip()) >= 120:
                pieces.append(followed_text)
                content_url = followed_url
                break

    return "\n".join(pieces), content_url


def normalize_text(document_text: str) -> str:
    text = unicodedata.normalize("NFKC", document_text)
    text = text.replace("\u200b", "").replace("\ufeff", "").replace("\xa0", " ")
    text = re.sub(r"조회수\s*[:：]?\s*[\d,]+", "조회수", text)
    text = re.sub(r"페이지\s*\d+\s*/\s*\d+", "페이지", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_fragment(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = value.replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"\s+", " ", value).strip(" \t\n\r-|•·")
    return value


def keyword_windows(line: str, keywords: Iterable[str]) -> list[str]:
    lowered = line.casefold()
    positions: list[int] = []
    for keyword in keywords:
        needle = keyword.casefold()
        start = 0
        while needle:
            position = lowered.find(needle, start)
            if position < 0:
                break
            positions.append(position)
            start = position + max(1, len(needle))

    if not positions:
        return []

    ranges: list[tuple[int, int]] = []
    for position in sorted(set(positions)):
        begin = max(0, position - 230)
        end = min(len(line), position + 390)
        if ranges and begin <= ranges[-1][1] + 90:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((begin, end))
    return [line[begin:end] for begin, end in ranges]


def extract_fragments(text: str, keywords: tuple[str, ...]) -> list[str]:
    fragments: list[str] = []
    seen: set[str] = set()
    folded_keywords = tuple(keyword.casefold() for keyword in keywords)

    for raw_line in text.splitlines():
        line = normalize_fragment(raw_line)
        if len(line) < 5:
            continue
        lowered = line.casefold()
        if not any(keyword in lowered for keyword in folded_keywords):
            continue

        candidates = [line] if len(line) <= MAX_FRAGMENT_LENGTH else keyword_windows(line, keywords)
        for candidate in candidates:
            candidate = normalize_fragment(candidate)
            if len(candidate) < 5:
                continue
            if len(candidate) > MAX_FRAGMENT_LENGTH:
                candidate = candidate[: MAX_FRAGMENT_LENGTH - 1].rstrip() + "…"
            key = candidate.casefold()
            if key not in seen:
                seen.add(key)
                fragments.append(candidate)

    return sorted(fragments, key=lambda item: item.casefold())


def sha256_lines(lines: Iterable[str]) -> str:
    payload = "\n".join(lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "sources": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read state file: {exc}") from exc

    if state.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(
            "unsupported state schema; delete state/policy_state.json and run once to re-baseline"
        )
    if not isinstance(state.get("sources"), dict):
        raise RuntimeError("invalid state file: sources must be an object")
    return state


def matched_terms(changed: Iterable[str], terms: tuple[str, ...]) -> set[str]:
    combined = "\n".join(changed).casefold()
    return {term for term in terms if term.casefold() in combined}


def clip_list(items: list[str], limit: int = MAX_REPORT_ITEMS_PER_SIDE) -> list[str]:
    return items[:limit]


def markdown_quote(item: str) -> str:
    item = item.replace("\n", " ").strip().replace("`", "′")
    if len(item) > 520:
        item = item[:519].rstrip() + "…"
    return item


def build_report(
    now: dt.datetime,
    changes: list[dict[str, Any]],
    source_errors: list[dict[str, str]],
    severity: str,
) -> str:
    lines: list[str] = [
        "# 위너에어컨 정책 변경 감지",
        "",
        f"- **감지 시각:** {now.strftime('%Y-%m-%d %H:%M')} KST",
        f"- **판정:** {severity}",
        "- **확인 원칙:** 자동 비교 결과이므로 시행일·적용대상·예외·경과조치는 공식 원문에서 최종 확인합니다.",
        "",
    ]

    if changes:
        lines.extend(["## 1. 무엇이 바뀌었나", ""])
        for index, change in enumerate(changes, start=1):
            source: Source = change["source"]
            lines.extend(
                [
                    f"### {index}) {source.name}",
                    f"- **영역:** {source.area}",
                    f"- **공식 원문:** {change.get('content_url') or source.url}",
                    f"- **감지 키워드:** {', '.join(sorted(change['matched_terms'])) or '해당 공식 페이지의 관련 문구 변경'}",
                ]
            )
            added = clip_list(change["added"])
            removed = clip_list(change["removed"])
            if added:
                lines.append("- **추가·변경된 문구:**")
                lines.extend(f"  - `{markdown_quote(item)}`" for item in added)
            if removed:
                lines.append("- **삭제·이전 문구:**")
                lines.extend(f"  - `{markdown_quote(item)}`" for item in removed)
            if len(change["added"]) > len(added) or len(change["removed"]) > len(removed):
                lines.append("- **표시 제한:** 변경 문구가 많아 각 방향 최대 10개만 표시했습니다.")

            lines.extend(["", "**왜 중요한가**", "", source.why, "", "**확인할 조치**", ""])
            lines.extend(f"- [ ] {action}" for action in source.actions)
            lines.append("")

        lines.extend(
            [
                "## 2. 위너에어컨 공통 점검",
                "",
                "- [ ] 판매 중인 에어컨·냉난방기 상품의 카테고리, 모델명, 옵션, KC·효율 정보를 확인한다.",
                "- [ ] 기본설치비, 추가배관, 앵글, 타공, 철거, 지방배송비 등 필수·추가 비용을 결제 전 명확히 고지했는지 확인한다.",
                "- [ ] 사업자명, 대표자, 주소, 연락처, 도메인, 통신판매업 신고번호가 정부24와 스마트스토어에 동일한지 확인한다.",
                "- [ ] 개인→사업자 전환, 양도양수, 계정·매니저 변경 시 기존 상품, 리뷰, 정산, 검색·쇼핑 노출 영향을 확인한다.",
                "- [ ] 판매자센터의 노출중단, 판매중지, 운영제한, 고객확인제도 및 신규 약관 동의 알림을 확인한다.",
                "- [ ] 상세페이지의 냉방면적, 성능, 전기요금, 설치 가능 여부가 객관적 근거와 일치하는지 확인한다.",
                "",
            ]
        )

    if source_errors:
        lines.extend(["## 모니터링 점검 필요", ""])
        for error in source_errors:
            lines.extend(
                [
                    f"- **{error['name']}**",
                    f"  - 원문: {error['url']}",
                    f"  - 오류: `{markdown_quote(error['error'])}`",
                ]
            )
        lines.extend(
            [
                "",
                "해당 출처가 3회 연속 확인되지 않았거나 전체 출처 확인에 실패했습니다. 사이트 구조 변경·접속 제한 여부를 점검해야 합니다.",
                "",
            ]
        )

    lines.extend(
        [
            "---",
            "이 이슈는 공개된 공식 페이지의 관련 문구가 바뀌었을 때 자동 생성됩니다. "
            "스마트스토어센터 로그인 후에만 보이는 개별 팝업·쪽지·이메일은 별도로 확인해야 합니다.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="sources.json")
    parser.add_argument("--state", default="state/policy_state.json")
    parser.add_argument("--report", default="policy_change_report.md")
    parser.add_argument("--result", default="monitor_result.json")
    args = parser.parse_args()

    config_path = Path(args.config)
    state_path = Path(args.state)
    report_path = Path(args.report)
    result_path = Path(args.result)

    sources, material_terms, urgent_terms = load_config(config_path)
    state = read_state(state_path)
    state_sources: dict[str, Any] = state.setdefault("sources", {})

    now = dt.datetime.now(SEOUL)
    state["last_checked_at"] = now.isoformat(timespec="seconds")

    changes: list[dict[str, Any]] = []
    error_alerts: list[dict[str, str]] = []
    run_errors: list[dict[str, str]] = []
    successful = 0
    baselined = 0

    for source in sources:
        previous = state_sources.get(source.id)
        try:
            visible_text, content_url = fetch_visible_text(source.url)
            normalized_text = normalize_text(visible_text)
            fragments = extract_fragments(normalized_text, source.keywords)
            if not fragments:
                raise RuntimeError("no keyword-matched text extracted; page may require login or JavaScript")

            successful += 1
            current_hash = sha256_lines(fragments)

            if previous is None or not previous.get("hash"):
                baselined += 1
            elif previous.get("hash") != current_hash:
                old_fragments = [str(value) for value in previous.get("fragments", [])]
                old_set = set(old_fragments)
                new_set = set(fragments)
                added = sorted(new_set - old_set, key=str.casefold)
                removed = sorted(old_set - new_set, key=str.casefold)
                changed_fragments = added + removed
                material_matches = matched_terms(changed_fragments, material_terms)
                if source.always_alert or material_matches:
                    changes.append(
                        {
                            "source": source,
                            "content_url": content_url,
                            "added": added,
                            "removed": removed,
                            "matched_terms": material_matches,
                            "urgent_terms": matched_terms(changed_fragments, urgent_terms),
                        }
                    )

            state_sources[source.id] = {
                "name": source.name,
                "url": source.url,
                "content_url": content_url,
                "hash": current_hash,
                "fragments": fragments,
                "failure_count": 0,
                "last_success_at": now.isoformat(timespec="seconds"),
            }
        except Exception as exc:
            previous = previous or {"name": source.name, "url": source.url, "failure_count": 0}
            old_count = int(previous.get("failure_count", 0) or 0)
            new_count = min(old_count + 1, 999)
            previous["name"] = source.name
            previous["url"] = source.url
            previous["failure_count"] = new_count
            previous["last_error"] = str(exc)
            previous["last_failure_at"] = now.isoformat(timespec="seconds")
            state_sources[source.id] = previous
            run_errors.append({"name": source.name, "url": source.url, "error": str(exc)})
            if new_count == 3:
                error_alerts.append({"name": source.name, "url": source.url, "error": str(exc)})

    if successful == 0:
        error_alerts = run_errors

    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    urgent = any(change["urgent_terms"] for change in changes)
    if changes:
        severity = "즉시 확인" if urgent else "7일 이내 확인"
        title = f"[정책변경 감지][{severity}] {now:%Y-%m-%d} 공식 출처 {len(changes)}곳"
    elif error_alerts:
        severity = "모니터링 점검 필요"
        title = f"[정책 모니터 점검] {now:%Y-%m-%d} 공식 출처 확인 실패"
    else:
        severity = "변경 없음"
        title = ""

    alert = bool(changes or error_alerts)
    if alert:
        report_path.write_text(build_report(now, changes, error_alerts, severity), encoding="utf-8")
    elif report_path.exists():
        report_path.unlink()

    result = {
        "alert": alert,
        "title": title,
        "severity": severity,
        "successful_sources": successful,
        "total_sources": len(sources),
        "baselined_sources": baselined,
        "changed_sources": len(changes),
        "run_errors": len(run_errors),
        "error_alerts": len(error_alerts),
    }
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"fatal monitor error: {exc}", file=sys.stderr)
        raise SystemExit(2)
