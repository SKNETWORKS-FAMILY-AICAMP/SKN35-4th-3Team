import unittest
import hashlib
import json
from pathlib import Path
import tempfile

from src.scripts.chunk_data import make_chunks, split_spans, validate
from src.evaluation.retrieval_check import parent_evidence
from src.scripts.data_io import load_processed_data
from src.scripts.preprocess_data import build_evidence_unit


class ChunkingTests(unittest.TestCase):
    def unit(self, text):
        return {"unit_id": "refund:table", "title": "환불 기준", "locator": "별표 2",
                "text": text, "atomic": True, "embedding_candidate": True}

    def test_large_refund_table_is_recoverable_with_conditions(self):
        text = "조건 | 환급액 | 예외\n" + "이용 전 해지 | 전액 반환 | 별도 약정 확인\n" * 100 + "단, 사업자 귀책이면 배상 기준도 확인한다."
        unit = self.unit(text)
        rows = list(make_chunks(unit, 300, 40))
        validate([unit], rows, 300)
        self.assertGreater(len(rows), 1)
        evidence = parent_evidence(rows[:3], {unit["unit_id"]: unit})
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["text"], text)
        self.assertTrue(all(r["requires_parent_evidence"] for r in rows))

    def test_short_legal_article_is_not_merged_into_another_article(self):
        unit = self.unit("제27조(해고사유 등의 서면통지) 해고사유와 해고시기를 서면으로 통지하여야 한다.")
        rows = list(make_chunks(unit))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["text"], unit["text"])

    def test_no_loss_for_unbroken_text_and_whitespace(self):
        for text in ("가" * 2031, "\n" * 500 + "근거" + " " * 600, "첫 문장.\n" * 300):
            unit = self.unit(text)
            validate([unit], list(make_chunks(unit, 300, 40)), 300)

    def test_invalid_parameters_and_corrupt_offsets_fail(self):
        for size, overlap in ((0, 0), (300, -1), (300, 150), (True, 0)):
            with self.assertRaises(ValueError):
                list(split_spans("본문", size, overlap))
        unit = self.unit("근거 본문")
        rows = list(make_chunks(unit))
        rows[0]["start"] = 1
        with self.assertRaises(ValueError):
            validate([unit], rows, 900)

    def test_source_change_and_review_gates(self):
        # Keep test fixtures inside the project, including on managed Windows hosts.
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            root = Path(directory)
            raw = root / "data/raw/consumer_sources/example.html"
            raw.parent.mkdir(parents=True)
            raw.write_bytes(b"original")
            processed = root / "data/processed/consumer_sources/example.json"
            processed.parent.mkdir(parents=True)
            common = {"document_id": "example", "title": "근거", "source_path": raw.relative_to(root).as_posix(),
                      "raw_sha256": hashlib.sha256(b"original").hexdigest()}
            units = []
            for key, text, reason in [("ok", "정상 본문", None),
                                      ("review", "검토 전 본문", "manual_review_required"),
                                      ("broken", "깨진 \ufffd 본문", None)]:
                unit, exclusion = build_evidence_unit(common, key, text, key, reason=reason)
                units.append(unit)
                if key != "ok":
                    self.assertIsNotNone(exclusion)
            document = {"schema_version": 3, "units": units}
            processed.write_text(json.dumps(document), encoding="utf-8")
            loaded = load_processed_data(root / "data/processed", raw_dir=root / "data/raw")
            self.assertEqual([u["unit_id"] for u in loaded], ["example:ok"])
            raw.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                load_processed_data(root / "data/processed", raw_dir=root / "data/raw")


if __name__ == "__main__":
    unittest.main()
