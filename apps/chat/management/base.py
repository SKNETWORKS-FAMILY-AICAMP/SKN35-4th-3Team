from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class DataCommand(BaseCommand):
    """두 데이터 명령어가 같은 경로 보호 규칙을 사용하도록 한다."""

    def get_data_paths(self):
        paths = {
            'raw': Path(settings.DATA_RAW_DIR).resolve(),
            'processed': Path(settings.DATA_PROCESSED_DIR).resolve(),
            'chunk': Path(settings.DATA_CHUNK_DIR).resolve(),
            'report': Path(settings.DATA_REPORT_DIR).resolve(),
        }
        # 별도 원본 경로를 사용해도 프로젝트의 원본 폴더는 출력 위치가 될 수 없다.
        original_raw = (Path(settings.BASE_DIR) / 'data/raw').resolve()
        for name in ('processed', 'chunk', 'report'):
            output = paths[name]
            if output.is_relative_to(original_raw) or original_raw.is_relative_to(output):
                raise CommandError(f'{name} 경로가 프로젝트 원본 경로와 겹칩니다: {output}')

        named_paths = list(paths.items())
        for index, (name, path) in enumerate(named_paths):
            for other_name, other_path in named_paths[index + 1:]:
                if path.is_relative_to(other_path) or other_path.is_relative_to(path):
                    raise CommandError(f'{name} 경로와 {other_name} 경로가 겹칩니다: {path}, {other_path}')
        return paths
