"""검색 청크의 parent_id로 전처리된 전체 근거를 조회합니다."""

from functools import lru_cache

from src.scripts.data_io import load_processed_data


@lru_cache(maxsize=1)
def _parents() -> dict[str, dict]:
    # 검색 근거는 청크 생성 당시 저장된 processed 스냅샷을 기준으로 읽습니다.
    # 현재 raw 파일의 해시는 별도로 점검하며, 조회 중 원본을 재추출하지 않습니다.
    return {
        unit["unit_id"]: unit
        for unit in load_processed_data(verify_sources=False)
    }


def lookup_parent_evidence(parent_id: str) -> dict | None:
    """청크 본문만으로 조건·예외를 판단하지 않도록 부모 근거를 반환합니다."""

    if not isinstance(parent_id, str) or not parent_id.strip():
        raise ValueError("parent_id는 비어 있지 않은 문자열이어야 합니다.")
    parent = _parents().get(parent_id.strip())
    return dict(parent) if parent is not None else None
