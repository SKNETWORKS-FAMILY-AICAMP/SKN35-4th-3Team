
"""LangGraph 없이 Planner → Rewrite → Multi-query → Hybrid → RRF → Rerank 평가.

프로젝트 루트에서 실행:
    python -m tests.rag.eval_rag --limit 1
    python -m tests.rag.eval_rag --replay results/rag_eval/traces.jsonl --output results/replay
"""
import os
import argparse
import csv
import json
import math
import time
from pathlib import Path

from src.rag.rrf import rrf

from functools import lru_cache
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from src.rag.query_planner import query_planner
from src.rag.query_rewrite import query_rewrite
from src.rag.multi_query import multi_query


# RAG 검색 성능을 평가할 때 상위 몇 개의 문서를 기준으로 평가할지 지정하는 K값
KS = (5, 10)


@lru_cache(maxsize=1)
def get_eval_llm():
    load_dotenv()

    return ChatOpenAI(
        model=os.environ["OPENAI_MODEL"],
        temperature=0,
    )


# ===== 프로젝트별 연결 지점 =====
def run_planner(question: str) -> dict:
    """실제 Planner 함수를 호출하고 결과를 반환."""
    return query_planner(
        question=question,
        llm=get_eval_llm()
    )


def run_rewriting(question: str, plan: dict) -> str:
    """Planner 결과를 반영하여 실제 Rewrite 함수를 호출하고 문자열 반환."""

    # rewriting이 존재하지 않을 경우
    if not plan["needs_rewrite"]:
        return question

    result = query_rewrite(
        question=question,
        llm=get_eval_llm(),
        planner_result=plan,
    )

    return result["rewritten_query"]


def run_multi_query(
        question: str,
        rewritten_query: str,
        plan: dict,
    ) -> list[str]:

    """실제 Multi-Query 함수를 호출하고 검색 질문 문자열 리스트 반환."""

    # 멀티쿼리가 없을 경우
    if not plan["needs_expansion"]:
        return []

    result = multi_query(
        question=question,
        llm=get_eval_llm(),
        rewritten_query=rewritten_query,
        planner_result=plan,
        max_queries=3,
    )
    return result["queries"]


# ===== 공통 평가 로직 =====
def document_ids(documents):
    ids, seen = [], set()
    for doc in documents:
        metadata = doc.get('metadata') or doc.get('payload') or {}
        pid = doc.get('point_id') or metadata.get('point_id') or doc.get('id')
        if pid is None:
            raise KeyError('검색 문서에 point_id가 없습니다.')
        pid = str(pid)
        if pid not in seen:
            ids.append(pid)
            seen.add(pid)
    return ids


def metrics(documents, gold):
    ids = document_ids(documents)
    relevant = {pid for pid, grade in gold.items() if grade >= 2}
    if not relevant:
        raise ValueError('정답이 없는 질문은 검색 평가에서 제외해야 합니다.')
    output = {}
    for k in KS:
        ranked = ids[:k]
        hits = sum(pid in relevant for pid in ranked)
        first = next((rank for rank, pid in enumerate(ranked, 1) if pid in relevant), None)
        dcg = sum((2 ** gold.get(pid, 0) - 1) / math.log2(rank + 1)
                  for rank, pid in enumerate(ranked, 1))
        ideal = sorted(gold.values(), reverse=True)[:k]
        idcg = sum((2 ** grade - 1) / math.log2(rank + 1)
                   for rank, grade in enumerate(ideal, 1))
        output.update({
            f'hit@{k}': float(hits > 0),
            f'recall@{k}': hits / len(relevant),
            f'precision@{k}': hits / k,
            f'mrr@{k}': 1 / first if first else 0.0,
            f'ndcg@{k}': dcg / idcg if idcg else 0.0,
        })
    output['unjudged@10'] = sum(pid not in gold for pid in ids[:10]) / len(ids[:10]) if ids[:10] else 0.0
    return output


def evaluate_query(case):
    from src.rag.hybrid_search import hybrid_search
    from src.rag.rerank import rerank
    question = case["query"]
    timings = {}

    # 1. Planner
    start = time.perf_counter()
    plan = run_planner(question)
    timings["planner"] = time.perf_counter() - start

    # 2. 조건부 Query Rewriting
    start = time.perf_counter()
    rewritten = run_rewriting(question, plan)
    timings["rewrite"] = time.perf_counter() - start

    if not isinstance(rewritten, str) or not rewritten.strip():
        raise ValueError("Rewrite 결과가 비어 있습니다.")

    rewritten = rewritten.strip()

    # 3. 조건부 Multi-Query
    start = time.perf_counter()
    expanded = run_multi_query(question, rewritten, plan)
    timings["multi_query"] = time.perf_counter() - start

    if not isinstance(expanded, list):
        raise TypeError("Multi-Query 결과는 리스트여야 합니다.")

    if any(not isinstance(q, str) or not q.strip() for q in expanded):
        raise ValueError("Multi-Query에 유효하지 않은 질문이 있습니다.")

    # 4. 검색할 질문 통합
    # 원본 질문 + 재작성 질문 + Multi-Query 질문
    # 중복 질문은 제거하고 순서는 유지
    queries = list(dict.fromkeys([
        q.strip()
        for q in [question, rewritten, *expanded]
    ]))

    # 5. 각 질문별 Hybrid Search
    start = time.perf_counter()
    rankings = {}

    for index, query in enumerate(queries, start=1):
        found = hybrid_search(
            query=query,
            top_k=40,
        )

        rankings[f"q{index}_dense"] = found["dense_results"]
        rankings[f"q{index}_bm25"] = found["bm25_results"]

    timings["hybrid_search"] = time.perf_counter() - start

    # 6. 모든 검색 결과 RRF 통합
    start = time.perf_counter()

    rrf_docs = rrf({
        "rankings": rankings
    })["documents"]

    timings["rrf"] = time.perf_counter() - start

    # 7. Cross-Encoder Rerank
    # 원본 질문을 기준으로 관련성 재평가
    start = time.perf_counter()

    rerank_docs = rerank(
        query=question,
        documents=rrf_docs,
        top_k=10,
        batch_size=8,
    )

    timings["rerank"] = time.perf_counter() - start

    gold_ids = {str(c["point_id"]) for c in case["relevant_chunks"] if c['relevance'] >= 2}
    judged_grades = {str(c['point_id']): c['relevance'] for c in case['relevant_chunks']}

    print("Planner:", plan)
    print("실제 검색 질의:", queries)

    for stage, docs in [("RRF", rrf_docs), ("Rerank", rerank_docs)]:
        print(f"\n[{stage}]")
        for rank, doc in enumerate(docs, 1):
            meta = doc.get("metadata") or doc.get("payload") or {}
            pid = str(doc["point_id"])
            print(
                rank,
                "정답" if pid in gold_ids else f"비정답 판정({judged_grades[pid]})" if pid in judged_grades else "미등재",
                pid,
                meta.get("source_name"),
                meta.get("locator"),
            )

    # 8. Ground Truth 정답 문서 구성
    gold = {
        str(chunk["point_id"]): int(chunk["relevance"])
        for chunk in case["relevant_chunks"]
    }

    # 9. RRF / Rerank 검색 성능 평가
    rrf_metrics = metrics(rrf_docs, gold)
    rerank_metrics = metrics(rerank_docs, gold)

    # 10. 평가 결과 반환
    return {
        "rrf": rrf_metrics,
        "rerank": rerank_metrics,

        "query_count": len(queries),
        "rrf_count": len(rrf_docs),
        "rerank_count": len(rerank_docs),

        # Planner 판단 결과
        "plan": plan,

        # 실제 검색 질문
        "original_query": question,
        "rewritten_query": rewritten,
        "expanded_queries": expanded,
        "queries": queries,

        # 단계별 실행 시간
        "timings": timings,

        # 검색된 문서 ID
        "rrf_ids": document_ids(rrf_docs),
        "rerank_ids": document_ids(rerank_docs),
        "rankings": rankings,
        "rrf_documents": rrf_docs,
        "rerank_documents": rerank_docs,
    }


def save_csv(path, rows):
    if not rows:
        if path.exists():
            path.unlink()
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    keys = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)



if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", default="tests/rag/result")
    parser.add_argument(
        "--ground-truth",
        default=str(Path(__file__).resolve().parent / "ground_truth.json"),
    )
    parser.add_argument("--replay", default=None)
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Ground Truth 읽기
    with open(args.ground_truth, encoding="utf-8") as f:
        data = json.load(f)

    cases = [
        case for case in data["test_cases"]
        if case.get("answerable", True)
        and case["query_id"] != "Q011"
        and any(c["relevance"] >= 2 for c in case["relevant_chunks"])
    ]

    if args.limit is not None:
        cases = cases[:args.limit]

    # ★ 추가: replay면 저장된 검색 결과를 사용
    replay_data = None
    if args.replay:
        replay_data = {}
        with open(args.replay, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    item = json.loads(line)
                    replay_data[item["query_id"]] = item

    per_query_rows = []
    failure_rows = []
    trace_rows = []

    for index, case in enumerate(cases, 1):
        query_id = case["query_id"]
        print(f"\n[{index}/{len(cases)}] {query_id}")

        try:
            start = time.perf_counter()

            gold = {
                str(c["point_id"]): int(c["relevance"])
                for c in case["relevant_chunks"]
            }

            if replay_data is None:
                result = evaluate_query(case)
                rrf_result = result["rrf"]
                rerank_result = result["rerank"]
                rrf_ids = result["rrf_ids"]
                rerank_ids = result["rerank_ids"]
                timings = result["timings"]
                query_count = result["query_count"]
            else:
                saved = replay_data[query_id]
                rrf_ids = saved["rrf_ids"]
                rerank_ids = saved["rerank_ids"]
                rrf_result = metrics(
                    [{"point_id": pid} for pid in rrf_ids], gold
                )
                rerank_result = metrics(
                    [{"point_id": pid} for pid in rerank_ids], gold
                )
                timings = saved.get("timings", {})
                query_count = saved.get("query_count", 0)

            elapsed = time.perf_counter() - start

            row = {
                "query_id": query_id,
                "category": case.get("category", ""),
                "query": case["query"],
                "query_count": query_count,
                "rrf_count": len(rrf_ids),
                "rerank_count": len(rerank_ids),
                "elapsed_seconds": round(elapsed, 3),
            }

            for key, value in rrf_result.items():
                row[f"rrf_{key}"] = value

            for key, value in rerank_result.items():
                row[f"rerank_{key}"] = value

            for key, value in timings.items():
                row[f"time_{key}"] = round(value, 3)

            per_query_rows.append(row)

            # replay용 최소 검색 기록
            trace_rows.append({
                "query_id": query_id,
                "query_count": query_count,
                "rrf_ids": rrf_ids,
                "rerank_ids": rerank_ids,
                "timings": timings,
            })

            for stage, result in [("RRF", rrf_result), ("RERANK", rerank_result)]:
                print(f"\n  [{stage}]")

                for k in KS:
                    print(
                        f"    K={k} | "
                        f"Hit={result[f'hit@{k}']:.4f} | "
                        f"Recall={result[f'recall@{k}']:.4f} | "
                        f"Precision={result[f'precision@{k}']:.4f} | "
                        f"MRR={result[f'mrr@{k}']:.4f} | "
                        f"nDCG={result[f'ndcg@{k}']:.4f}"
                    )

        except Exception as e:
            print(f"[ERROR] {query_id}: {e}")
            failure_rows.append({
                "query_id": query_id,
                "error": repr(e),
            })

    # 질문별 평가 결과
    save_csv(output_dir / "per_query.csv", per_query_rows)

    # 전체 평균 평가 결과
    summary_rows = []
    if per_query_rows:
        metric_keys = [
            key for key in per_query_rows[0]
            if key.startswith(("rrf_", "rerank_"))
            and key not in ("rrf_count", "rerank_count")
        ]

        summary = {
            "total_cases": len(cases),
            "success_count": len(per_query_rows),
            "failure_count": len(failure_rows),
        }

        for key in metric_keys:
            summary[key] = sum(
                row[key] for row in per_query_rows
            ) / len(per_query_rows)

        summary_rows.append(summary)

    save_csv(output_dir / "summary.csv", summary_rows)

    # 검색 결과 기록 저장
    with (output_dir / "traces.jsonl").open(
        "w", encoding="utf-8"
    ) as f:
        for item in trace_rows:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print("\n========== 평가 완료 ==========")
    print(f"전체 질문: {len(cases)}")
    print(f"성공: {len(per_query_rows)}")
    print(f"실패: {len(failure_rows)}")
    print(f"저장 위치: {output_dir.resolve()}")