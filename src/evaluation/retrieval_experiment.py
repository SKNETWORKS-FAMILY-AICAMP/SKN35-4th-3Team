"""기존 검색과 후보 확대·문자 유사도 결합·어휘 재순위화를 비교한다.

실행: python -m src.evaluation.retrieval_experiment
외부 API나 임베딩 모델을 사용하지 않고 데이터와 기본 검색 설정을 유지한다.
"""

from collections import Counter, defaultdict
import hashlib
import json
import math
import re

from src.scripts.data_io import ROOT, load_chunks
from .retrieval_check import (
    AnchoredRerankIndex, CoverageRerankIndex, ExpandedCandidateIndex,
    RRF_OFFSET, EnhancedLexicalIndex, LexicalIndex,
    evaluate, normalize_query,
)


def character_grams(text):
    text = "".join(re.findall(r"[가-힣a-z0-9]+", text.lower()))
    if not text:
        return Counter()
    width = min(3, len(text))
    return Counter(text[i:i + width] for i in range(len(text) - width + 1))


class CharacterIndex:
    """문자 3개 묶음의 TF-IDF 코사인 유사도. 의미 임베딩 검색은 아니다."""

    def __init__(self, rows):
        counts = [character_grams(row.get("embedding_text", row["text"])) for row in rows]
        frequencies = Counter(gram for count in counts for gram in count)
        self.idf = {gram: math.log((1 + len(rows)) / (1 + count)) + 1
                    for gram, count in frequencies.items()}
        self.postings = defaultdict(list)
        self.rows = rows
        for index, count in enumerate(counts):
            weights = {gram: (1 + math.log(n)) * self.idf[gram] for gram, n in count.items()}
            norm = math.sqrt(sum(w * w for w in weights.values()))
            for gram, weight in weights.items():
                self.postings[gram].append((index, weight / norm))

    def search(self, query, k=300):
        weights = {gram: (1 + math.log(n)) * self.idf[gram]
                   for gram, n in character_grams(query).items() if gram in self.idf}
        norm = math.sqrt(sum(w * w for w in weights.values()))
        if not norm or k <= 0:
            return []
        scores = defaultdict(float)
        for gram, weight in weights.items():
            for index, value in self.postings[gram]:
                scores[index] += weight / norm * value
        return [self.rows[i] for i in sorted(scores, key=lambda i: (-scores[i], self.rows[i]["chunk_id"]))[:k]]


class CharacterFusionIndex(ExpandedCandidateIndex):
    def __init__(self, rows):
        super().__init__(rows)
        self.character_index = CharacterIndex(rows)

    def candidate_ranking(self, query):
        normalized = normalize_query(query)
        return self.fuse([LexicalIndex.search(self, query, k=self.candidate_count),
                          LexicalIndex.search(self, normalized, k=self.candidate_count),
                          self.character_index.search(normalized, k=self.candidate_count)])


def main():
    paths = sorted(path for path in (ROOT / "data").rglob("*") if path.is_file())
    hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    rows = list(load_chunks())
    results = {}
    for name, index in (("current", EnhancedLexicalIndex),
                        ("candidates_300", ExpandedCandidateIndex),
                        ("character_fusion", CharacterFusionIndex),
                        ("coverage_rerank", CoverageRerankIndex),
                        ("keep_first_rerank", AnchoredRerankIndex)):
        results[name] = evaluate(rows, mode="enhanced", index_class=index)
        print(name, results[name]["hit_at_5"], results[name]["mrr_at_5"], flush=True)
    changes = {}
    current = results["current"]["cases"]
    for name, result in results.items():
        if name == "current":
            continue
        changes[name] = [{"query": old["query"], "evaluation_status": old["evaluation_status"],
                          "before": old["rank_at_5"], "after": new["rank_at_5"]}
                         for old, new in zip(current, result["cases"])
                         if old["rank_at_5"] != new["rank_at_5"]]
    assert set(paths) == {p for p in (ROOT / "data").rglob("*") if p.is_file()}
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in hashes.items())
    report = {"applied_to_default_search": True, "promoted_method": "keep_first_rerank",
              "experiment_changes_settings": False, "data_files_unchanged": True,
              "settings": {"expanded_candidates_per_channel": 300, "rerank_candidates": 100,
                           "rrf_offset": RRF_OFFSET, "rerank_weights": {"rank": .5, "coverage": .5}},
              "results": results, "rank_changes": changes,
              "experiment_notes": "Keeping the current first result was added after observing first-place regressions in coverage reranking. It uses no expected evidence, source IDs, or query-specific rules. It is still development-set iteration.",
              "limitations": "Fixed experiments on existing development questions, not held out. All methods are lexical; no semantic embeddings or learned reranker. Expected evidence and raw data unchanged."}
    path = ROOT / "docs/data_reports/retrieval_experiment.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
