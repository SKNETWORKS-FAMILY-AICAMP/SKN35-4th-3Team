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


def clean(text):
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


def html_extract(data, filename):
    from lxml import html

    decoded, encoding = decode_html(data)
    tree = html.fromstring(decoded)
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
    node = deepcopy(max(candidates, key=lambda n: len(n.text_content())))
    for bad in node.xpath('.//script|.//style|.//nav|.//footer|.//form|.//button|.//noscript|.//comment()'):
        bad.drop_tree() if hasattr(bad, "drop_tree") else bad.getparent().remove(bad)
    for bad in node.xpath('.//*[contains(@class,"board_v_btn") or contains(@class,"board_v_info") or contains(@class,"location") or contains(@class,"sns_")]'):
        if bad.getparent() is not None:
            bad.drop_tree()
    tables = []
    for index, table in enumerate(node.xpath('.//table')):
        if table.xpath('.//table'):
            continue  # Nested layout tables are handled at their innermost evidence tables.
        rows = []
        for row in table.xpath('.//tr'):
            cells = []
            for cell in row.xpath('./th|./td'):
                cells.append({"text": clean(cell.text_content()),
                              "rowspan": cell.get("rowspan", "1"), "colspan": cell.get("colspan", "1")})
            if cells:
                rows.append(cells)
        tables.append({"table_index": index, "rows": rows, "merged_cells_preserved": True})
        # Keep the table in the complete body; separate metadata preserves cell boundaries.
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
    return body, {"selector": selector, "encoding": encoding, "tables": tables,
                  "image_references": image_refs,
                  "images_downloaded_or_ocr_performed": False}


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
        end = body.rfind('┏', product_heads[last].end(), product_heads[last + 1].start()) if last + 1 < len(product_heads) else len(body)
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
    with zipfile.ZipFile(data_path) as archive:
        sections = sorted(n for n in archive.namelist() if re.fullmatch(r"Contents/section\d+\.xml", n))
        if not sections:
            raise ValueError("HWPX section XML missing")
        parts, tables = [], []
        def render(node):
            name = tag(node)
            if name == "t":
                return "".join(node.itertext())
            if name in {"lineBreak", "br"}:
                return "\n"
            text = "".join(render(child) for child in node)
            return text + ("\n" if name in {"p", "tr"} else " | " if name == "tc" else "")
        for section in sections:
            tree = ET.fromstring(archive.read(section))
            parts.append(render(tree))
            for node in tree.iter():
                if tag(node) == "tbl":
                    rows = []
                    for row in node:
                        if tag(row) == "tr":
                            rows.append([{"text": clean(render(cell)),
                                          "spans": [dict(c.attrib) for c in cell if tag(c) in {"cellSpan", "cellAddr"}]}
                                         for cell in row if tag(cell) == "tc"])
                    tables.append({"rows": rows})
        return clean("\n".join(parts)), {"sections": sections,
            "tables": tables,
            "table_structure_policy": "source XML order and cell spans preserved; no inferred cell meaning",
            "needs_table_layout_review": True}


def extract_pdf(path):
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


def get_exclusion_reason(text, *, reason=None, historical=False):
    """지정된 사유, 과거 자료, 깨진 문자, 빈 본문 순으로 제외 여부를 판단한다."""
    if not reason and historical:
        reason = "historical_reference_not_current"
    if not reason and ("\ufffd" in text or re.search(r"\(cid:\d+\)", text)):
        reason = "broken_characters"
    if not reason and not text:
        reason = "empty_or_image_only"
    return reason


def build_evidence_unit(common, key, text, locator, *, reason=None, historical=False, **extra):
    """근거 하나를 구성하고 제외 사유가 있으면 함께 반환한다."""
    text = clean(text)
    reason = get_exclusion_reason(text, reason=reason, historical=historical)
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


def build_pdf_entries(path, pages):
    """PDF 본문과 표에 제목 문맥과 검토 조건을 붙인다."""
    heading = ""
    for page in pages:
        number, body = page["page_number"], page["body_text"]
        found = re.findall(r"(?m)^\d+(?:\.\d+)*\.\s+[^\n]+", body)
        if found:
            heading = " > ".join(found[:3])
        locator = f"PDF {number}페이지" + (f" / {heading}" if heading else "")
        is_cover = path.parent.name == "guides" and number == 1
        is_toc = bool(re.search(r"(?:\.\s*){5,}\d+\s*$", body, re.M))
        image_review = any((img["width"] or 0) > 80 and (img["height"] or 0) > 50 for img in page["images"])
        reason = "cover_or_toc" if is_cover or is_toc else "image_layout_review_required" if image_review else None
        if path.parent.name == "law_appendices" and re.search(r"_\d+F$", path.stem):
            reason = "application_form_reference_only"
        yield {"key": f"p{number}:body", "text": body, "locator": locator, "reason": reason,
               "page_number": number, "content_type": "pdf_body",
               "removed_margin_lines": page["removed_margin_lines"], "images": page["images"]}
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
        pages = extract_pdf(path)
        return build_pdf_entries(path, pages), pages
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
    return {"preprocessing_mode": "raw_extraction", "raw_reextraction_performed": True,
            "old_processed_body_text_used": False, "ocr_performed": False,
            "raw_file_count": len(file_records), "file_status_counts": dict(Counter(r["status"] for r in file_records)),
            "parent_count": len(units), "processed_dir": str(processed_dir),
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
