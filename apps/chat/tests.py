from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from src.scripts.chunk_data import chunk_data
from src.scripts.preprocess_data import preprocess_data


class DataCommandTests(SimpleTestCase):
    def setUp(self):
        workspace = Path(__file__).resolve().parents[2]
        temporary = tempfile.TemporaryDirectory(dir=workspace / 'tests')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.raw = self.root / 'incoming'
        self.processed = self.root / 'processed'
        self.chunks = self.root / 'chunks'
        self.reports = self.root / 'reports'
        self.source = self.raw / 'consumer_sources/example.xml'
        self.source.parent.mkdir(parents=True)
        self.source.write_text(
            '<AdmRulService><행정규칙기본정보><행정규칙명>시험 고시</행정규칙명>'
            '</행정규칙기본정보><조문내용>계약 해지의 조건과 예외를 확인한다.</조문내용>'
            '</AdmRulService>', encoding='utf-8',
        )
        configuration = override_settings(
            BASE_DIR=self.root,
            DATA_RAW_DIR=self.raw,
            DATA_PROCESSED_DIR=self.processed,
            DATA_CHUNK_DIR=self.chunks,
            DATA_REPORT_DIR=self.reports,
        )
        configuration.enable()
        self.addCleanup(configuration.disable)

    def run_command(self, name, **options):
        output = StringIO()
        call_command(name, stdout=output, **options)
        return output.getvalue()

    def file_contents(self, directory):
        return {path.relative_to(directory): path.read_bytes()
                for path in directory.rglob('*') if path.is_file()}

    def test_commands_are_discovered(self):
        for name in ('preprocess_data', 'chunk_data'):
            self.assertEqual(get_commands()[name], 'apps.chat')

    def test_commands_match_direct_functions_and_keep_stages_separate(self):
        raw_before = self.file_contents(self.raw)
        direct_processed = self.root / 'direct/processed'
        direct_chunks = self.root / 'direct/chunks'
        preprocess_data(self.raw, direct_processed, root=self.root,
                        report_dir=self.root / 'direct/reports')
        chunk_data(direct_processed, direct_chunks, raw_dir=self.raw, root=self.root,
                   report_dir=self.root / 'direct/reports', chunk_size=300, overlap=40)

        message = self.run_command('preprocess_data')
        self.assertIn('전처리 완료', message)
        self.assertFalse(self.chunks.exists())
        processed_before = self.file_contents(self.processed)
        message = self.run_command('chunk_data', size=300, overlap=40)
        self.assertIn('청킹 완료', message)
        self.assertEqual(self.file_contents(direct_processed), self.file_contents(self.processed))
        self.assertEqual(self.file_contents(direct_chunks), self.file_contents(self.chunks))
        self.assertEqual(processed_before, self.file_contents(self.processed))
        self.assertEqual(raw_before, self.file_contents(self.raw))
        self.assertTrue((self.reports / 'quality_report.json').exists())
        self.assertTrue((self.reports / 'chunking_report.json').exists())

    def test_missing_raw_is_reported_without_writing_outputs(self):
        with override_settings(DATA_RAW_DIR=self.root / 'missing'):
            with self.assertRaises(CommandError):
                self.run_command('preprocess_data')
        self.assertFalse(self.processed.exists())

    def test_invalid_chunk_options_do_not_create_outputs(self):
        with self.assertRaises(CommandError):
            self.run_command('chunk_data', size=0)
        self.assertFalse(self.chunks.exists())

    def test_report_directory_cannot_overlap_raw(self):
        raw_before = self.file_contents(self.raw)
        with override_settings(DATA_REPORT_DIR=self.raw / 'reports'):
            for name in ('preprocess_data', 'chunk_data'):
                with self.subTest(command=name), self.assertRaises(CommandError):
                    self.run_command(name)
        self.assertEqual(raw_before, self.file_contents(self.raw))
        self.assertFalse(self.processed.exists())

    def test_project_raw_is_protected_even_when_configured_raw_is_elsewhere(self):
        with override_settings(DATA_PROCESSED_DIR=self.root / 'data/raw/processed'):
            with self.assertRaises(CommandError):
                self.run_command('preprocess_data')
        self.assertFalse((self.root / 'data/raw').exists())

    def test_changed_source_keeps_previous_chunks(self):
        self.run_command('preprocess_data')
        self.run_command('chunk_data')
        previous = self.file_contents(self.chunks)
        self.source.write_text('changed', encoding='utf-8')
        with self.assertRaises(CommandError):
            self.run_command('chunk_data')
        self.assertEqual(previous, self.file_contents(self.chunks))

    def test_missing_ingestion_dependency_has_actionable_error(self):
        with patch('apps.chat.management.commands.preprocess_data.preprocess_data',
                   side_effect=ModuleNotFoundError('pdfplumber')):
            with self.assertRaisesRegex(CommandError, 'uv sync --group ingestion'):
                self.run_command('preprocess_data')
