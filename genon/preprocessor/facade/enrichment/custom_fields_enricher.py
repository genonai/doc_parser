import importlib.util
import json
import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml
from docling_core.types import DoclingDocument
from docling_core.types.doc import DescriptionAnnotation
from docling_core.types.doc.document import MiscAnnotation

from docling.utils.llm_cache import async_cached_call, remaining_timeout

from genon.preprocessor.facade.common import config_parse as cp
from genon.preprocessor.facade.enrichment import config_schema as cs

from .base_enricher import BaseEnricher
from .field_transforms import store_metadata_in_document
from .prompt_files import read_prompt_file
from .prompt_template import PromptTemplate
from .thinking import resolve_thinking_kwargs, strip_reasoning
from .table_description import TABLE_TEXT_DESCRIPTION_PROVENANCE, TableDescriptionExtractor
from .table_text_context import (
    TableTextDescriptionOptions,
    TableTextTarget,
    collect_table_text_targets,
    merge_table_text_description,
    render_table_targets,
)

_log = logging.getLogger(__name__)

# 문자 수를 토큰으로 환산할 때의 안전계수. 한국어 기준 1글자가 1토큰을 넘는 경우를 감안한 상한.
_TOKENS_PER_CHAR = 1.5

_CONFIG_DIR = Path(__file__).parent.parent / "configs" / "enrich" / "custom_fields"

# user 가 user_prompt 만 지정한 경우 사용할 built-in default system prompt.
_DEFAULT_CUSTOM_FIELDS_SYSTEM_PROMPT = (
    "너는 문서 정보추출 전문가다. 주어진 문서에서 요청한 필드를 정확하게 추출하라."
)


# extractor 이름은 종류마다 **하나씩만** 받는다. 예전에는 document_llm/tabular/
# column_mapping/json_records 별칭이 함께 있었는데, 출고 설정 어디에도 쓰이지 않으면서
# "무엇을 써야 하나"라는 질문만 만들었다(설정 개념 수를 줄인다는 원칙).
DOCUMENT_CUSTOM_FIELD_EXTRACTORS = {"llm"}
TABULAR_CUSTOM_FIELD_EXTRACTORS = {"tabular_mapping"}
# json_mapping/json_records(JsonRecordsMapper)와 json_semantic(SemanticJsonMapper)은 서로 다른
# 빌더(json_records.build_json_records_mappers / json_semantic.build_semantic_json_mappers)로
# 컴파일된다 — 두 집합을 나눠 두면 각 빌더가 "내가 처리할 설정"만 자기 필터로 고르므로
# (json_records.py:348,590 / json_semantic.py:524,780), 공개 빌더를 직접 호출해도(또는 다른
# extractor 로 매퍼를 생성해도) 서로의 설정을 침범하지 않는다.
JSON_RECORD_EXTRACTORS = {"json_mapping"}
JSON_SEMANTIC_EXTRACTORS = {"json_semantic"}
JSON_CUSTOM_FIELD_EXTRACTORS = JSON_RECORD_EXTRACTORS | JSON_SEMANTIC_EXTRACTORS
SUPPORTED_CUSTOM_FIELD_EXTRACTORS = (
    DOCUMENT_CUSTOM_FIELD_EXTRACTORS
    | TABULAR_CUSTOM_FIELD_EXTRACTORS
    | JSON_CUSTOM_FIELD_EXTRACTORS
)


def normalize_doc_type(value: Any) -> str:
    """런타임/config 문서유형을 비교 가능한 canonical 문자열로 정규화한다."""
    return str(value or "").strip().lower()


def normalize_doc_types(value: Any) -> tuple[str, ...]:
    """doc_type 설정의 단일 문자열/문자열 목록을 정규화한다."""
    values = value if isinstance(value, (list, tuple, set)) else [value]
    normalized = []
    for item in values:
        doc_type = normalize_doc_type(item)
        if doc_type and doc_type not in normalized:
            normalized.append(doc_type)
    return tuple(normalized)


def matches_doc_type(configured: Any, runtime: Any) -> bool:
    """설정 doc_type이 없으면 wildcard, 있으면 런타임 doc_type과 정확히 매칭한다."""
    configured_types = normalize_doc_types(configured)
    if not configured_types:
        return True
    return normalize_doc_type(runtime) in configured_types


def custom_fields_extractor(config: dict) -> str:
    """custom_fields 추출기 종류. 기존 설정은 llm으로 하위 호환한다."""
    return str((config or {}).get("extractor") or "llm").strip().lower()


def resolve_custom_fields_config_path(
    config_file: str, resource_path: str | None = None
) -> Path:
    """custom_fields 하위 config 경로를 기존 규칙대로 해석한다."""
    cfg_path = Path(config_file)
    if cfg_path.is_absolute():
        return cfg_path
    if cfg_path.suffix in {".yaml", ".yml"} or len(cfg_path.parts) > 1:
        return (Path(resource_path) / cfg_path).resolve() if resource_path else cfg_path
    if resource_path:
        candidate = (Path(resource_path) / f"{config_file}.yaml").resolve()
        if candidate.exists():
            return candidate
    return _CONFIG_DIR / f"{config_file}.yaml"


def load_custom_fields_config(
    config_file: str, resource_path: str | None = None
) -> dict:
    """custom_fields 하위 YAML을 로드한다.

    LLM enricher와 Markdown 전처리가 같은 파일 및 경로 해석 규칙을 공유하도록
    module-level 함수로 둔다.
    """
    if not config_file:
        return {}
    path = resolve_custom_fields_config_path(config_file, resource_path)
    if not path.exists():
        raise FileNotFoundError(f"custom_fields config 없음: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(
            f"custom_fields config는 mapping이어야 합니다: {path} "
            f"({type(loaded).__name__})"
        )
    return loaded


# ── llm_fields (행/레코드 단위 LLM 생성 필드) ────────────────────────────────
# tabular_mapping(Excel 행)과 json_mapping(JSON 레코드)이 공유하는 선언 스펙이다.
# 두 mapper 모두 이 모듈을 import 하므로 여기가 공통 상위 지점이다
# (json_records 는 tabular_custom_fields 를 import 하므로 그쪽에 두면 순환이 된다).
VALID_LLM_ERROR_POLICIES = ("null", "skip_record")

# LlmFieldSpec 자신이 소비하는 키. 나머지는 전부 CustomFieldsEnricher 생성자로 넘어간다
# (_NON_ENRICHER_KEYS 와 같은 발상 — 소비자별로 키를 나눈다).
# output_fields 는 양쪽이 다 쓴다 — 스펙은 실패 시 null 채움에, enricher 는 응답 정규화에.
_LLM_SPEC_ONLY_KEYS = ("input_fields", "concurrency", "on_error")


class LlmFieldSpec:
    """`llm_fields` 항목 — 원천에 없는 필드를 행/레코드마다 LLM 으로 생성하는 선언.

    LLM 연결/프롬프트 설정은 **이 항목에 직접 써도 되고**(`url`·`model`·`system_prompt`·
    `user_prompt` …) `config_file` 로 외부 yaml 을 가리켜도 된다. 아래 `_LLM_SPEC_ONLY_KEYS` 를 뺀
    나머지 키가 그대로 `CustomFieldsEnricher` 생성자로 넘어가므로 두 방식이 같은 코드로 처리된다
    — 설정 하나짜리 유형은 파일을 쪼개지 않고 한 파일로 끝낼 수 있다.
    """

    def __init__(self, cfg: dict):
        if not isinstance(cfg, dict):
            raise ValueError("llm_fields 항목은 object 여야 합니다.")

        self.output_fields = [str(f).strip() for f in (cfg.get("output_fields") or []) if str(f).strip()]
        if not self.output_fields:
            raise ValueError("llm_fields 항목의 output_fields 가 비어 있습니다.")

        self.input_fields = [str(f).strip() for f in (cfg.get("input_fields") or []) if str(f).strip()]
        if not self.input_fields:
            raise ValueError(f"llm_fields{self.output_fields} 의 input_fields 가 비어 있습니다.")

        # 설정을 통째로 빠뜨린 경우를 기동 시에 잡는다. 값이 placeholder 라 실제 호출이 실패하는 건
        # 런타임 경고로 흡수하지만(enricher.is_configured), 연결 정보가 아예 없는 건 오설정이다.
        if not (str(cfg.get("config_file") or "").strip() or str(cfg.get("url") or "").strip()):
            raise ValueError(
                f"llm_fields{self.output_fields} 에는 config_file 또는 url 중 하나가 필요합니다."
            )

        try:
            self.concurrency = max(1, int(cfg.get("concurrency") or 4))
        except (TypeError, ValueError):
            _log.warning("[llm_fields] Invalid llm_fields.concurrency, fallback to 4")
            self.concurrency = 4

        policy = str(cfg.get("on_error") or "null").strip().lower()
        if policy not in VALID_LLM_ERROR_POLICIES:
            _log.warning(f"[llm_fields] Invalid llm_fields.on_error '{policy}', fallback to 'null'")
            policy = "null"
        self.on_error = policy

        # 나머지는 전부 enricher 생성자로. 알 수 없는 키는 거기서 TypeError 로 걸러진다(기동 시 노출).
        self.enricher_kwargs = {k: v for k, v in cfg.items() if k not in _LLM_SPEC_ONLY_KEYS}

    @property
    def label(self) -> str:
        """로그/오류 메시지에서 이 항목을 가리키는 이름(인라인이면 파일명이 없다)."""
        config_file = self.enricher_kwargs.get("config_file")
        return str(config_file) if config_file else ",".join(self.output_fields)

    def build_input_text(self, fields: dict) -> str:
        """프롬프트의 `{{raw_text}}` 로 들어갈 입력 텍스트(선언 순서대로 결합).

        입력이 2개 이상이면 어떤 값이 무엇인지 모델이 알 수 있게 `필드명: 값` 형태로 붙인다.
        """
        labeled = len(self.input_fields) > 1
        parts = []
        for name in self.input_fields:
            value = fields.get(name)
            if value in (None, ""):
                continue
            parts.append(f"{name}: {value}" if labeled else str(value))
        return "\n\n".join(parts)


def build_llm_field_specs(cfg: dict) -> list["LlmFieldSpec"]:
    """config 의 `llm_fields` 목록을 스펙으로 컴파일한다(두 mapper 공통)."""
    return [LlmFieldSpec(item) for item in ((cfg or {}).get("llm_fields") or [])]


# custom_fields 항목에 있지만 이 enricher 가 아니라 다른 단계가 소비하는 키.
# CustomFieldsEnricher 생성자는 **kwargs 를 받지 않으므로, 여기서 제외하지 않으면
# 설정에 이 키를 넣는 순간 TypeError 가 난다.
#   - json: .json 입력에서 본문 텍스트를 꺼낼 key 목록 (parser 의 DocumentProcessor 가 소비)
#   - markdown: .md front matter 분리/선택 규칙 (parser 의 DocumentProcessor 가 소비)
#   - html: .html 마커 heading 승격 규칙 (parser 의 DocumentProcessor 가 소비)
_NON_ENRICHER_KEYS = ("json", "markdown", "html")


def _enricher_kwargs(config: dict) -> dict:
    """설정 dict 에서 CustomFieldsEnricher 생성자에 넘길 키만 남긴다(원본 미변경)."""
    return {k: v for k, v in (config or {}).items() if k not in _NON_ENRICHER_KEYS}


def build_document_custom_fields_enrichers(configs: list[dict]) -> list["CustomFieldsEnricher"]:
    """document/LLM custom_fields 설정만 실제 Docling enricher로 생성한다.

    tabular_mapping(Excel 행 매핑) / json_mapping(JSON 레코드 매핑) 설정은 parser의 조기
    분기에서 별도 handler가 소비한다. 이를 여기서 제외해야 intelligent/convert/chunking
    프로세서 초기화 시 LLM enricher 생성자로 잘못 전달되지 않는다.
    """
    enrichers = []
    for config in configs or []:
        extractor = custom_fields_extractor(config)
        if extractor not in SUPPORTED_CUSTOM_FIELD_EXTRACTORS:
            raise ValueError(f"지원하지 않는 custom_fields extractor: {extractor}")
        if extractor in DOCUMENT_CUSTOM_FIELD_EXTRACTORS:
            enrichers.append(CustomFieldsEnricher(**_enricher_kwargs(config)))
    return enrichers


class CustomFieldsEnricher(BaseEnricher):
    """문서 단위 커스텀 메타데이터 추출 enricher.

    - LLM을 1회 호출해 다중 필드를 추출한다.
    - 파싱 로직은 parser 설정에 따라 외부 파이썬 파일/함수로 위임 가능하다.
    - parser 파일 경로는 config 파일과 동일한 위치(resource_path) 기준으로 해석한다.
    """

    def __init__(
        self,
        api_key: str = "",
        config_file: str = "",
        resource_path: str | None = None,
        url: str = "",
        model: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: int | None = None,
        system_prompt: str = "",
        user_prompt: str = "",
        system_prompt_file: str = "",
        user_prompt_file: str = "",
        output_fields: list[str] | None = None,
        constants: dict | None = None,
        parser: dict | None = None,
        pages: list[int] | None = None,
        variables: dict | None = None,
        template: dict | None = None,
        template_mode: str = "strict",
        thinking: str | None = None,
        thinking_dialect: str | None = None,
        doc_type: str | list[str] | None = None,
        extractor: str = "llm",
        table_text_description: dict | None = None,
    ):
        cfg = self._load_config(config_file, resource_path)
        # 모르는 키는 지금까지 조용히 무시됐다 — `output_field`(오타)처럼 한 글자만 틀려도
        # 그 필드가 결과에서 사라질 뿐 아무 신호가 없었다. 키 소비 전에 대조한다.
        cs.validate_known_keys(
            cfg, label=f"custom_fields({config_file})", extractor=extractor
        )
        prompt_cfg = cfg.get("prompt", {}) if isinstance(cfg.get("prompt"), dict) else {}

        # prompt 파일/parser 파일 경로 해석 기준 디렉토리.
        self._parser_base_dir = self._resolve_parser_base_dir(config_file, resource_path)

        self._url = url or cfg.get("url", "")
        self._model = model or cfg.get("model", "")
        # 생성자 기본값이 None 인 것이 중요하다 — 예전에는 기본값과 같은 값을 등록 블록에
        # **명시**해도 "미지정"과 구분되지 않아 config_file 값이 이겼다(`temperature: 0.0`
        # 이 가장 자주 밟혔다). None 판정으로 바꿔 다른 키와 우선순위를 맞춘다.
        self._max_tokens = max_tokens if max_tokens is not None else cfg.get("max_tokens", 1000)
        self._temperature = (
            temperature if temperature is not None else cfg.get("temperature", 0.0)
        )
        self._timeout = timeout if timeout is not None else cfg.get("timeout", 60)
        # 우선순위: 등록 블록(file > 인라인) > config_file(file > 인라인 > prompt 블록) > 기본값.
        # 등록 블록이 config_file 을 이기는 것은 다른 모든 키와 같다 — 예전에는 config_file 의
        # `*_prompt_file` 이 등록 블록 인라인 프롬프트를 이겨 이 키만 방향이 반대였다.
        self._system_prompt = (
            self._maybe_read_prompt(system_prompt_file)
            or system_prompt
            or self._maybe_read_prompt(cfg.get("system_prompt_file"))
            or str(cfg.get("system_prompt") or "").strip()
            or str(prompt_cfg.get("system") or "").strip()
            or _DEFAULT_CUSTOM_FIELDS_SYSTEM_PROMPT
        )
        self._user_prompt = (
            self._maybe_read_prompt(user_prompt_file)
            or user_prompt
            or self._maybe_read_prompt(cfg.get("user_prompt_file"))
            or str(cfg.get("user_prompt") or "").strip()
            or str(prompt_cfg.get("user") or "").strip()
        )
        self._output_fields = list(output_fields or cfg.get("output_fields", []))
        # 문서마다 값이 고정인 필드(관계사 전용 파일의 GROUP_C 등). LLM 에게 상수를 받아쓰게
        # 시키는 대신 여기서 채운다 — 환각·누락 여지가 없고 프롬프트도 짧아진다.
        # tabular_mapping/json_mapping 의 `constants` 와 같은 의미다.
        # 키 단위 병합이다. 전체 치환이면 등록 블록에 상수 하나만 덧붙여도 config_file 의
        # constants 가 통째로 사라진다 — markdown/html/table_text_description 과 같은 규칙.
        self._constants = {**(cfg.get("constants") or {}), **(constants or {})}
        # 청크 본문(text)과 같은 값을 실을 필드 이름(검색 대상 본문 컬럼). 값 자체는 청커가
        # 청크 단위로 채우므로 여기서는 이름만 문서 metadata 에 실어 넘긴다.
        self._body_fields = cp.parse_field_name_list(cfg.get(cp.BODY_FIELDS_KEY))
        # 청크 본문 앞에 얹을 메타 필드(body_fields 의 반대 방향). 값이 아니라 규칙이라
        # 여기서는 이름만 문서 metadata 에 실어 청커까지 넘긴다.
        #   chunk_prefix_fields: 모든 청크에 반복 / first_chunk_fields: 첫 청크에만 1회
        self._chunk_prefix_fields = cp.parse_field_name_list(cfg.get(cp.CHUNK_PREFIX_FIELDS_KEY))
        self._first_chunk_fields = cp.parse_field_name_list(cfg.get(cp.FIRST_CHUNK_FIELDS_KEY))
        # 위 두 규칙으로 얹힌 값 앞에 붙일 사람이 읽는 항목명. 이름이 있는 필드만 붙는다.
        self._field_labels = cp.parse_field_labels(cfg.get(cp.FIELD_LABELS_KEY))
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        resolved_key = api_key or cfg.get("api_key", "")
        if resolved_key:
            self._headers["Authorization"] = f"Bearer {resolved_key}"

        self._parser_cfg = parser or cfg.get("parser", {}) or {}
        self._parser_callable = self._build_parser_callable()
        self._extract_pattern: str = self._parser_cfg.get("extract_pattern", "")

        # thinking(추론) 모드. 기본 "off"(차단 토큰 전송). "auto"면 미전송(모델 자동 판단).
        # 생성자 기본값을 None 으로 두는 것이 중요하다 — 다른 키와 같은 우선순위
        # (등록 블록 > config_file > 기본값)를 갖는다. 예전에는 기본값이 truthy 라
        # cfg 까지 도달하지 못해 문서유형 yaml 의 값이 조용히 무시됐다.
        self._thinking = str(
            thinking if thinking is not None else cfg.get("thinking") or "off"
        ).strip().lower()
        self._thinking_dialect = str(
            thinking_dialect
            if thinking_dialect is not None
            else cfg.get("thinking_dialect") or "standard"
        ).strip().lower()
        self._doc_types = normalize_doc_types(doc_type)
        self._extractor = str(extractor or "llm").strip().lower()
        self._table_description_options = self._resolve_table_text_description_options(
            merge_table_text_description(table_text_description, cfg.get("table_text_description"))
        )

        cfg_pages = cfg.get("pages")
        self._pages: list[int] | None = pages or (cfg_pages if isinstance(cfg_pages, list) and cfg_pages else None)

        # 변수 치환 템플릿. user-defined 변수는 reserved 와 함께 strict 검증에 허용된다.
        self._variables = dict(variables or cfg.get("variables") or {})
        _tmpl_cfg = template if isinstance(template, dict) else cfg.get("template")
        _mode = (_tmpl_cfg.get("mode") if isinstance(_tmpl_cfg, dict) else None) or template_mode or "strict"
        _allowed = set(self._variables.keys())
        self._system_tpl = PromptTemplate(self._system_prompt, mode=_mode, allowed_names=_allowed)
        self._user_tpl = PromptTemplate(self._user_prompt, mode=_mode, allowed_names=_allowed)

    def _maybe_read_prompt(self, file_ref: Any) -> str:
        """prompt 파일 경로가 지정된 경우 읽어서 반환, 없으면 빈 문자열."""
        if isinstance(file_ref, str) and file_ref.strip():
            return read_prompt_file(file_ref.strip(), self._parser_base_dir)
        return ""

    def _resolve_table_text_description_options(
        self, table_cfg: dict | None
    ) -> TableTextDescriptionOptions:
        """텍스트 표 설명 프롬프트는 custom_fields와 동일하게 MD 파일 우선으로 해석한다."""
        cfg = table_cfg if isinstance(table_cfg, dict) else {}
        prompt_file = cfg.get("prompt_template_file") or cfg.get("prompt_file")
        prompt = self._maybe_read_prompt(prompt_file) or str(
            cfg.get("prompt_template") or cfg.get("prompt") or ""
        ).strip()
        return replace(
            TableTextDescriptionOptions.from_config(cfg), prompt_template=prompt
        )

    def _load_config(self, config_file: str, resource_path: str | None = None) -> dict:
        loaded = load_custom_fields_config(config_file, resource_path)
        # `schema: v2` 면 내부(v1) 형태로 번역해 넘긴다 — 아래 코드는 v1/v2 를 구분하지 않는다.
        from . import config_v2 as cv2

        normalized, _ = cv2.load(loaded, label=f"custom_fields({config_file})")
        return normalized

    @staticmethod
    def _resolve_parser_base_dir(config_file: str, resource_path: str | None) -> Path:
        if config_file:
            cfg_path = Path(config_file)
            if cfg_path.is_absolute():
                return cfg_path.resolve().parent
            if cfg_path.suffix in {".yaml", ".yml"} or len(cfg_path.parts) > 1:
                if resource_path:
                    return (Path(resource_path) / cfg_path).resolve().parent
                return cfg_path.resolve().parent
        if resource_path:
            return Path(resource_path).resolve()
        return Path.cwd().resolve()

    def _build_parser_callable(self) -> Callable[..., dict]:
        parser_type = str(self._parser_cfg.get("type", "json")).strip().lower()
        if parser_type == "python":
            return self._load_external_parser(self._parser_cfg)
        return self._default_parse

    def _load_external_parser(self, parser_cfg: dict) -> Callable[..., dict]:
        parser_file = parser_cfg.get("file")
        parser_callable = parser_cfg.get("callable", "parse")
        if not parser_file:
            raise ValueError("custom_fields parser.type=python 인 경우 parser.file 값이 필요합니다.")

        parser_path = (self._parser_base_dir / parser_file).resolve()
        try:
            parser_path.relative_to(self._parser_base_dir)
        except ValueError as exc:
            raise ValueError(
                f"parser 경로가 허용 범위를 벗어났습니다: {parser_path} (base: {self._parser_base_dir})"
            ) from exc

        if not parser_path.exists():
            raise FileNotFoundError(f"parser 파일이 없습니다: {parser_path}")

        module_name = f"custom_fields_parser_{abs(hash(str(parser_path)))}"
        spec = importlib.util.spec_from_file_location(module_name, parser_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"parser 모듈 로딩 실패: {parser_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        fn = getattr(module, parser_callable, None)
        if not callable(fn):
            raise TypeError(f"parser callable을 찾을 수 없거나 호출 불가: {parser_callable}")
        return fn

    def _extract_text_for_json(self, text: str) -> str:
        if not self._extract_pattern:
            return text
        m = re.search(self._extract_pattern, text, re.DOTALL)
        if not m:
            return text
        return m.group(1) if m.lastindex else m.group(0)

    def _default_parse(self, llm_output: str, **kwargs) -> dict:
        if isinstance(llm_output, dict):
            return llm_output
        if not isinstance(llm_output, str):
            return {}

        # extract_pattern 지정 시 먼저 적용
        if self._extract_pattern:
            candidate = self._extract_text_for_json(llm_output)
            try:
                parsed = json.loads(candidate)
                return parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                pass
            return {}

        # 자동 fallback — 3단계
        # 1단계: 직접 파싱
        try:
            parsed = json.loads(llm_output)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            pass

        # 2단계: 마크다운 코드블록
        for block in re.findall(r"```(?:json)?\s*([\s\S]*?)```", llm_output):
            try:
                parsed = json.loads(block.strip())
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue

        # 3단계: raw_decode 스캔 — 설명문구 앞뒤에 JSON이 섞인 경우
        decoder = json.JSONDecoder()
        for i, ch in enumerate(llm_output):
            if ch in "{[":
                try:
                    parsed, _ = decoder.raw_decode(llm_output, i)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue

        return {}

    @staticmethod
    def _normalize_message_content(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict):
                    if "text" in item and isinstance(item["text"], str):
                        chunks.append(item["text"])
                elif isinstance(item, str):
                    chunks.append(item)
            return "\n".join(chunks).strip()
        return str(content)

    def _render_prompts(
        self,
        raw_text: str,
        document: DoclingDocument | None,
        user_suffix: str = "",
    ) -> "tuple[str, str]":
        needed = self._user_tpl.referenced | self._system_tpl.referenced
        if document is not None:
            ctx = PromptTemplate.doc_context(
                document, needed=needed, raw_text=raw_text, **self._variables
            )
        else:
            ctx = {"raw_text": raw_text, **self._variables}
        user = raw_text if self._user_tpl.is_empty else self._user_tpl.render(**ctx)
        if user_suffix:
            user = f"{user}\n\n{user_suffix}" if user else user_suffix
        system = "" if self._system_tpl.is_empty else self._system_tpl.render(**ctx)
        return system, user

    async def _call_llm(
        self,
        raw_text: str,
        document: DoclingDocument | None = None,
        user_suffix: str = "",
    ) -> str:
        system, prompt = self._render_prompts(raw_text, document, user_suffix)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
            "messages": messages,
        }
        ctk = resolve_thinking_kwargs(self._thinking, self._thinking_dialect)
        if ctk:
            payload["chat_template_kwargs"] = ctk

        async def _produce() -> str:
            # #329: llm_cache opt-in 시 캐시 경유. 미사용 시 기존과 동일.
            async with httpx.AsyncClient(timeout=httpx.Timeout(remaining_timeout(self._timeout))) as client:
                resp = await client.post(self._url, json=payload, headers=self._headers)
                resp.raise_for_status()
                data = resp.json()
                message = data["choices"][0]["message"]
                content = strip_reasoning(message)
                return self._normalize_message_content(content)

        return await async_cached_call(self._url, payload, _produce)

    def _parse_with_custom_parser(
        self, llm_output: str, document: DoclingDocument | None, **kwargs
    ) -> dict:
        try:
            parsed = self._parser_callable(
                llm_output,
                output_fields=self._output_fields,
                parser_config=self._parser_cfg,
                document=document,
                **kwargs,
            )
        except TypeError:
            parsed = self._parser_callable(llm_output)
        if not isinstance(parsed, dict):
            raise TypeError("custom_fields parser 결과는 dict 이어야 합니다.")
        return parsed

    def _normalize_output_fields(
        self, parsed: dict, structured_fields: dict | None = None
    ) -> dict:
        normalized = (
            parsed if not self._output_fields
            else {key: parsed.get(key) for key in self._output_fields}
        )
        # YAML front matter처럼 이미 구조화된 원천값은 LLM 추론값보다 신뢰도가 높다.
        # output_fields 밖의 필드도 사용자가 metadata_fields로 명시한 것이므로 보존한다.
        if structured_fields:
            overridden = sorted(
                key for key, value in structured_fields.items()
                if key in normalized
                and normalized.get(key) not in (None, "")
                and value != normalized.get(key)
            )
            if overridden:
                _log.warning(
                    "custom_fields 구조화 원천값이 LLM 결과를 덮어씁니다: %s", overridden
                )
            normalized = {**normalized, **structured_fields}
        # 상수는 LLM 응답보다 우선한다 — 고정값이라고 선언한 이상 모델이 뭘 내놓든 그 값이다.
        # front matter보다도 우선하며, output_fields 에 없는 상수도 그대로 실린다.
        if self._constants:
            normalized = {**normalized, **self._constants}
        # 값이 아니라 규칙이다. 청커가 읽어 청크 본문으로 채우고 청크 필드에서는 뺀다.
        if self._body_fields:
            normalized = {**normalized, cp.BODY_FIELDS_KEY: list(self._body_fields)}
        # 같은 성격의 규칙 — 청커가 읽어 청크 본문 앞에 해당 필드 값을 얹는다.
        if self._chunk_prefix_fields:
            normalized = {
                **normalized, cp.CHUNK_PREFIX_FIELDS_KEY: list(self._chunk_prefix_fields)}
        if self._first_chunk_fields:
            normalized = {
                **normalized, cp.FIRST_CHUNK_FIELDS_KEY: list(self._first_chunk_fields)}
        if self._field_labels:
            normalized = {**normalized, cp.FIELD_LABELS_KEY: dict(self._field_labels)}
        return normalized

    def _extract_raw_text(self, document: DoclingDocument) -> str:
        if not self._pages:
            return document.export_to_text()
        from genon.preprocessor.facade.common.markdown_export import export_markdown

        return export_markdown(document, pages=set(self._pages))

    def wants_table_descriptions(self, **kwargs: Any) -> bool:
        """현재 요청에서 텍스트 표 설명 기능이 켜졌는지 반환한다.

        런타임 플래그(table_text_desc)로도 켤 수 있지만, 프롬프트가 없으면 켜지 않는다 —
        전역 플래그 하나로 프롬프트 미설정 문서유형의 custom_fields 추출까지 실패시키지 않기 위해서다.

        `table_text_description` 이 자체 LLM 연결로 이미 표 설명을 만든 요청이면 융합하지
        않는다(`_table_text_desc_owned`) — 같은 표를 두 번 설명하는 것을 막는다.
        """
        if kwargs.get("_table_text_desc_owned"):
            return False
        if not self.is_configured or not matches_doc_type(self._doc_types, kwargs.get("doc_type")):
            return False
        if self._table_description_options.conflict_policy == "prefer_image":
            return False
        runtime = kwargs.get("table_text_desc")
        if runtime is None:
            wanted = self._table_description_options.enabled
        elif isinstance(runtime, str):
            wanted = runtime.strip().lower() in {"1", "true", "yes", "on"}
        else:
            wanted = bool(runtime)
        if wanted and not self._table_description_options.prompt_template:
            _log.warning(
                "표 설명을 요청했지만 table_text_description.prompt_template_file 설정이 없어 건너뜁니다."
            )
            return False
        return wanted

    @property
    def table_description_conflict_policy(self) -> str:
        return self._table_description_options.conflict_policy

    def _table_prompt(self, targets: list[TableTextTarget]) -> str:
        options = self._table_description_options
        limits = (
            f"retrieval_context 최대 {options.retrieval_context_max_chars}자, "
            f"key_facts 최대 {options.key_fact_limit}개(항목당 {options.key_fact_max_chars}자), "
            f"search_terms 최대 {options.search_terms_limit}개."
        )
        return "\n\n".join((
            options.prompt_template,
            limits,
            render_table_targets(targets),
        ))

    def _prompt_fits(
        self, raw_text: str, document: DoclingDocument, suffix: str
    ) -> bool:
        """토크나이저 없이 문자 수로 토큰을 추정한다.

        한국어는 한 글자가 1토큰을 넘는 경우가 흔하므로 문자 수를 그대로 토큰으로 보면
        과소 추정이 된다. _TOKENS_PER_CHAR 안전계수를 곱해 넘치는 쪽으로 판정한다.
        """
        system, user = self._render_prompts(raw_text, document, suffix)
        available = max(
            1,
            self._table_description_options.max_context_tokens
            - self._table_description_options.completion_reserved_tokens,
        )
        estimated = (len(system) + len(user)) * _TOKENS_PER_CHAR
        return estimated <= available

    @staticmethod
    def _clean_string_list(value: Any, limit: int, max_chars: int | None = None) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
            if not text or text in result:
                continue
            result.append(text[:max_chars] if max_chars else text)
            if len(result) >= limit:
                break
        return result

    def _attach_table_descriptions(
        self, parsed: dict, targets: list[TableTextTarget]
    ) -> None:
        values = parsed.pop("_table_descriptions", None)
        if not isinstance(values, list):
            _log.warning("custom_fields 표 설명 응답에 _table_descriptions 배열이 없습니다.")
            return
        by_id = {
            str(value.get("table_id") or ""): value
            for value in values
            if isinstance(value, dict)
        }
        options = self._table_description_options
        for target in targets:
            value = by_id.get(target.table_id)
            if not value:
                _log.warning("custom_fields 표 설명 누락: %s", target.table_id)
                continue
            context = re.sub(r"\s+", " ", str(value.get("retrieval_context") or "")).strip()
            context = context[:options.retrieval_context_max_chars]
            facts = self._clean_string_list(
                value.get("key_facts"), options.key_fact_limit, options.key_fact_max_chars
            )
            terms = self._clean_string_list(value.get("search_terms"), options.search_terms_limit)
            if not context:
                _log.warning("custom_fields 표 retrieval_context 누락: %s", target.table_id)
                continue
            description = context
            if facts:
                description += "\n핵심 사실: " + " | ".join(facts)
            if options.include_search_terms and terms:
                description += "\n검색어: " + ", ".join(terms)
            item = target.table_item
            # 재실행 시 같은 표에 설명이 겹치지 않도록 이전 텍스트 설명을 먼저 뗀다.
            item.annotations = TableDescriptionExtractor.strip_text_descriptions(
                getattr(item, "annotations", None)
            )
            item.annotations.append(DescriptionAnnotation(
                text=description, provenance=TABLE_TEXT_DESCRIPTION_PROVENANCE
            ))
            item.annotations.append(MiscAnnotation(content={
                "provenance": TABLE_TEXT_DESCRIPTION_PROVENANCE,
                "table_retrieval": {
                    "retrieval_context": context,
                    "key_facts": facts,
                    "search_terms": terms,
                    "include_search_terms": options.include_search_terms,
                    "repeat_context_on_split": options.repeat_context_on_split,
                },
            }))

    def _fit_targets(
        self, raw_text: str, document: DoclingDocument, targets: list[TableTextTarget]
    ) -> tuple[list[TableTextTarget], bool]:
        """예산 안에 들어가도록 target 을 단계적으로 줄인다.

        계획한 순서 그대로 (1) 있는 그대로 (2) 주변 문맥 축소 (3) HTML 을 markdown 으로 낮춤
        을 시도하고, 어느 단계에서 들어갔는지를 (targets, 통과여부) 로 돌려준다.
        """
        options = self._table_description_options
        if self._prompt_fits(raw_text, document, self._table_prompt(targets)):
            return targets, True

        shrunk = collect_table_text_targets(
            document, options, max_context_chars=max(1, options.max_context_chars // 4)
        )
        if shrunk and self._prompt_fits(raw_text, document, self._table_prompt(shrunk)):
            _log.info("표 설명 프롬프트가 예산을 넘어 주변 문맥을 축소했습니다.")
            return shrunk, True

        if any(target.input_format == "html" for target in (shrunk or targets)):
            downgraded = collect_table_text_targets(
                document, options, input_format="markdown",
                max_context_chars=max(1, options.max_context_chars // 4),
            )
            if downgraded and self._prompt_fits(raw_text, document, self._table_prompt(downgraded)):
                _log.info("표 설명 프롬프트가 예산을 넘어 표 본문을 markdown 으로 낮췄습니다.")
                return downgraded, True
            return downgraded or shrunk or targets, False
        return shrunk or targets, False

    def _plan_batches(
        self, document: DoclingDocument, targets: list[TableTextTarget]
    ) -> list[list[TableTextTarget]]:
        """표만 담는 추가 호출을 예산 안에서 최소 개수로 묶는다.

        표 하나만으로도 예산을 넘으면 그 표는 어떤 호출에도 담을 수 없으므로 제외한다
        (overflow_policy=error 는 호출부에서 예외로 바꾼다).
        """
        batches: list[list[TableTextTarget]] = []
        batch: list[TableTextTarget] = []
        for target in targets:
            if not self._prompt_fits("", document, self._table_prompt([target])):
                _log.warning("표 하나가 프롬프트 예산을 넘어 설명을 생략합니다: %s", target.table_id)
                continue
            candidate = batch + [target]
            if batch and not self._prompt_fits("", document, self._table_prompt(candidate)):
                batches.append(batch)
                batch = [target]
            else:
                batch = candidate
        if batch:
            batches.append(batch)
        return batches

    async def _extract_with_table_descriptions(
        self,
        raw_text: str,
        document: DoclingDocument,
        targets: list[TableTextTarget],
        **kwargs: Any,
    ) -> dict:
        """가능하면 custom fields와 모든 표 설명을 단일 호출로 추출한다.

        표 설명은 부가 기능이므로 이 경로의 어떤 실패도 custom fields 결과를 버리지 않는다.
        """
        policy = self._table_description_options.overflow_policy
        try:
            fitted, fits = self._fit_targets(raw_text, document, targets)
        except Exception as exc:
            _log.warning("표 설명 입력 구성 실패: %s", exc)
            fitted, fits = targets, False

        if fits:
            output = await self._call_llm(raw_text, document, self._table_prompt(fitted))
            parsed = self._parse_with_custom_parser(output, document, **kwargs)
            try:
                self._attach_table_descriptions(parsed, fitted)
            except Exception as exc:
                _log.warning("표 설명 부착 실패: %s", exc)
                parsed.pop("_table_descriptions", None)
            return parsed

        if policy == "error":
            raise ValueError("custom_fields + 표 설명 프롬프트가 max_context_tokens를 초과했습니다.")
        output = await self._call_llm(raw_text, document)
        parsed = self._parse_with_custom_parser(output, document, **kwargs)
        if policy == "skip":
            _log.warning("표 설명 프롬프트가 한도를 초과해 표 설명을 건너뜁니다.")
            return parsed

        # batch 정책: custom fields는 한 번만 호출하고 표는 들어가는 만큼 묶어 추가 호출한다.
        try:
            batches = self._plan_batches(document, fitted)
            for table_batch in batches:
                table_output = await self._call_llm("", document, self._table_prompt(table_batch))
                table_parsed = self._parse_with_custom_parser(table_output, document, **kwargs)
                self._attach_table_descriptions(table_parsed, table_batch)
        except Exception as exc:
            # 이미 추출한 custom fields 는 유지한다 — 표 설명만 없는 상태로 진행.
            _log.warning("표 설명 배치 호출 실패: %s", exc)
        return parsed

    async def describe_table_targets(
        self,
        targets: list[TableTextTarget],
        *,
        document: DoclingDocument | None = None,
        **kwargs: Any,
    ) -> dict:
        """target 별 표 설명 응답을 table_id 기준으로 모아 반환한다.

        `document` 를 선택 인자로 둔 이유는 docling 문서가 없는 레코드 경로(json/tabular
        매핑)도 같은 프롬프트·예산·배치 규칙을 그대로 쓰게 하기 위해서다 — 문서가 없으면
        프롬프트 문맥 변수만 비고 나머지 계산은 동일하다.

        예산 초과는 `_plan_batches` 가 호출을 나눠 흡수한다. 표 하나가 통째로 예산을 넘으면
        그 표만 빠진다(경고 후 계속).
        """
        results: dict = {}
        if not targets:
            return results
        for batch in self._plan_batches(document, targets):
            output = await self._call_llm("", document, self._table_prompt(batch))
            parsed = self._parse_with_custom_parser(output, document, **kwargs)
            values = parsed.get("_table_descriptions")
            if not isinstance(values, list):
                _log.warning("표 설명 응답에 _table_descriptions 배열이 없습니다.")
                continue
            for value in values:
                table_id = str(value.get("table_id") or "") if isinstance(value, dict) else ""
                if table_id:
                    results[table_id] = value
        return results

    async def describe_tables_only(
        self, document: DoclingDocument, **kwargs: Any
    ) -> None:
        """custom_fields 추출 없이 표 설명만 만들어 문서에 부착한다.

        문서형 custom_fields 가 없거나 그 연결을 빌려 쓸 수 없는 문서를 위해
        `TableTextDescriptionEnricher` 가 부르는 진입점이다. 융합 경로
        (`_extract_with_table_descriptions`)와 같은 헬퍼를 쓰되, custom_fields 본문을
        비워 두고 표만 싣는다 — 그래서 융합 경로의 "custom fields 1회 + 표 배치" 대신
        표 배치만 호출한다(본문이 없으니 그 1회가 순수 낭비다).
        """
        targets = collect_table_text_targets(document, self._table_description_options)
        if not targets:
            return
        fitted, fits = self._fit_targets("", document, targets)
        if fits:
            output = await self._call_llm("", document, self._table_prompt(fitted))
            parsed = self._parse_with_custom_parser(output, document, **kwargs)
            self._attach_table_descriptions(parsed, fitted)
            return

        policy = self._table_description_options.overflow_policy
        if policy == "error":
            raise ValueError("표 설명 프롬프트가 max_context_tokens 를 초과했습니다.")
        if policy == "skip":
            _log.warning("표 설명 프롬프트가 한도를 초과해 표 설명을 건너뜁니다.")
            return
        for table_batch in self._plan_batches(document, fitted):
            output = await self._call_llm("", document, self._table_prompt(table_batch))
            parsed = self._parse_with_custom_parser(output, document, **kwargs)
            self._attach_table_descriptions(parsed, table_batch)

    @property
    def is_configured(self) -> bool:
        """LLM 연결 설정이 채워져 있는지. 비어 있으면 호출 자체를 하지 않는다."""
        return bool(self._url and self._model)

    async def extract_fields_from_text(self, raw_text: str) -> dict:
        """DoclingDocument 없이 원문 텍스트만으로 필드를 추출한다(레코드 단위 호출용).

        `enrich` 는 문서 단위 진입점이라 문서에서 raw_text 를 뽑고 결과를 문서에 저장하지만,
        JSON 레코드 매핑(json_records)은 문서가 없고 레코드마다 호출한다. 프롬프트 템플릿/
        thinking dialect/llm_cache/응답 파싱은 모두 같은 경로를 그대로 쓴다.
        """
        llm_output = await self._call_llm(raw_text, None)
        parsed = self._parse_with_custom_parser(llm_output, None)
        return self._normalize_output_fields(parsed)

    async def enrich(self, document: DoclingDocument, **kwargs) -> DoclingDocument:
        if not matches_doc_type(self._doc_types, kwargs.get("doc_type")):
            return document

        raw_text = self._extract_raw_text(document)
        front_matter = kwargs.get("_markdown_front_matter")
        if not isinstance(front_matter, dict):
            front_matter = {}
        structured_fields = front_matter.get("metadata")
        if not isinstance(structured_fields, dict):
            structured_fields = {}
        prompt_prefix = str(front_matter.get("prompt_prefix") or "").strip()

        # LLM 도 없고 LLM 과 무관한 산출물(front matter/constants)도 없으면 예전처럼 아무것도
        # 하지 않는다. 이 가드가 없으면 url 이 빈 설정에서도 output_fields 가 전부 null 로
        # 저장돼 모든 청크에 빈 property 가 생긴다.
        if not (self.is_configured or structured_fields or self._constants):
            _log.warning("custom_fields enricher 비활성: url/model 설정이 비어있습니다.")
            return document

        if prompt_prefix:
            # 청크 텍스트에서 제외한 front matter 도 custom_fields 추출에는 계속 제공한다
            # (product_slf 의 PRODUCT_C 처럼 근거가 front matter 에만 있는 필드가 있다).
            raw_text = f"{prompt_prefix}\n\n{raw_text}" if raw_text else prompt_prefix

        parsed: dict = {}
        if self.is_configured:
            try:
                targets = (
                    collect_table_text_targets(document, self._table_description_options)
                    if self.wants_table_descriptions(**kwargs)
                    else []
                )
                if targets:
                    parsed = await self._extract_with_table_descriptions(
                        raw_text, document, targets, **kwargs
                    )
                else:
                    llm_output = await self._call_llm(raw_text, document)
                    parsed = self._parse_with_custom_parser(llm_output, document, **kwargs)
            except Exception as e:
                _log.warning(f"custom_fields 추출 실패: {e}")
        else:
            # LLM 연결과 무관한 constants/front matter 는 계속 산출해야 한다.
            _log.warning(
                "custom_fields LLM 비활성: url/model 설정이 비어있어 LLM 필드는 null 로 둡니다."
            )

        normalized = self._normalize_output_fields(parsed, structured_fields)

        # 문서에 저장 → 별도 chunk API 경계를 넘어 청커 passthrough 가 각 청크에 부착
        # (created_date/MetadataEnricher 와 동일 경로). 선언된 output_fields 는 값이 null 이어도
        # 결과에 모두 남겨야 하므로 preserve_nulls=True(sentinel 왕복)로 저장한다.
        # typed_keys 는 front matter 유래 키로 한정한다 — 전 필드에 적용하면 기존 doc_type 의
        # 청크 property 타입까지 바뀐다(field_transforms.store_metadata_in_document 주석 참고).
        store_metadata_in_document(
            document, normalized, preserve_nulls=True, typed_keys=set(structured_fields)
        )

        context = kwargs.get("_enrichment_context")
        if isinstance(context, dict):
            context.setdefault("metadata", {}).update(normalized)

        return document
