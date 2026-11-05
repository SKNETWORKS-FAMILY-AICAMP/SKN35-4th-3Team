"""검색 파이프라인의 단계별 시간을 서버 로그에 기록합니다."""

from functools import wraps
import logging
from time import perf_counter

logger = logging.getLogger("apps.chat.stages")


def timed(node, name):
    @wraps(node)
    def run(state):
        started = perf_counter()
        try:
            return node(state)
        finally:
            logger.info("chat stage=%s elapsed_seconds=%.2f", name, perf_counter() - started)
    return run
