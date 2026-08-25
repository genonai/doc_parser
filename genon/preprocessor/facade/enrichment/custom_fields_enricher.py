import importlib.util
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml
from docling_core.types import DoclingDocument

from docling.utils.llm_cache import async_cached_call, remaining_timeout

from .base_enricher import BaseEnricher
from .field_transforms import store_metadata_in_document
from .prompt_files import read_prompt_file
from .prompt_template import PromptTemplate
from .thinking import resolve_thinking_kwargs, strip_reasoning

_log = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).parent.parent / "configs" / "enrich" / "custom_fields"

# user 가 user_prompt 만 지정한 경우 사용할 built-in default system prompt.
_DEFAULT_CUSTOM_FIELDS_SYSTEM_PROMPT = (
    "너는 문서 정보추출 전문가다. 주어진 문서에서 요청한 필드를 정확하게 추출하라."
)


DOCUMENT_CUSTOM_FIELD_EXTRACTORS = {"llm", "document_llm"}
TABULAR_CUSTOM_FIELD_EXTRACTORS = {"tabular", "tabular_mapping", "column_mapping"}
JSON_CUSTOM_FIELD_EXTRACTORS = {"json_mapping", "json_records"}
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
_NON_ENRICHER_KEYS = ("json", "markdown")


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
        max_tokens: int = 1000,
        temperature: float = 0.0,
        timeout: int = 60,
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
        thinking: str | None = "off",
        thinking_dialect: str = "standard",
        doc_type: str | list[str] | None = None,
        extractor: str = "llm",
    ):
        cfg = self._load_config(config_file, resource_path)
        prompt_cfg = cfg.get("prompt", {}) if isinstance(cfg.get("prompt"), dict) else {}

        # prompt 파일/parser 파일 경로 해석 기준 디렉토리.
        self._parser_base_dir = self._resolve_parser_base_dir(config_file, resource_path)

        self._url = url or cfg.get("url", "")
        self._model = model or cfg.get("model", "")
        self._max_tokens = max_tokens if max_tokens != 1000 else cfg.get("max_tokens", max_tokens)
        self._temperature = temperature if temperature != 0.0 else cfg.get("temperature", temperature)
        self._timeout = timeout if timeout != 60 else cfg.get("timeout", timeout)
        # 우선순위: file > 생성자 kwarg > cfg["*_prompt"] > cfg["prompt"][*] > built-in default
        self._system_prompt = (
            self._maybe_read_prompt(system_prompt_file or cfg.get("system_prompt_file"))
            or system_prompt
            or str(cfg.get("system_prompt") or "").strip()
            or str(prompt_cfg.get("system") or "").strip()
            or _DEFAULT_CUSTOM_FIELDS_SYSTEM_PROMPT
        )
        self._user_prompt = (
            self._maybe_read_prompt(user_prompt_file or cfg.get("user_prompt_file"))
            or user_prompt
            or str(cfg.get("user_prompt") or "").strip()
            or str(prompt_cfg.get("user") or "").strip()
        )
        self._output_fields = list(output_fields or cfg.get("output_fields", []))
        # 문서마다 값이 고정인 필드(관계사 전용 파일의 GROUP_C 등). LLM 에게 상수를 받아쓰게
        # 시키는 대신 여기서 채운다 — 환각·누락 여지가 없고 프롬프트도 짧아진다.
        # tabular_mapping/json_mapping 의 `constants` 와 같은 의미다.
        self._constants = dict(constants or cfg.get("constants") or {})
        self._headers: dict[str, str] = {"Content-Type": "application/json"}
        resolved_key = api_key or cfg.get("api_key", "")
        if resolved_key:
            self._headers["Authorization"] = f"Bearer {resolved_key}"

        self._parser_cfg = parser or cfg.get("parser", {}) or {}
        self._parser_callable = self._build_parser_callable()
        self._extract_pattern: str = self._parser_cfg.get("extract_pattern", "")

        # thinking(추론) 모드. 기본 "off"(차단 토큰 전송). "auto"면 미전송(모델 자동 판단).
        _thinking = thinking if thinking is not None else cfg.get("thinking")
        self._thinking = str(_thinking or "off").strip().lower()
        self._thinking_dialect = str(
            thinking_dialect or cfg.get("thinking_dialect") or "standard"
        ).strip().lower()
        self._doc_types = normalize_doc_types(doc_type)
        self._extractor = str(extractor or "llm").strip().lower()

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

    def _load_config(self, config_file: str, resource_path: str | None = None) -> dict:
        return load_custom_fields_config(config_file, resource_path)

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

    def _render_prompts(self, raw_text: str, document: DoclingDocument | None) -> "tuple[str, str]":
        needed = self._user_tpl.referenced | self._system_tpl.referenced
        if document is not None:
            ctx = PromptTemplate.doc_context(
                document, needed=needed, raw_text=raw_text, **self._variables
            )
        else:
            ctx = {"raw_text": raw_text, **self._variables}
        user = raw_text if self._user_tpl.is_empty else self._user_tpl.render(**ctx)
        system = "" if self._system_tpl.is_empty else self._system_tpl.render(**ctx)
        return system, user

    async def _call_llm(self, raw_text: str, document: DoclingDocument | None = None) -> str:
        system, prompt = self._render_prompts(raw_text, document)
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
        return normalized

    def _extract_raw_text(self, document: DoclingDocument) -> str:
        if not self._pages:
            return document.export_to_text()
        from docling_core.transforms.serializer.markdown import (
            MarkdownDocSerializer,
            MarkdownParams,
        )
        serializer = MarkdownDocSerializer(
            doc=document,
            params=MarkdownParams(pages=set(self._pages)),
        )
        return serializer.serialize().text

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
