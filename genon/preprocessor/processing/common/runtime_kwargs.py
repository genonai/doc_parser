"""요청 스코프 런타임 토글(img_desc/chart_desc/table_desc/doc_summary/toc) 해석.

intelligent / convert / parser 세 facade 가 복제해 두었던 로직의 단일 사본이다.
세 사본은 줄바꿈과 docstring 만 달랐고 실질 로직 차이는 0 이었다.

프로세서 인스턴스를 받아 속성을 읽고 되쓴다 — 재구성 대상이 프로세서에 붙어 있는
enricher/options 라 값만 주고받는 형태로는 표현되지 않는다. 속성 접근은 전부
getattr 기본값을 거치므로 __init__ 을 우회해 만든 인스턴스에서도 죽지 않는다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from genon.preprocessor.processing.common.config_parse import as_int_flag
from genon.preprocessor.processing.enrichment.doc_summary import (
    DocSummaryEnricher,
    resolve_runtime_doc_summary_options,
)
from genon.preprocessor.processing.enrichment.image_description import (
    ImageDescriptionEnricher,
    resolve_runtime_image_options,
)
from genon.preprocessor.processing.enrichment.table_description import (
    TableDescriptionEnricher,
    resolve_runtime_table_options,
)

_log = logging.getLogger(__name__)


def normalize_runtime_kwargs(processor, kwargs: dict) -> dict:
    """이미지/차트/표 description 런타임 토글을 정규화한다(전부 0/1 플래그).

    img_desc          : 이미지 description 사용유무          → image_description.enable
    chart_desc        : 차트 description 사용유무            → chart.enable (chart_convert alias)
    chart_detection   : 1=auto(docling 자동판별)/0=all       → chart.detection
    doc_summary       : 문서 본문요약 사용유무               → body_summary.enable
    table_desc        : 표 description 사용유무              → table_description.enable
    table_refine      : 표 재구성 사용유무                   → table_description.refine.enable
    toc               : 목차 enrichment 사용유무 (toc_on alias)
    미지정 kwarg 는 config(runtime 섹션 또는 base 옵션) 기본값을 따른다.
    """
    normalized = dict(kwargs or {})
    runtime = getattr(processor, "_runtime_cfg", None) or {}
    base = getattr(processor, "_base_image_description_options", None)

    img_default = as_int_flag(runtime.get("img_desc"), 1 if (base and base.enabled) else 0)
    chart_default = as_int_flag(
        runtime.get("chart_desc", runtime.get("chart_convert")),
        1 if (base and base.chart_enabled) else 0,
    )
    detection_default = as_int_flag(
        runtime.get("chart_detection"), 1 if (base and base.chart_detection == "auto") else 0
    )
    dbase = getattr(processor, "_base_doc_summary_options", None)
    summary_default = as_int_flag(runtime.get("doc_summary"), 1 if (dbase and dbase.enabled) else 0)

    normalized["img_desc"] = as_int_flag(normalized.get("img_desc"), img_default)
    normalized["chart_desc"] = as_int_flag(
        normalized.get("chart_desc", normalized.get("chart_convert")), chart_default
    )
    normalized["chart_detection"] = as_int_flag(
        normalized.get("chart_detection"), detection_default
    )
    normalized["doc_summary"] = as_int_flag(normalized.get("doc_summary"), summary_default)

    # 표 description 런타임 토글(table_desc→enable, table_refine→refine.enable)
    tbase = getattr(processor, "_base_table_description_options", None)
    table_default = as_int_flag(runtime.get("table_desc"), 1 if (tbase and tbase.enabled) else 0)
    refine_default = as_int_flag(
        runtime.get("table_refine"), 1 if (tbase and tbase.refine_enabled) else 0
    )
    normalized["table_desc"] = as_int_flag(normalized.get("table_desc"), table_default)
    normalized["table_refine"] = as_int_flag(normalized.get("table_refine"), refine_default)

    # TOC 런타임 토글(toc/toc_on alias) — 기본값은 config 의 do_toc_enrichment.
    toc_default = as_int_flag(
        runtime.get("toc", runtime.get("toc_on")),
        1 if getattr(getattr(processor, "enrichment_options", None), "do_toc_enrichment", False) else 0,
    )
    normalized["toc"] = as_int_flag(normalized.get("toc", normalized.get("toc_on")), toc_default)
    # merge_sections 별칭은 도입하지 않는다 — 기존 chunk_mode kwarg 가 동일 기능이며
    # split_documents 의 chunk_mode 해석이 0/1/문자열을 직접 처리한다.
    return normalized


@dataclass(frozen=True)
class RequestEnrichers:
    """요청 한 건의 enrichment 토글로 만든 options·enricher 묶음(#481).

    `_start_job` 이 만들어 `job.params["_enrichers"]` 에 싣고, enrichment 단계(`docling_runtime.enrich_*`)가
    kwargs 에서 꺼내 쓴다. 프로세서 인스턴스에는 쓰지 않는다 — 같은 인스턴스로 문서 둘을 동시에
    처리해도 서로의 토글을 덮지 않기 위해서다. 기준 옵션(`_base_*_options`)이 없는 종류는 None 이고,
    그때 enrich_* 래퍼는 기동 때 만든 인스턴스 enricher 를 쓴다.
    """
    image_options: object = None
    image: object = None
    table_options: object = None
    table: object = None
    doc_summary_options: object = None
    doc_summary: object = None


def build_request_enrichers(processor, kwargs: dict) -> RequestEnrichers:
    """정규화된 kwargs 로 요청용 image/table/doc_summary options·enricher 를 만든다(#481).

    순수 override 계산은 각 enrichment 모듈의 resolve_runtime_* 에 위임한다. processor 는 읽기만 한다
    (`_base_*_options`, `pipe_line_options`). base 옵션이 없는 프로세서는 해당 항목이 None 이다.
    """
    doc_summary = as_int_flag(kwargs.get("doc_summary"), 0)
    image_options = image = None
    base = getattr(processor, "_base_image_description_options", None)
    if base is not None:
        img_desc = as_int_flag(kwargs.get("img_desc"), 0)
        chart_desc = as_int_flag(kwargs.get("chart_desc"), 0)
        chart_detection = as_int_flag(kwargs.get("chart_detection"), 0)
        image_options = resolve_runtime_image_options(
            base,
            img_desc=img_desc,
            chart_desc=chart_desc,
            chart_detection=chart_detection,
            classification_available=getattr(
                getattr(processor, "pipe_line_options", None), "do_picture_classification", False
            ),
        )
        image = ImageDescriptionEnricher(image_options)
        _log.info(
            "[runtime_feature] image mode enabled=%s img_desc=%s chart_desc=%s detection=%s",
            image_options.enabled, img_desc, chart_desc, image_options.chart_detection,
        )
    table_options = table = None
    tbase = getattr(processor, "_base_table_description_options", None)
    if tbase is not None:
        table_desc = as_int_flag(kwargs.get("table_desc"), 0)
        table_refine = as_int_flag(kwargs.get("table_refine"), 0)
        table_options = resolve_runtime_table_options(
            tbase, table_desc=table_desc, table_refine=table_refine
        )
        table = TableDescriptionEnricher(table_options)
        _log.info(
            "[runtime_feature] table mode enabled=%s table_desc=%s table_refine=%s",
            table_options.enabled, table_desc, table_refine,
        )
    doc_summary_options = summary = None
    dbase = getattr(processor, "_base_doc_summary_options", None)
    if dbase is not None:
        doc_summary_options = resolve_runtime_doc_summary_options(dbase, doc_summary=doc_summary)
        summary = DocSummaryEnricher(doc_summary_options)
        _log.info(
            "[runtime_feature] doc_summary mode enabled=%s doc_summary=%s",
            doc_summary_options.enabled, doc_summary,
        )
    return RequestEnrichers(image_options, image, table_options, table, doc_summary_options, summary)


def request_enricher(kwargs: dict, kind: str):
    """kwargs 에 실린 요청 enricher(image / table / doc_summary). 없으면 None — 호출부가 인스턴스 것으로 폴백."""
    enrichers = (kwargs or {}).get("_enrichers")
    return getattr(enrichers, kind, None) if enrichers is not None else None


def configure_runtime_image_mode(processor, kwargs: dict) -> None:
    """옛 입구(intelligent·convert 파사드용) — 요청 묶음을 만들어 processor 속성에 붙인다.

    공유 인스턴스를 요청마다 덮어쓰므로 동시 요청에 안전하지 않다. core 파서는 쓰지 않는다
    (#481: `build_request_enrichers` + `job.params["_enrichers"]`). HTTP 파사드가 물러나면 지운다.
    """
    e = build_request_enrichers(processor, kwargs)
    if e.image is not None:
        processor.image_description_options = e.image_options
        processor.image_description_enricher = e.image
    if e.table is not None:
        processor.table_description_options = e.table_options
        processor.table_description_enricher = e.table
    if e.doc_summary is not None:
        processor.doc_summary_options = e.doc_summary_options
        processor.doc_summary_enricher = e.doc_summary
