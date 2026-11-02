"""국가법령정보센터 판례 API의 검색·상세 조회 클라이언트."""

from __future__ import annotations

import html
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import requests
from dotenv import load_dotenv

from src.const.precedent import (
    DEFAULT_ENV_PATH,
    LAW_API_DETAIL_URL,
    LAW_API_OC_ENV,
    LAW_API_SEARCH_URL,
    LAW_PRECEDENT_SOURCE_URL,
)


class PrecedentApiError(RuntimeError):
    """외부에 노출해도 되는 오류 코드와 메시지를 함께 전달합니다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True)
class OfficialPrecedent:
    """공식 API 응답에서 사용하는 표준 판례 구조."""

    precedent_id: str
    case_name: str = ""
    case_number: str = ""
    decision_date: str = ""
    court: str = ""
    holding: str = ""
    summary: str = ""
    reference_articles: str = ""
    body: str = ""
    source_url: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class PrecedentApiLike(Protocol):
    """검증 서비스가 의존하는 최소 판례 API 인터페이스."""

    def search(
        self,
        query: str,
        *,
        display: int = 20,
        page: int = 1,
    ) -> tuple[OfficialPrecedent, ...]: ...

    def get_detail(self, precedent_id: str) -> OfficialPrecedent: ...

    def health_check(self) -> bool: ...


def _clean_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(html.unescape(text).split())


def _normalize_date(value: object) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return _clean_text(value)


def _fields(element: ET.Element) -> dict[str, str]:
    return {
        child.tag: "".join(child.itertext()).strip()
        for child in element
    }


def _precedent_id(fields: dict[str, str]) -> str:
    return _clean_text(
        fields.get("판례정보일련번호") or fields.get("판례일련번호")
    )


def _to_precedent(fields: dict[str, str]) -> OfficialPrecedent:
    identifier = _precedent_id(fields)
    if not identifier:
        raise PrecedentApiError(
            "invalid_response",
            "공식 판례 응답에 판례 식별자가 없습니다.",
        )
    return OfficialPrecedent(
        precedent_id=identifier,
        case_name=_clean_text(fields.get("사건명")),
        case_number=_clean_text(fields.get("사건번호")),
        decision_date=_normalize_date(fields.get("선고일자")),
        court=_clean_text(fields.get("법원명")),
        holding=_clean_text(fields.get("판시사항")),
        summary=_clean_text(fields.get("판결요지")),
        reference_articles=_clean_text(fields.get("참조조문")),
        body=_clean_text(fields.get("판례내용")),
        source_url=LAW_PRECEDENT_SOURCE_URL.format(precedent_id=identifier),
    )


class PrecedentApiClient:
    """인증값을 URL이나 오류 메시지에 노출하지 않는 판례 API 클라이언트."""

    def __init__(
        self,
        oc: str,
        *,
        session: requests.Session | None = None,
        timeout: tuple[float, float] = (10, 30),
    ) -> None:
        if not isinstance(oc, str) or not oc.strip():
            raise ValueError(f"{LAW_API_OC_ENV} 인증값이 필요합니다.")
        self._oc = oc.strip()
        self._session = session or requests.Session()
        self._owns_session = session is None
        self._timeout = timeout

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def _request_xml(self, url: str, **params: object) -> ET.Element:
        try:
            response = self._session.get(
                url,
                params={
                    "OC": self._oc,
                    "target": "prec",
                    "type": "XML",
                    **params,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
        except requests.RequestException as error:
            status = error.response.status_code if error.response is not None else None
            raise PrecedentApiError(
                "connection_error",
                f"공식 판례 API 요청에 실패했습니다. HTTP={status}",
            ) from None
        try:
            return ET.fromstring(response.content)
        except ET.ParseError:
            raise PrecedentApiError(
                "invalid_response",
                "공식 판례 API가 올바른 XML을 반환하지 않았습니다.",
            ) from None

    def search(
        self,
        query: str,
        *,
        display: int = 20,
        page: int = 1,
    ) -> tuple[OfficialPrecedent, ...]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query는 비어 있지 않은 문자열이어야 합니다.")
        for name, value in (("display", display), ("page", page)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name}는 양의 정수여야 합니다.")

        root = self._request_xml(
            LAW_API_SEARCH_URL,
            search=2,
            query=query.strip(),
            display=min(display, 100),
            page=page,
        )
        if root.tag != "PrecSearch":
            raise PrecedentApiError(
                "invalid_response",
                "공식 판례 API 검색 응답 형식이 올바르지 않습니다.",
            )

        results: list[OfficialPrecedent] = []
        for entry in root.findall("prec"):
            try:
                results.append(_to_precedent(_fields(entry)))
            except PrecedentApiError:
                continue
        return tuple(results)

    def get_detail(self, precedent_id: str) -> OfficialPrecedent:
        if (
            not isinstance(precedent_id, str)
            or not precedent_id.strip().isascii()
            or not precedent_id.strip().isdigit()
        ):
            raise ValueError("precedent_id는 ASCII 숫자 문자열이어야 합니다.")

        identifier = precedent_id.strip()
        root = self._request_xml(LAW_API_DETAIL_URL, ID=identifier)
        fields = _fields(root)
        if not fields or not _precedent_id(fields):
            raise PrecedentApiError(
                "not_found",
                "공식 API에서 판례를 찾지 못했습니다.",
            )
        precedent = _to_precedent(fields)
        if precedent.precedent_id != identifier:
            raise PrecedentApiError(
                "mismatch",
                "공식 API 응답의 판례 식별자가 요청값과 다릅니다.",
            )
        return precedent

    def health_check(self) -> bool:
        self.search("임대차", display=1)
        return True


def create_precedent_api_client(
    env_path: str | Path | None = None,
) -> PrecedentApiClient:
    """프로젝트 환경설정으로 공식 판례 API 클라이언트를 만듭니다."""

    load_dotenv(
        Path(env_path) if env_path is not None else DEFAULT_ENV_PATH,
        override=False,
    )
    return PrecedentApiClient(os.getenv(LAW_API_OC_ENV, ""))
