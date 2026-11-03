"""원본 XML·HTML·HWPX·PDF에서 본문과 근거 정보를 추출한다.
"""

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import html as html_std
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from .data_io import ROOT, load_chunks, read_json
from .data_io import processed_path, save_processed_data

# 같은 별표라도 추출 배치가 다를 수 있어, 직접 대조한 두 버전만 출처를 연결한다.
REVIEWED_APPENDIX_MEMBERS = {
    "003058_270551_20251023:appendix1": "fb0b4239a7cd38164ec6fc0d39073bb750de57eb92d7ceebc7bc7a4255fe0d49",
    "003058_270551_20251023_appendix_000200E:p1:body": "67b16dab2b8fdc26ad253c1bc03a2fb8a141c59b05633f93e29503843eba0fee",
}
SHOPPING_GUIDE_SHA256 = "41f96f93f3a9d71182f8e178ef7fe753423753fe4ab8d2c0c296b593329227dc"


def attach_canonical_source(unit):
    """검토한 동일 버전 별표의 출처를 연결한다. 본문 동일성을 뜻하지 않는다."""
    expected_hash = REVIEWED_APPENDIX_MEMBERS.get(unit["unit_id"])
    if expected_hash is None:
        return unit
    if unit["raw_sha256"] != expected_hash:
        raise ValueError("Reviewed canonical source changed; review grouping again")
    return {**unit,
            "canonical_source_id": "003058_270551_20251023:appendix:000200E",
            "canonical_source_scope": "same_version_appendix_document",
            "canonical_source_url": "https://www.law.go.kr/LSW/flDownload.do?flSeq=150839759"}


def clean(text):
    """HTML 엔티티·줄바꿈·공백을 정리하고 조문 번호와 표 구분 문자는 유지한다."""
    text = html_std.unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def decode_html(data):
    # 인코딩 자동 감지가 한글을 잘못 읽을 수 있어 UTF-8부터 확인한다.
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            pass
    raise ValueError("HTML encoding could not be decoded without replacement")


def tag(node):
    return node.tag.rsplit("}", 1)[-1]


def element_text(node):
    return clean("".join(node.itertext()))


def content_text(node):
    """조·항·호 순서를 유지하며 내용 필드를 한 번씩 읽는다."""
    parts = []
    for child in node.iter():
        if tag(child).endswith("내용"):
            # 중첩된 내용 필드를 중복으로 읽지 않는다.
            if not any(tag(n).endswith("내용") for n in child.iter() if n is not child):
                value = element_text(child)
                if value:
                    parts.append(value)
    return "\n".join(parts)


def iso_date(value):
    value = str(value or "")
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}" if re.fullmatch(r"\d{8}", value) else value or None


def catalog(root):
    """출처 설정을 읽는다. 설정 파일이 없으면 이전 형식의 메타데이터만 복구한다."""
    saved = root / "src/const/data_sources.json"
    if saved.exists():
        return read_json(saved)
    result, document_meta = {}, {}
    for chunk in load_chunks(root / "data/chunk"):
        key = chunk.get("source_id") or chunk.get("version_id") or chunk.get("guide_id")
        document_meta.setdefault(key, {k: chunk.get(k) for k in ("domains", "dates")})
    for path in sorted((root / "data/processed").rglob("*.json")):
        doc = read_json(path)
        if not isinstance(doc, dict):
            continue
        identity = doc.get("source_id") or doc.get("version_id") or doc.get("guide_id")
        if not identity:
            continue
        kind = path.parent.name
        raw = doc.get("raw_file") or doc.get("source_path")
        if not raw and kind == "laws":
            raw = f"data/raw/laws/{doc['source_file']}"
        if raw:
            result[raw] = {
                "document_id": identity, "title": doc.get("law_name") or doc["title"],
                "source_url": doc.get("source_url"),
                "document_type": doc.get("document_type", kind),
                **document_meta.get(identity, {}),
            }
    return result


def remove_case_page_metadata(node):
    """소비자원 게시판의 화면 정보만 본문에서 분리한다."""
    page_metadata = {}
    for table in node.xpath('.//table[contains(concat(" ", normalize-space(@class), " "), " v_tbl ")]'):
        for row in table.xpath('./thead/tr|./tbody/tr|./tr'):
            if not {"web", "mobile"}.intersection(row.get("class", "").split()):
                continue
            cells = row.xpath('./th|./td')
            if not cells or len(cells) % 2:
                continue
            pairs = list(zip(cells[::2], cells[1::2]))
            if not all(key.tag == "th" and value.tag == "td"
                       and clean(key.text_content()) in {"수정일", "조회수"}
                       for key, value in pairs):
                continue
            for key, value in pairs:
                label = clean(key.text_content())
                values = page_metadata.setdefault(label, [])
                text = clean(value.text_content())
                if text not in values:
                    values.append(text)
            row.drop_tree()
        for caption in table.xpath('./caption'):
            text = re.sub(r"\s+", "", caption.text_content())
            if text.startswith("이표는") and "항목으로구성되어있습니다" in text:
                caption.drop_tree()
    return page_metadata


def separate_board_screen_elements(node):
    """확인한 소비자원 게시판 구조의 화면 문구를 따로 보관한다."""
    excluded = []
    page_metadata = {}

    def separate(element, reason):
        excluded.append({"reason": reason, "text": clean(element.text_content()),
                         "selector": element.getroottree().getpath(element),
                         "search_excluded": True})
        element.drop_tree()

    for table in node.xpath('.//table[contains(concat(" ", normalize-space(@class), " "), " board_insert ")]'):
        # 상담 내용에 등장하는 같은 단어를 지우지 않도록 직속 행만 확인한다.
        for row in table.xpath('./thead/tr|./tbody/tr|./tr'):
            cells = row.xpath('./th|./td')
            if not cells or len(cells) % 2:
                continue
            pairs = list(zip(cells[::2], cells[1::2]))
            labels = [clean(key.text_content()) for key, _ in pairs]
            if not all(key.tag == "th" and value.tag == "td" for key, value in pairs):
                continue
            if set(labels) <= {"등록일", "조회수"}:
                for key, value in pairs:
                    page_metadata.setdefault(clean(key.text_content()), []).append(clean(value.text_content()))
                separate(row, "board_metadata")
            elif len(pairs) == 1 and labels[0] in {"이전글", "다음글"}:
                value = pairs[0][1]
                if {"prev_cont", "next_cont"}.intersection(value.get("class", "").split()):
                    separate(row, "adjacent_post")
        for caption in table.xpath('./caption'):
            if "게시글 상세보기" in caption.text_content() and "조회수" in caption.text_content():
                separate(caption, "board_accessibility_caption")

    for footer in node.xpath('.//div[contains(concat(" ", normalize-space(@class), " "), " footerIn ")]'):
        popup = footer.xpath('./div[@id="popup_layer" and contains(concat(" ", normalize-space(@class), " "), " popup_layer ")]')
        if popup and "삭제사유" in popup[0].text_content() and "홈페이지 게시물 관리" in popup[0].text_content():
            separate(footer, "board_management_footer")
    return page_metadata, excluded


def select_html_body(tree, filename):
    """사이트별로 확인한 본문 영역을 선택한다."""
    if filename.startswith("easylaw_"):
        candidates = tree.xpath('//*[@id="contents"]/div[not(@id) and not(@class)]')
        selector = '#contents > div without navigation classes'
    elif filename.startswith(("kca_case_", "kca_pilates_")):
        candidates = tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," board_view ")]')
        selector = '.board_view'
    elif filename.startswith("moel_wage_evidence"):
        candidates = tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," body03_g ")]')
        selector = '.body03_g'
    elif filename.startswith("moel_wage_procedure"):
        candidates = tree.xpath('//*[contains(concat(" ",normalize-space(@class)," ")," contents ")]')
        selector = '.contents'
    else:
        candidates, selector = [], None
        for candidate in ("contents", "contentBody", "content"):
            found = tree.xpath(f'//*[@id="{candidate}"]')
            if found:
                candidates, selector = found, '#' + candidate
                break
    if not candidates:
        raise ValueError("No reviewed site-specific body selector matched")
    return deepcopy(max(candidates, key=lambda n: len(n.text_content()))), selector


def remove_submission_popups(node):
    """일반 접수 안내와 구분되는 팝업을 분리하고 원문을 보관한다."""
    excluded_popups = []
    for popup in node.xpath('.//*[@id]'):
        classes = set(popup.get("class", "").split())
        if re.fullmatch(r"layerPopupNotice\d+", popup.get("id", "")) and {"layer_pop", "pgnotice"} <= classes:
            excluded_popups.append({"id": popup.get("id"), "text": clean(popup.text_content()),
                                    "reason": "event_specific_popup", "search_excluded": True})
            popup.drop_tree()
    return excluded_popups


def extract_html_tables(node):
    """본문의 표를 그대로 두고 셀 경계와 병합 정보를 따로 기록한다."""
    tables = []
    for index, table in enumerate(node.xpath('.//table')):
        if table.xpath('.//table'):
            continue  # 중첩 표는 가장 안쪽 표에서 읽는다.
        rows = []
        for row in table.xpath('.//tr'):
            cells = []
            for cell in row.xpath('./th|./td'):
                cells.append({"text": clean(cell.text_content()),
                              "rowspan": cell.get("rowspan", "1"), "colspan": cell.get("colspan", "1")})
            if cells:
                rows.append(cells)
        tables.append({"table_index": index, "rows": rows, "merged_cells_preserved": True})
    return tables


def html_extract(data, filename):
    """사이트별 본문을 읽고, 분리한 화면 정보와 표 구조를 함께 반환한다."""
    from lxml import html

    decoded, encoding = decode_html(data)
    node, selector = select_html_body(html.fromstring(decoded), filename)
    for bad in node.xpath('.//script|.//style|.//nav|.//footer|.//form|.//button|.//noscript|.//comment()'):
        if hasattr(bad, "drop_tree"):
            bad.drop_tree()
        else:
            bad.getparent().remove(bad)
    screen_elements_xpath = (
        './/*[contains(@class,"board_v_btn") or contains(@class,"board_v_info") '
        'or contains(@class,"location") or contains(@class,"sns_")]'
    )
    for bad in node.xpath(screen_elements_xpath):
        if bad.getparent() is not None:
            bad.drop_tree()
    page_metadata = {}
    if filename.startswith(("kca_case_", "kca_pilates_")):
        page_metadata = remove_case_page_metadata(node)
    excluded_screen_elements = []
    if filename.startswith("kca_"):
        board_metadata, excluded_screen_elements = separate_board_screen_elements(node)
        for key, values in board_metadata.items():
            page_metadata.setdefault(key, []).extend(values)
    excluded_popups = []
    if filename.startswith("kca_redress_submission_"):
        excluded_popups = remove_submission_popups(node)
    tables = extract_html_tables(node)
    for element in node.iter():
        if not isinstance(element.tag, str):
            continue
        if element.tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "dt", "dd", "section", "br"}:
            element.tail = "\n" + (element.tail or "")
        elif element.tag in {"td", "th"}:
            element.tail = " | " + (element.tail or "")
    body = clean(node.text_content())
    image_refs = [{"src": img.get("src"), "alt": img.get("alt", "")}
                  for img in node.xpath('.//img') if img.get("src")]
    details = {"selector": selector, "encoding": encoding, "tables": tables,
                  "image_references": image_refs,
                  "images_downloaded_or_ocr_performed": False}
    if page_metadata:
        details["page_metadata"] = page_metadata
    if excluded_popups:
        details["excluded_popups"] = excluded_popups
    if excluded_screen_elements:
        details["excluded_screen_elements"] = excluded_screen_elements
    return body, details


def extract_consumer_industry_tables(body, number='0002'):
    """체육시설·인터넷쇼핑 업종의 표 전체를 선별한다."""
    result = []
    headings = list(re.finditer('(?m)^[ \\t]*<[^>\\n]+>', body))
    selected = set()
    for i, match in enumerate(headings):
        for name, domain in (('체육시설업', 'fitness_refund'), ('인터넷쇼핑몰업', 'shopping_return')):
            if name not in match.group():
                continue
            end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
            result.append({
                'key': f'industry:{domain}',
                'text': body[match.start():end],
                'locator': f'별표 {number} / {name}',
                'content_type': 'industry_table',
                'domains': [domain],
                'source_start': match.start(),
                'source_end': end,
                'source_offset_basis': '별표단위[별표번호=0002].별표내용',
                'calculation_restricted': True,
                'warnings': ['XML 문자 표: 조건·예외 전체 보존, PDF 셀 배치 대조 전 수치 계산 금지'],
            })
            selected.add(domain)
    if selected != {'fitness_refund', 'shopping_return'}:
        raise ValueError('Required consumer refund sections not found in raw XML')
    return result


def extract_consumer_product_tables(body, number='0002'):
    """귀금속·전자제품 표와 원문 위치를 추출한다."""
    result = []
    product_heads = list(re.finditer('(?m)^┃[ \\t]*[①-⑳][^│\\n]*┃[ \\t]*$', body))
    for name in ('귀금속', '전자제품'):
        positions = [i for i, h in enumerate(product_heads) if name in h.group()]
        if not positions:
            raise ValueError(f'Product heading missing: {name}')
        first, last = (positions[0], positions[-1])
        start = body.rfind('┏', 0, product_heads[first].start())
        if last + 1 < len(product_heads):
            end = body.rfind('┏', product_heads[last].end(), product_heads[last + 1].start())
        else:
            end = len(body)
        if start < 0 or end <= product_heads[last].end():
            raise ValueError(f'Product table boundary invalid: {name}')
        result.append({
            'key': f'product:{name}',
            'text': body[start:end],
            'locator': f'별표 {number} / {name}',
            'content_type': 'product_table',
            'domains': ['shopping_return'],
            'source_start': start,
            'source_end': end,
            'source_offset_basis': '별표단위[별표번호=0002].별표내용',
            'calculation_restricted': True,
            'warnings': ['XML 표의 병합 관계 미확인: 전체 조건 확인 및 PDF 대조 필요'],
        })
    return result


def extract_consumer_refund_tables(body, number='0002'):
    """대상 업종과 품목의 표를 원문 그대로 모은다."""
    return extract_consumer_industry_tables(body, number) + extract_consumer_product_tables(body, number)


def extract_administrative_xml(tree):
    """행정규칙의 조문·부칙·대상 별표를 읽는다."""
    result = []
    title = tree.findtext('행정규칙기본정보/행정규칙명')
    date = iso_date(tree.findtext('행정규칙기본정보/시행일자'))
    for index, child in enumerate(tree.findall('조문내용')):
        result.append({
            'key': f'article{index}',
            'text': element_text(child),
            'locator': next(iter(element_text(child).splitlines()), '빈 조문')[:120],
            'content_type': 'administrative_rule',
            'effective_date': date,
        })
    for index, child in enumerate(tree.findall('부칙')):
        result.append({
            'key': f'supplement{index}',
            'text': element_text(child),
            'locator': '부칙',
            'content_type': 'supplement',
        })
    for index, appendix in enumerate(tree.findall('.//별표단위')):
        body = appendix.findtext('별표내용', '')
        number = appendix.findtext('별표번호', '')
        heading = appendix.findtext('별표제목', '별표')
        if title == '소비자분쟁해결기준':
            if number == '0002':
                result.extend(extract_consumer_refund_tables(body, number))
            continue
        if body:
            result.append({
                'key': f'appendix{index}',
                'text': body,
                'locator': f'별표 {number} / {heading}',
                'content_type': 'appendix',
                'calculation_restricted': True,
            })
    return (title, result)


def extract_law_xml(tree):
    """법령의 조문·부칙·별표를 원문 순서로 읽는다."""
    result = []
    title = tree.findtext('.//기본정보/법령명_한글')
    for index, article in enumerate(tree.findall('.//조문/조문단위')):
        if article.findtext('조문여부') != '조문':
            continue
        number, branch = (article.findtext('조문번호', ''), article.findtext('조문가지번호', ''))
        label = f'제{number}조' + (f'의{branch}' if branch else '')
        heading = article.findtext('조문제목', '')
        text = content_text(article)
        result.append({
            'key': f'article{index}',
            'text': text,
            'locator': label + (f'({heading})' if heading else ''),
            'article_number': number,
            'branch_number': branch,
            'effective_date': iso_date(article.findtext('조문시행일자')),
            'content_type': 'main_article',
            'deleted': bool(re.match('^제\\d+조(?:의\\d+)?\\s*삭제', text)),
        })
    for index, supplement in enumerate(tree.findall('.//부칙/부칙단위')):
        result.append({
            'key': f'supplement{index}',
            'text': content_text(supplement),
            'locator': '부칙 / ' + supplement.findtext('부칙공포일자', ''),
            'content_type': 'supplement',
            'temporal_scope': 'historical_supplement',
        })
    for index, appendix in enumerate(tree.findall('.//별표/별표단위')):
        text = appendix.findtext('별표내용', '')
        if text:
            result.append({
                'key': f'appendix{index}',
                'text': clean(text),
                'locator': appendix.findtext('별표제목', '별표'),
                'content_type': 'appendix',
            })
    return (title, result)


def xml_extract(data, historical=False):
    """XML 종류에 따라 법령 또는 행정규칙 추출 함수를 호출한다."""
    tree = ET.fromstring(data)
    if tree.tag == 'AdmRulService':
        return extract_administrative_xml(tree)
    return extract_law_xml(tree)


def hwpx_extract(data_path):
    """HWPX의 XML 순서로 본문을 읽고 표 셀의 병합 정보를 별도로 보관한다."""
    with zipfile.ZipFile(data_path) as archive:
        sections = sorted(n for n in archive.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n))
        if not sections:
            raise ValueError("HWPX section XML missing")
        parts, tables = [], []
        def render(node):
            # 한 단어가 여러 run으로 나뉠 수 있으므로 글자 조각 사이에 공백을 넣지 않는다.
            name = tag(node)
            if name == "t":
                return "".join(node.itertext())
            if name in {"lineBreak", "br"}:
                return "\n"
            text = "".join(render(child) for child in node)
            if name in {"p", "tr"}:
                return text + "\n"
            if name == "tc":
                return text + " | "
            return text
        for section in sections:
            tree = ET.fromstring(archive.read(section))
            parts.append(render(tree))
            for node in tree.iter():
                if tag(node) != "tbl":
                    continue
                rows = []
                for row in node:
                    if tag(row) != "tr":
                        continue
                    cells = []
                    for cell in row:
                        if tag(cell) != "tc":
                            continue
                        spans = []
                        for child in cell:
                            if tag(child) in {"cellSpan", "cellAddr"}:
                                spans.append(dict(child.attrib))
                        cells.append({"text": clean(render(cell)), "spans": spans})
                    rows.append(cells)
                tables.append({"rows": rows})
        return clean("\n".join(parts)), {"sections": sections,
            "tables": tables,
            "table_structure_policy": "source XML order and cell spans preserved; no inferred cell meaning",
            "needs_table_layout_review": True}


def extract_pdf(path):
    """페이지별 전체 텍스트·표 밖 본문·표·이미지 위치를 추출한다. OCR은 하지 않는다."""
    import pdfplumber

    with pdfplumber.open(path) as document:
        pages = []
        for number, page in enumerate(document.pages, 1):
            tables = []
            for table in page.find_tables():
                rows = table.extract()
                tables.append({"bbox": list(table.bbox), "rows": rows,
                               "merged_cells_policy": "null cells preserved, not forward-filled"})
            def outside(obj):
                # 표는 별도 근거로 저장하므로 본문 추출에서는 표 안의 글자만 제외한다.
                if obj.get("object_type") != "char":
                    return True
                x, y = (obj["x0"] + obj["x1"]) / 2, (obj["top"] + obj["bottom"]) / 2
                return not any(t["bbox"][0] <= x <= t["bbox"][2] and t["bbox"][1] <= y <= t["bbox"][3] for t in tables)
            text = clean(page.extract_text() or "")
            body = clean(page.filter(outside).extract_text() or "")
            images = [{"bbox": [i["x0"], i["top"], i["x1"], i["bottom"]],
                       "width": i.get("width"), "height": i.get("height")}
                      for i in page.images]
            pages.append({"page_number": number, "raw_text": text, "body_text": body,
                          "tables": tables, "images": images})
        # 반복되는 법률 조건을 지우지 않도록 페이지 가장자리만 확인한다.
        margins = Counter()
        for page in pages:
            lines = page["body_text"].splitlines()
            margins.update(set(lines[:1] + lines[-1:]))
        repeated = {line for line, count in margins.items() if count >= max(3, len(pages) * .6)}
        for page in pages:
            lines = page["body_text"].splitlines()
            removed = []
            for index in reversed(range(len(lines))):
                if index in {0, len(lines)-1} and (lines[index] in repeated or re.fullmatch(r"\d+\s*/\s*\d+", lines[index])):
                    removed.append(lines.pop(index))
            page["body_text"] = "\n".join(lines)
            page["removed_margin_lines"] = removed
        return pages


def get_exclusion_reason(text, *, reason=None, historical=False, locator="", content_type=None):
    """제외 사유를 판단한다. 짧다는 이유만으로 정상 본문을 제외하지 않는다."""
    if not reason and historical:
        reason = "historical_reference_not_current"
    if not reason and ("\ufffd" in text or re.search(r"\(cid:\d+\)", text)):
        reason = "broken_characters"
    if not reason and not text:
        reason = "empty_or_image_only"
    if not reason and re.fullmatch(r"(?:\[(?:별표|별지)[^\]\n]*\]\s*)?삭제\s*(?:<[^<>\n]+>)?", text):
        reason = "deleted_notice"
    if not reason and content_type == "main_article":
        compact = re.sub(r"\s+", "", text)
        if compact == re.sub(r"\s+", "", locator) and re.fullmatch(r"제\d+조(?:의\d+)?(?:\([^()\n]+\))?", compact):
            reason = "heading_only"
    return reason


def build_evidence_unit(common, key, text, locator, *, reason=None, historical=False, **extra):
    """근거 하나를 구성하고 제외 사유가 있으면 함께 반환한다."""
    text = clean(text)
    reason = get_exclusion_reason(text, reason=reason, historical=historical,
                                  locator=locator, content_type=extra.get("content_type"))
    # 제외된 근거도 전처리 결과에는 남기고, 청킹 여부만 embedding_candidate로 구분한다.
    unit = {**common, "unit_id": f"{common['document_id']}:{key}", "text": text,
            "locator": locator, "embedding_candidate": reason is None, **extra}
    exclusion = {
        "source_path": common["source_path"],
        "unit_id": unit["unit_id"],
        "reason": reason,
    } if reason else None
    return unit, exclusion


def build_source_metadata(path, raw_dir, metadata, data):
    """원본 하나의 출처 정보와 해시를 구성한다."""
    relative = "data/raw/" + path.relative_to(raw_dir).as_posix()
    meta = dict(metadata.get(relative, {}))
    identity = meta.get("document_id") or path.stem
    if not meta and path.parent.name == "consumer_sources":
        matches = [value for value in metadata.values()
                   if path.stem.startswith(value["document_id"] + "_")]
        if matches:
            meta = dict(max(matches, key=lambda value: len(value["document_id"])))
            meta["related_document_id"] = meta["document_id"]
    if path.parent.name == "law_appendices":
        version = path.stem.split("_appendix_", 1)[0]
        meta = {**metadata.get(f"data/raw/laws/{version}.json", {}), **meta}
    return {**meta, "document_id": identity, "title": meta.get("title", path.stem),
            "source_path": relative, "raw_sha256": hashlib.sha256(data).hexdigest(),
            "extraction_origin": "raw", "domains": meta.get("domains", []),
            "warnings": [], "dates": meta.get("dates", {})}


def pdf_example_sections(pages):
    """검토한 쇼핑 안내의 예시 문맥을 다음 페이지의 출처 표시까지 연결한다."""
    by_number = {page["page_number"]: page for page in pages}
    if 29 not in by_number or 30 not in by_number:
        raise ValueError("Reviewed example pages are missing")
    first, second = by_number[29]["body_text"], by_number[30]["body_text"]
    title = "※ 청약철회 방해문구 사례"
    marker = first.find("<예시>")
    end = re.search(r"(?m)^<\s*출처:.*전자거래분쟁조정사례집.*>[^\S\n]*\n?", second)
    if not first.startswith(title) or marker < 0 or not end or not second[end.end():].startswith("물품반환 비용 부담"):
        raise ValueError("Reviewed example context or ending has changed; review the PDF again")
    context = first[:marker + len("<예시>")]
    return {
        29: [{"start": 0, "end": len(first), "original_text": first,
              "text": first, "role": "misleading_merchant_examples", "label": "청약철회 방해문구 사례"}],
        30: [{"start": 0, "end": end.end(), "original_text": second[:end.end()],
              "text": context + "\n" + second[:end.end()], "role": "misleading_merchant_examples",
              "label": "청약철회 방해문구 사례", "context_source": {"page_number": 29, "start": 0,
                  "end": marker + len("<예시>"), "text": context}},
             {"start": end.end(), "end": len(second), "original_text": second[end.end():],
              "text": second[end.end():], "role": "guidance", "label": "물품반환 비용 부담"}],
    }


def read_pdf_toc(pages):
    """점선·쪽수가 있는 목차 행에서 제목과 목차 페이지 번호를 읽는다."""
    toc = {}
    toc_pages = set()
    pattern = re.compile(r"(?m)^(\d+(?:\.\d+)*)\.\s+(.+?)\s+(?:\.\s*){5,}(\d+)\s*$")
    for page in pages:
        # 목차의 점선과 쪽수가 함께 있는 행만 제목 후보로 등록한다.
        for match in pattern.finditer(page["body_text"]):
            key, title, _ = match.groups()
            toc[key] = key + ". " + title.strip()
            toc_pages.add(page["page_number"])
    return toc, toc_pages


def pdf_page_heading_paths(body, toc):
    """본문과 목차에서 일치한 제목을 골라 같은 페이지의 상하 관계를 구성한다."""
    matches = []
    # 번호만 같아서는 부족하다. 목차와 제목까지 일치해야 날짜·번호 목록을 피할 수 있다.
    for match in re.finditer(r"(?m)^(\d+(?:\.\d+)*)\.\s+[^\n]+", body):
        key = match.group(1)
        if key in toc and re.sub(r"\s+", "", match.group()) == re.sub(r"\s+", "", toc[key]):
            matches.append(key)
    visible_keys = set(matches)
    def hierarchy(key, visible_keys):
        parts = key.split(".")
        return " > ".join(toc[prefix] for depth in range(1, len(parts) + 1)
                          if (prefix := ".".join(parts[:depth])) in visible_keys)
    return [hierarchy(key, visible_keys) for key in matches]


def pdf_heading_contexts(pages):
    """목차와 일치하는 제목만 사용해 안내서의 페이지 문맥을 구성한다."""
    toc, toc_pages = read_pdf_toc(pages)
    if len(toc) < 3 or not any("." in key for key in toc):
        return {}

    contexts = {}
    active = ""
    for page in pages:
        number, body = page["page_number"], page["body_text"]
        if number in toc_pages:
            contexts[number] = ""
            continue
        paths = pdf_page_heading_paths(body, toc)
        if not paths:
            contexts[number] = active
            continue
        active = paths[-1]
        # 같은 페이지의 형제 절을 상하 관계로 연결하지 않는다.
        paths = list(dict.fromkeys(paths))
        contexts[number] = " | ".join(path for path in paths
                                      if not any(other.startswith(path + " > ") for other in paths))
    return contexts


def build_pdf_entries(path, pages):
    """PDF 본문과 표에 제목 문맥과 검토 조건을 붙인다."""
    heading = ""
    guide_headings = pdf_heading_contexts(pages) if path.parent.name == "guides" else {}
    sections = pdf_example_sections(pages) if path.name == "[pdf]인터넷 쇼핑.pdf" else {}
    for page in pages:
        number, body = page["page_number"], page["body_text"]
        found = re.findall(r"(?m)^\d+(?:\.\d+)*\.\s+[^\n]+", body)
        if guide_headings:
            heading = guide_headings[number]
        elif found:
            heading = " > ".join(found[:3])
        locator = f"PDF {number}페이지" + (f" / {heading}" if heading else "")
        is_cover = path.parent.name == "guides" and number == 1
        is_toc = bool(re.search(r"(?:\.\s*){5,}\d+\s*$", body, re.M))
        has_large_image = any((img["width"] or 0) > 80 and (img["height"] or 0) > 50
                              for img in page["images"])
        if is_cover or is_toc:
            reason = "cover_or_toc"
        elif has_large_image:
            reason = "image_layout_review_required"
        else:
            reason = None
        if path.parent.name == "law_appendices" and re.search(r"_\d+F$", path.stem):
            reason = "application_form_reference_only"
        body_reason = reason
        # 표는 별도 근거로 보존하고, 별표 번호와 제목뿐인 페이지 본문은 검색에서 뺀다.
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if not reason and page["tables"] and len(lines) == 2:
            appendix_label = re.fullmatch(r"(?:<별표[^<>]+>|(?:■\s*[^\[\n]+\s*)?\[별표[^\]\n]*\](?:\s*<[^<>]+>)?)", lines[0])
            title = re.fullmatch(r"[^.!?\n]+(?:표|기준|규정|관할구역|직종|수수료)(?:\([^()]*\))?", lines[1])
            if appendix_label and title:
                body_reason = "heading_only"
        base = {"key": f"p{number}:body", "text": body, "locator": locator, "reason": body_reason,
                "page_number": number, "content_type": "pdf_body",
                "removed_margin_lines": page["removed_margin_lines"], "images": page["images"]}
        if number in sections:
            for index, section in enumerate(sections[number]):
                yield {**base, "key": base["key"] + (":rules" if index else ""), "text": section["text"],
                       "locator": locator + " / " + section["label"], "content_role": section["role"],
                       "evidence_metadata": {"pdf_body_section": {"page_number": number,
                           "start": section["start"], "end": section["end"],
                           "original_text": section["original_text"]},
                           "context_source": section.get("context_source")}}
        else:
            yield base
        for index, table in enumerate(page["tables"]):
            table_text = "\n".join(" | ".join(str(c or "") for c in row) for row in table["rows"])
            yield {"key": f"p{number}:table{index}", "text": table_text,
                   "locator": locator + f" / 표 {index+1}", "reason": reason,
                   "page_number": number, "content_type": "table", "table": table,
                   "warnings": ["병합 셀의 빈칸을 임의로 채우지 않음; 전체 표와 조건을 함께 확인"]}


def extract_source_entries(path, data, common):
    """파일 형식에 맞게 추출하고 XML에 있는 출처 정보를 반영한다."""
    historical = path.parent.name == "law_history"
    if path.suffix == ".json":
        raw = json.loads(data.decode("utf-8-sig"))
        if not raw.get("response_xml"):
            raise ValueError("Raw JSON has no source response_xml")
        common["source_url"] = raw.get("source_url")
        common["version_scope"] = raw.get("version_scope")
        title, entries = xml_extract(raw["response_xml"], historical)
        common["title"] = title or common["title"]
        if historical:
            common["dates"] = {"effective_date": iso_date(raw.get("effective_date"))}
        for entry in entries:
            deleted = entry.pop("deleted", False)
            entry["reason"] = "deleted_article" if deleted else None
        return entries, None
    if path.suffix == ".xml":
        title, entries = xml_extract(data)
        common["title"] = title or common["title"]
        return entries, None
    if path.suffix == ".html":
        if historical:
            return [{"key": "history_index", "text": "", "locator": "법령 연혁 목록",
                     "reason": "history_navigation_index"}], None
        text, details = html_extract(data, path.name)
        return [{"key": "body", "text": text, "locator": details["selector"], "content_type": "html_body",
                 "evidence_metadata": details,
                 "warnings": ["본문 내 이미지는 다운로드·OCR하지 않음; 표의 조건·예외는 전체 근거에서 확인"]}], None
    if path.suffix == ".hwpx":
        text, details = hwpx_extract(path)
        return [{"key": "body", "text": text, "locator": "HWPX section XML", "evidence_metadata": details,
                 "content_type": "terms", "warnings": ["표 셀 배치 검토 전: 수치 계산 근거로 단독 사용 금지"]}], None
    if path.suffix == ".pdf":
        from .pdf_tables import reviewed_pdf_entries

        if path.name == "[pdf]인터넷 쇼핑.pdf" and common["raw_sha256"] != SHOPPING_GUIDE_SHA256:
            raise ValueError("Shopping guide differs from the reviewed PDF version")
        pages = extract_pdf(path)
        entries = list(build_pdf_entries(path, pages))
        reviewed = reviewed_pdf_entries(path, pages)
        if reviewed:
            for entry in entries:
                if entry["key"] == "p2:table0" and entry["text"] == "법 제12조제1항부터 제3항 | 까":
                    entry["reason"] = "superseded_by_reviewed_rows"
            entries.extend(reviewed)
        return entries, pages
    raise ValueError(f"Unsupported format: {path.suffix}")


def preprocess_file(path, raw_dir, metadata):
    """파일 하나의 근거·제외 내역·처리 기록·PDF 페이지를 반환한다."""
    path, raw_dir = Path(path), Path(raw_dir)
    data = path.read_bytes()
    common = build_source_metadata(path, raw_dir, metadata, data)
    record = {"path": common["source_path"], "sha256": common["raw_sha256"], "format": path.suffix,
              "status": "extracted", "extracted_units": 0, "indexed_units": 0}
    extracted, excluded, pdf_pages = [], [], None
    try:
        entries, pages = extract_source_entries(path, data, common)
        for entry in entries:
            unit, exclusion = build_evidence_unit(common, historical=path.parent.name == "law_history", **entry)
            if unit["unit_id"] == "consumer_dispute_standard:industry:fitness_refund":
                from .pdf_tables import apply_refund_formulas

                unit = apply_refund_formulas(unit, raw_dir=raw_dir)
            unit = attach_canonical_source(unit)
            extracted.append(unit)
            if exclusion:
                excluded.append(exclusion)
        if pages is not None:
            record["page_count"] = len(pages)
            record["pages_with_images"] = sum(bool(p["images"]) for p in pages)
            record["page_extraction_file"] = str(processed_path(common["source_path"], common["document_id"]))
            pdf_pages = pages
    except (ValueError, ET.ParseError, OSError) as error:
        record["status"] = "failed"
        record["error"] = str(error)
    record["extracted_units"] = len(extracted)
    record["indexed_units"] = sum(u["embedding_candidate"] for u in extracted)
    if record["status"] == "extracted" and not any(u["text"] for u in extracted):
        record["status"] = "metadata_or_index_only"
    return {"units": extracted, "exclusions": excluded, "record": record, "pdf_pages": pdf_pages}


def build_preprocessing_report(file_records, documents, excluded, processed_dir):
    """근거 ID 중복을 검사하고 전처리 결과를 집계한다."""
    units = [unit for unit in documents if unit["embedding_candidate"]]
    ids = [unit["unit_id"] for unit in units]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate parent IDs in raw extraction")
    supplemental_paths = {source["path"] for unit in documents
                          for source in unit.get("supplemental_sources", [])}
    return {"preprocessing_mode": "raw_extraction", "raw_reextraction_performed": True,
            "old_processed_body_text_used": False, "ocr_performed": False,
            "raw_file_count": len(file_records), "file_status_counts": dict(Counter(r["status"] for r in file_records)),
            "parent_count": len(units), "processed_dir": str(processed_dir),
            "supplemental_source_count": len(supplemental_paths),
            "exclusions": excluded, "excluded_by_reason": dict(Counter(x["reason"] for x in excluded)),
            "files": file_records, "validation": "unique parent IDs passed"}


def save_preprocessing_report(report, report_dir):
    """전처리 보고서를 저장하고 경로를 반환한다."""
    path = Path(report_dir) / "quality_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def preprocess_data(raw_dir=None, processed_dir=None, *, root=ROOT, report_dir=None, progress=None):
    """원본 파일을 처리하고 전처리 결과와 보고서를 저장한다."""
    root = Path(root).resolve()
    raw_dir = Path(raw_dir or root / "data/raw").resolve()
    processed_dir = Path(processed_dir or root / "data/processed").resolve()
    report_dir = Path(report_dir or root / "docs/data_reports").resolve()
    if not raw_dir.is_dir():
        raise FileNotFoundError(f"Raw directory does not exist: {raw_dir}")
    if raw_dir == processed_dir or raw_dir.is_relative_to(processed_dir) or processed_dir.is_relative_to(raw_dir):
        raise ValueError("Raw and processed directories must not overlap")
    metadata = catalog(root)
    if processed_dir == (root / "data/processed").resolve():
        metadata_path = root / "src/const/data_sources.json"
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    excluded, file_records, documents, pdf_pages = [], [], [], {}
    raw_files = sorted(p for p in raw_dir.rglob("*") if p.is_file())
    if not raw_files:
        raise ValueError(f"No raw files found: {raw_dir}")
    for file_index, path in enumerate(raw_files, 1):
        result = preprocess_file(path, raw_dir, metadata)
        record = result["record"]
        file_records.append(record)
        documents.extend(result["units"])
        excluded.extend(result["exclusions"])
        if result["pdf_pages"] is not None:
            pdf_pages[record["path"]] = result["pdf_pages"]
        if progress is not None:
            progress(f"[{file_index}/{len(raw_files)}] {record['path']}: {record['status']} ({record['indexed_units']} units)")
    report = build_preprocessing_report(file_records, documents, excluded, processed_dir)
    report_path = save_preprocessing_report(report, report_dir)
    # 추출 실패가 있으면 보고서만 남기고 기존 전처리 파일을 덮어쓰지 않는다.
    if report["file_status_counts"].get("failed"):
        raise ValueError(f"Raw extraction failed; see {report_path}")
    saved = save_processed_data(processed_dir, documents, pdf_pages)
    report["processed_file_count"] = len(saved)
    save_preprocessing_report(report, report_dir)
    return report


def main():
    parser = argparse.ArgumentParser(description="Preprocess raw files only; chunking is a separate command.")
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--processed-dir", type=Path)
    parser.add_argument("--report-dir", type=Path)
    args = parser.parse_args()
    report = preprocess_data(args.raw_dir, args.processed_dir, report_dir=args.report_dir, progress=print)
    print(json.dumps({k: v for k, v in report.items() if k not in ("files", "exclusions")}, indent=2))


if __name__ == "__main__":
    main()
