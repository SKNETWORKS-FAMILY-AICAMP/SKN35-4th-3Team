"""Offline lexical smoke comparison; this does not evaluate embedding or answer quality."""

from collections import Counter, defaultdict
import argparse
import json
import math
import re
from pathlib import Path

from .preprocessing import ROOT, old_chunks


DEFAULT_CASES_PATH = ROOT / "tests/data/retrieval_cases.json"


def load_cases(path=DEFAULT_CASES_PATH):
    """Read non-empty query cases with an expected document and optional locator."""
    cases = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("Test cases must be a non-empty JSON array")
    for number, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            raise ValueError(f"Case {number} must be a JSON object")
        status = case.get("evaluation_status", "scored")
        if status not in ("scored", "needs_evidence_review"):
            raise ValueError(f"Case {number}: unknown evaluation_status")
        for field in (("query", "expected_document") if status == "scored" else ("query",)):
            value = case.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Case {number}: {field} must be a non-empty string")
        locator = case.get("expected_locator")
        if locator is not None and (not isinstance(locator, str) or not locator.strip()):
            raise ValueError(f"Case {number}: expected_locator must be null or a non-empty string")
    return cases


def tokens(text):
    # Korean character bigrams reduce sensitivity to spacing/particles without a model.
    words = re.findall(r"[가-힣A-Za-z0-9]+", text.lower())
    return [token for word in words for token in ([word] + [word[i:i+2] for i in range(len(word)-1)])]


def normalize_query(query):
    """Translate common expressions into corpus vocabulary, without choosing evidence IDs."""
    replacements = (
        (r"알바비|월급|급여|봉급", "임금"),
        (r"아르바이트|알바", "근로자"),
        (r"사장님|고용주", "사용자"),
        (r"집주인", "임대인"), (r"세입자", "임차인"),
        (r"전셋집", "전세 주택"),
        (r"그만둔|그만뒀|퇴사", "퇴직"),
        (r"나오지\s*말\w*|잘렸\w*", "해고 예고"),
        (r"미리\s*알려\w*", "예고"),
        (r"자동(?:으로)?\s*(?:연장|갱신)", "묵시적 갱신"),
        (r"주인이\s*바뀌\w*", "임대인 변경 승계"),
        (r"팔려\w*|팔렸\w*", "매매 양도"),
        (r"증거", "증빙자료"),
        (r"배송비|반품비", "반환 비용"),
        (r"돌려받\w*|돌려줘\w*", "반환 환급"),
        (r"절반만|반만", "일부 지급"),
    )
    text = query
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    # These conversational endings should not outrank the substantive terms.
    text = re.sub(r"어떻게|언제까지|하나요|건가요|아닌가요|있나요|되나요|해야|제가|저도|그런데", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def diversify_hits(hits, k=5, max_per_document=2):
    """Keep one hit per evidence unit and cap a document's share of the results."""
    if k < 1:
        return []
    if max_per_document < 1:
        raise ValueError("max_per_document must be positive")
    selected, parents, documents = [], set(), Counter()
    for hit in hits:
        parent = hit.get("parent_id") or hit["chunk_id"]
        document = hit.get("document_id") or hit.get("source_path") or hit["chunk_id"]
        if parent in parents or documents[document] >= max_per_document:
            continue
        selected.append(hit)
        parents.add(parent)
        documents[document] += 1
        if len(selected) == k:
            break
    return selected


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


class EnhancedLexicalIndex(LexicalIndex):
    def search(self, query, k=5):
        # Fuse the original and normalized query rankings; preserve exact legal terms.
        original = super().search(query, k=100)
        normalized = super().search(normalize_query(query), k=100)
        scores, hits = defaultdict(float), {}
        for weight, ranking in ((1.0, original), (1.0, normalized)):
            for rank, hit in enumerate(ranking, 1):
                key = hit["chunk_id"]
                hits[key] = hit
                scores[key] += weight / (60 + rank)
        ranking = [hits[key] for key in sorted(scores, key=lambda key: (-scores[key], key))]
        return diversify_hits(ranking, k=k)


def parent_evidence(hits, parents):
    """Expand retrieval children to complete evidence; never treat a table slice as a rule."""
    result, seen = [], set()
    for hit in hits:
        key = hit["parent_id"]
        if key not in seen:
            result.append(parents[key])
            seen.add(key)
    return result


def evaluate(rows, cases=None, *, preview_chars=300, mode="baseline"):
    """Evaluate document/locator ranks and expose excerpts for manual review."""
    cases = load_cases() if cases is None else cases
    if not cases:
        raise ValueError("At least one test case is required")
    if type(preview_chars) is not int or preview_chars < 1:
        raise ValueError("preview_chars must be a positive integer")
    if mode not in ("baseline", "enhanced"):
        raise ValueError("Unknown retrieval mode")
    index = (EnhancedLexicalIndex if mode == "enhanced" else LexicalIndex)(rows)
    results = []
    for case in cases:
        query, document = case["query"], case.get("expected_document")
        scored = case.get("evaluation_status", "scored") == "scored"
        locator = case.get("expected_locator")
        hits = index.search(query)
        def relevant(row):
            if not scored:
                return False
            identity = row.get("document_id", "")
            same_document = identity == document or identity.endswith(":" + document) or document in (
                row.get("version_id"), row.get("source_id"), row.get("guide_id"))
            heading = row.get("locator", row.get("context_header", ""))
            return same_document and (locator is None or locator in heading)
        rank = next((i + 1 for i, row in enumerate(hits) if relevant(row)), None)
        results.append({"query": query, "normalized_query": normalize_query(query) if mode == "enhanced" else query,
                        "evaluation_status": "scored" if scored else "needs_evidence_review",
                        **({"evidence_note": case["evidence_note"]} if "evidence_note" in case else {}),
                        "expected_document": document, "expected_locator": locator,
                        "rank_at_5": rank, "top5": [{"id": r["chunk_id"],
                            "parent_id": r.get("parent_id"), "title": r.get("title", r.get("law_name")),
                            "locator": r.get("locator", r.get("context_header")),
                            "source": r.get("source_path"), "text_preview": r["text"][:preview_chars],
                            "text_length": len(r["text"]), "preview_truncated": len(r["text"]) > preview_chars}
                            for r in hits]})
    evaluated = [r for r in results if r["evaluation_status"] == "scored"]
    return {"evaluated_case_count": len(evaluated), "unscored_case_count": len(results) - len(evaluated),
            "hit_at_5": sum(r["rank_at_5"] is not None for r in evaluated) / len(evaluated) if evaluated else None,
            "mrr_at_5": sum(1 / r["rank_at_5"] if r["rank_at_5"] else 0 for r in evaluated) / len(evaluated) if evaluated else None,
            "cases": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--baseline-root", type=Path, help="Snapshot root containing data/chunk")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="JSON file containing test queries and expected evidence")
    parser.add_argument("--mode", choices=("baseline", "enhanced"), default="enhanced",
                        help="Compare the original scorer with normalized-query fusion and diverse results")
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    output = args.data_dir.resolve()
    if output != (ROOT / "data").resolve() and not output.is_relative_to((ROOT / "data/rebuilt").resolve()):
        raise ValueError("Data directory must be data or inside data/rebuilt")
    new = list(old_chunks(output.parent)) if output.name == "data" else [
        json.loads(line) for path in sorted((output / "chunk").rglob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    snapshots = sorted(p for p in (ROOT / ".data_backups").glob("*") if (p / "data/chunk").is_dir())
    baseline = args.baseline_root or (snapshots[-1] if snapshots else None)
    report = {"method": "Offline Korean bigram BM25; enhanced mode uses query normalization, reciprocal rank fusion and diversity",
              "mode": args.mode,
              "cases_file": str(args.cases.resolve()), "case_count": len(cases),
              "limitations": "Small development set used during improvement; not held out. Single expected evidence may miss other valid results. No embedding, learned reranker, or answer evaluation.",
              "baseline": evaluate(new, cases, mode="baseline"),
              "new": evaluate(new, cases, mode=args.mode)}
    if baseline:
        report["baseline_root"] = str(baseline)
        report["old"] = evaluate(list(old_chunks(baseline)), cases, mode=args.mode)
    report_path = (ROOT / "docs/data_reports" if output == ROOT / "data" else output / "reports") / "retrieval_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: {m: v for m, v in report[k].items() if m != "cases"} for k in ("baseline", "old", "new") if k in report}, indent=2))


if __name__ == "__main__":
    main()
