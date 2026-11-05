from django.conf import settings
from django.core.management.base import CommandError

from apps.chat.management.base import DataCommand
from src.scripts.chunk_data import chunk_data


class Command(DataCommand):
    help = '전처리 결과를 읽어 청크와 청킹 보고서를 저장합니다.'

    def add_arguments(self, parser):
        parser.add_argument('--size', type=int, default=900, help='청크 본문 최대 문자 수')
        parser.add_argument('--overlap', type=int, default=100, help='중복 구간 최대 문자 수')

    def handle(self, *args, **options):
        paths = self.get_data_paths()
        try:
            report = chunk_data(
                processed_dir=paths['processed'],
                chunk_dir=paths['chunk'],
                chunk_size=options['size'],
                overlap=options['overlap'],
                raw_dir=paths['raw'],
                root=settings.BASE_DIR,
                report_dir=paths['report'],
            )
        except (OSError, ValueError) as error:
            raise CommandError(str(error)) from error
        self.stdout.write(self.style.SUCCESS(
            f"청킹 완료: 부모 근거 {report['parent_count']}개, 청크 {report['chunk_count']}개"
        ))
