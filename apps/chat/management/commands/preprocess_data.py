from django.conf import settings
from django.core.management.base import CommandError

from apps.chat.management.base import DataCommand
from src.scripts.preprocess_data import preprocess_data


class Command(DataCommand):
    help = '원본을 읽어 전처리 결과와 처리 보고서를 저장합니다.'

    def handle(self, *args, **options):
        paths = self.get_data_paths()
        try:
            report = preprocess_data(
                raw_dir=paths['raw'],
                processed_dir=paths['processed'],
                root=settings.BASE_DIR,
                report_dir=paths['report'],
                progress=self.stdout.write,
            )
        except ImportError as error:
            raise CommandError(
                '원본 추출 의존성을 확인하세요: uv sync --group ingestion'
            ) from error
        except (OSError, ValueError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(
            f"전처리 완료: 문서 {report['processed_file_count']}개, "
            f"청킹 대상 근거 {report['parent_count']}개"
        ))
