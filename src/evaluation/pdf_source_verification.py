"""추가 PDF 표본과 XML에서 빠진 환불 계산식을 대조한다. data/는 변경하지 않는다.

실행: python -m src.evaluation.pdf_source_verification
공식 PDF는 별도 확보한 검증용 파일을 사용하며 실행 중 다운로드하지 않는다.
"""

from collections import Counter
import hashlib
import json

from src.scripts.data_io import ROOT, load_processed_data, read_json
from src.scripts.pdf_tables import REFUND_PDF, fraction_pairs, verify_refund_formulas


REVIEWED_PDF_SAMPLES = [
    {"filename": "minimum_wage_2026_attachment_body_b87d3570ff339e04.pdf",
     "classification": "valid_short_table",
     "observation": "시급 표와 월 환산액·근로시간 조건이 각각 보존됨"},
    {"filename": "[pdf]해고근로자.pdf",
     "classification": "title_or_revision_artifact",
     "observation": "표로 인식된 부분은 제목 상자이며 Q&A 본문은 별도로 보존됨"},
    {"filename": "003058_270551_20251023_appendix_000500E.pdf",
     "classification": "title_or_revision_artifact",
     "observation": "개정 표시를 표로 인식했으며 실제 내용은 문단 목록임"},
    {"filename": "003572_286997_20260701_appendix_000200E.pdf",
     "classification": "layout_review_required",
     "observation": "표에는 제목만 추출되고 실제 처분 행은 본문으로 평탄화됨"},
    {"filename": "004950_287183_20260701_appendix_000100E.pdf",
     "classification": "title_or_revision_artifact",
     "observation": "개정 표시와 실제 관할구역 표가 별도 추출됨; 병합 셀 주의"},
    {"filename": "004950_287183_20260701_appendix_000200E.pdf",
     "classification": "title_or_revision_artifact",
     "observation": "신설 표시와 실제 수수료 표가 별도 추출됨"},
    {"filename": "005498_287001_20260701_appendix_000400E.pdf",
     "classification": "layout_review_required",
     "observation": "표에는 제목만 추출됨; 본문의 수치에는 단위와 공통기준 결합이 필요함"},
    {"filename": "009325_272631_20250712_appendix_000000E.pdf",
     "classification": "title_or_revision_artifact",
     "observation": "개정 표시와 실제 기관·재산 표가 별도 추출됨"},
    {"filename": "009325_272631_20250712_appendix_000000E.pdf",
     "classification": "valid_short_table",
     "observation": "짧지만 정상적인 마지막 행이며 이전 페이지의 열 제목을 함께 봐야 함"},
    {"filename": "009338_288143_20260721_appendix_000100E.pdf",
     "classification": "layout_review_required",
     "observation": "첫 페이지 표는 제목만 추출되고 실제 행은 본문으로 평탄화됨"},
]


def load_pdf_review_candidates():
    """이전 표 점검 보고서에서 검토 대상만 같은 순서로 읽는다."""
    report = read_json(ROOT / "docs/data_reports/table_chunking_experiment.json")
    candidates = report["table_audit"]["short_table_candidates"]
    return [candidate for candidate in candidates if "F.pdf" not in candidate["source_path"]
            and not (candidate["source_path"].endswith("009338_288143_20260721_appendix_000100E.pdf")
                     and candidate["page"] == 2)]


def build_pdf_sample_reviews(candidates, units):
    """파일별 검토 의견을 실제 후보와 연결한다. 기존 대상 검증을 유지한다."""
    if len(candidates) != len(REVIEWED_PDF_SAMPLES) or any(
        not candidate["source_path"].endswith(review["filename"])
        for candidate, review in zip(candidates, REVIEWED_PDF_SAMPLES)
    ):
        raise ValueError("PDF candidate inventory differs from the visually reviewed set")
    samples = []
    for candidate, review in zip(candidates, REVIEWED_PDF_SAMPLES):
        related = [unit for unit in units if unit["source_path"] == candidate["source_path"]
                   and unit.get("page_number") == candidate["page"]]
        samples.append({**candidate, "classification": review["classification"],
                        "observation": review["observation"], "visually_reviewed": True,
                        "eligible_unit_ids": [unit["unit_id"] for unit in related]})
    return samples


def collect_pdf_source_text(units, filename):
    return "\n".join(unit["text"] for unit in units if unit["source_path"].endswith(filename))


def check_pdf_sample_content(units):
    """검토한 수치와 예외 조건이 전처리 결과에 남아 있는지 확인한다."""
    wage = collect_pdf_source_text(units, REVIEWED_PDF_SAMPLES[0]["filename"])
    fees = collect_pdf_source_text(units, REVIEWED_PDF_SAMPLES[5]["filename"])
    checks = {"wage_and_monthly_conditions": all(value in wage for value in ("10,320원", "2,156,880원", "209시간", "40시간")),
              "five_fee_rows": all(value in fees for value in ("1억원 미만 | 10,000원", "1억원 이상 3억원 미만 | 20,000원",
                                   "3억원 이상 5억원 미만 | 30,000원", "5억원 이상 10억원 미만 | 50,000원", "10억원 이상 | 100,000원")),
              "fee_exception_preserved": "수수료는 1만원" in fees,
              "last_page_row_preserved": "한국교통안전공단" in collect_pdf_source_text(units, REVIEWED_PDF_SAMPLES[8]["filename"])}
    return checks


def verify_additional_pdf_samples(units):
    """후보 읽기, 검토 의견 연결, 본문 검증을 순서대로 실행한다."""
    candidates = load_pdf_review_candidates()
    samples = build_pdf_sample_reviews(candidates, units)
    checks = check_pdf_sample_content(units)
    return {"sample_pages": len(samples),
            "classification_counts": dict(Counter(sample["classification"] for sample in samples)),
            "samples": samples, "content_checks": checks,
            "limitations": "Reviewed candidate pages only, not all PDFs. Layout-review cases are not missing-text claims. No production filtering or table reconstruction applied."}



def main():
    paths = sorted(p for p in (ROOT / "data").rglob("*") if p.is_file())
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    units = load_processed_data()
    report = {"refund_verification": verify_refund_formulas(units),
              "additional_pdf_verification": verify_additional_pdf_samples(units),
              "production_changes_applied": any(u.get("evidence_metadata", {}).get("formula_supplement") for u in units)}
    assert set(paths) == {p for p in (ROOT / "data").rglob("*") if p.is_file()}
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == value for p, value in hashes.items())
    report["data_files_unchanged"] = True
    output = ROOT / "docs/data_reports/pdf_source_verification.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified_formulas": len(report["refund_verification"]["verified_formulas"]),
                      "additional_pdf_pages": report["additional_pdf_verification"]["sample_pages"],
                      "pdf_classifications": report["additional_pdf_verification"]["classification_counts"],
                      "data_files_unchanged": True}, indent=2))


if __name__ == "__main__":
    main()
