"""Offline lexical smoke comparison; this does not evaluate embedding or answer quality."""

from collections import Counter, defaultdict
import argparse
import json
import math
import re
from pathlib import Path

from .preprocessing import ROOT, old_chunks


CASES = [
    ("헬스장 이용 계약 중도해지 잔여대금 환급", "kca_case_1003817065", None),
    ("필라테스 계약 중도해지 환급", "kca_pilates_1003941650", None),
    ("맞춤제작 한복 반품 거절 청약철회", "kca_case_1003164054", None),
    ("하자 반복 반지 반품 구입대금 환급", "kca_case_1003604873", None),
    ("소비자 피해구제 신청 증빙서류 접수 방법", "kca_redress_submission", None),
    ("임금체불 진정 고소 신고 절차", "moel_wage_procedure", None),
    ("임금체불 근로계약서 급여자료 증빙 준비", "moel_wage_evidence_1000323154", None),
    ("주택임대차 계약 묵시적 갱신", "001248_276291_20260102", "제6조("),
    ("주택임대차 묵시적 갱신 계약 해지 통지 3개월", "001248_276291_20260102", "제6조의2"),
    ("보증금 반환 임차권등기명령 신청", "001248_276291_20260102", "제3조의3"),
    ("근로기준법 해고 예고 30일 통상임금", "001872_285279_20261008", "제26조("),
    ("근로기준법 해고 사유 시기 서면 통지", "001872_285279_20261008", "제27조("),
    ("퇴직 임금 금품 청산 14일", "001872_285279_20261008", "제36조("),
    ("임금 직접 전액 지급 매월 일정한 날짜", "001872_285279_20261008", "제43조("),
]


def tokens(text):
    # Korean character bigrams reduce sensitivity to spacing/particles without a model.
    words = re.findall(r"[가-힣A-Za-z0-9]+", text.lower())
    return [token for word in words for token in ([word] + [word[i:i+2] for i in range(len(word)-1)])]


class LexicalIndex:
    def __init__(self, rows):
        self.rows = rows
        self.postings = defaultdict(list)
        self.lengths = []
        for index, row in enumerate(rows):
            counts = Counter(tokens(row.get("embedding_text", row["text"])))
            self.lengths.append(sum(counts.values()))
            for token, count in counts.items():
                self.postings[token].append((index, count))
        self.average = sum(self.lengths) / max(1, len(rows))

    def search(self, query, k=5):
        scores = defaultdict(float)
        for token in set(tokens(query)):
            posting = self.postings.get(token, [])
            idf = math.log(1 + (len(self.rows) - len(posting) + .5) / (len(posting) + .5))
            for index, frequency in posting:
                denominator = frequency + 1.5 * (.25 + .75 * self.lengths[index] / self.average)
                scores[index] += idf * frequency * 2.5 / denominator
        return [self.rows[i] for i in sorted(scores, key=lambda i: (-scores[i], i))[:k]]


def parent_evidence(hits, parents):
    """Expand retrieval children to complete evidence; never treat a table slice as a rule."""
    result, seen = [], set()
    for hit in hits:
        key = hit["parent_id"]
        if key not in seen:
            result.append(parents[key])
            seen.add(key)
    return result


def evaluate(rows):
    index = LexicalIndex(rows)
    results = []
    for query, document, locator in CASES:
        hits = index.search(query)
        def relevant(row):
            identity = row.get("document_id", "")
            same_document = identity == document or identity.endswith(":" + document) or document in (
                row.get("version_id"), row.get("source_id"), row.get("guide_id"))
            heading = row.get("locator", row.get("context_header", ""))
            return same_document and (locator is None or locator in heading)
        rank = next((i + 1 for i, row in enumerate(hits) if relevant(row)), None)
        results.append({"query": query, "expected_document": document, "expected_locator": locator,
                        "rank_at_5": rank, "top5": [{"id": r["chunk_id"],
                            "locator": r.get("locator", r.get("context_header")),
                            "source": r.get("source_path")} for r in hits]})
    return {"hit_at_5": sum(r["rank_at_5"] is not None for r in results) / len(results),
            "mrr_at_5": sum(1 / r["rank_at_5"] if r["rank_at_5"] else 0 for r in results) / len(results),
            "cases": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--baseline-root", type=Path, help="Snapshot root containing data/chunk")
    args = parser.parse_args()
    output = args.data_dir.resolve()
    if output != (ROOT / "data").resolve() and not output.is_relative_to((ROOT / "data/rebuilt").resolve()):
        raise ValueError("Data directory must be data or inside data/rebuilt")
    new = list(old_chunks(output.parent)) if output.name == "data" else [
        json.loads(line) for path in sorted((output / "chunk").rglob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    snapshots = sorted(p for p in (ROOT / ".data_backups").glob("*") if (p / "data/chunk").is_dir())
    baseline = args.baseline_root or (snapshots[-1] if snapshots else None)
    report = {"method": "Offline Korean bigram BM25 scorer; 14 hand-authored smoke queries",
              "limitations": "Small, development-only set; not held out. No embedding, reranker, or answer evaluation.",
              "new": evaluate(new)}
    if baseline:
        report["baseline_root"] = str(baseline)
        report["old"] = evaluate(list(old_chunks(baseline)))
    report_path = (ROOT / "docs/data_reports" if output == ROOT / "data" else output / "reports") / "retrieval_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: {m: v for m, v in report[k].items() if m != "cases"} for k in ("old", "new") if k in report}, indent=2))


if __name__ == "__main__":
    main()
