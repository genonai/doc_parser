# processing — 파일 단위 역할표

파싱·청킹·보강 처리 라이브러리의 모듈별 역할이다. facade 는 여기를 상속·호출만 한다.
디렉터리 단위 요약은 루트 `CLAUDE.md` 의 "공용 하위 모듈 지도" 절에 있다.

새 로직을 어디 둘지 고민되면 이 표에서 가장 가까운 모듈을 먼저 찾는다
(표는 요약이므로 실제 파일 목록은 `ls` 로 확인한다).

| 모듈 | 역할 |
|---|---|
| `core/parser.py`, `core/chunker.py` | 파싱·청킹 처리 본체(`ParserCore`/`ChunkerCore`). 두 파사드는 상속만 한다 |
| `core/toolbox.py` | 고객이 훅에서 쓰는 기능 재수출(값 변환·엑셀·JSON·표·청크 메타). **새 구현을 두는 자리가 아니다** |
| `core/cli.py`, `core/errors.py` | 파일 단독 실행 진입점(`DocumentProcessor.cli()`), 공용 `GenosServiceException` |
| `common/config_parse.py` | yaml/kwargs 값 파싱, `load_config`, 토크나이저·청크크기·`compact_tables` 등 설정 해석 |
| `common/file_probe.py`, `format_alias.py` | 파일 종류 판별(PDF/텍스트/암호화/HWP 보호), PDF 경로 변환, 확장자 별칭 |
| `common/pdf_convert.py` | 비-PDF 입력의 PDF 변환 진입점. backend chain 순서 + 이슈 #286 사전 체크 |
| `common/docling_ops.py` | docling 배관 — OCR 옵션, 컨버터 생성, 표 이미지 저장, 글리프·빈 텍스트 검사 |
| `common/pipeline_setup.py`, `runtime_kwargs.py` | `__init__` 의 OCR·PDF·layout 해석 / 런타임 kwargs 정규화·이미지 모드 배선 |
| `common/vector_meta.py`, `doc_meta.py` | `GenOSVectorMeta` 빌더 공통 코어, 문서 메타 |
| `common/loaders.py`, `markdown_export.py`, `runtime.py`, `appendix.py` | 로더(`install_packages`, Text/Tabular/Audio), 마크다운 내보내기, 로깅 초기화, 별첨 키워드 판정 |
| `chunking/smart_chunker.py` | `GenosSmartChunker` 본체. 활성 3종이 ClassVar 플래그만 다른 서브클래스로 상속 |
| `chunking/hybrid_chunker.py` | docling_core `HybridChunker`/`HierarchicalChunker` 포크본(`TokenAwareHybridChunker`/`HierarchicalDocChunker` 로 개명해 업스트림과 구분). 갈라진 축은 모듈 docstring 참조 |
| `chunking/table_*.py`, `rich_cells.py` | 표 행 분할·모양 판정·HTML 표 직렬화·변형 처리 |
| `chunking/header_path.py`, `page_split.py`, `doc_prefix.py`, `text_norm.py` | 청크 헤더 경로, 페이지 분할, 문서 접두, 청크 텍스트 정제 |
| `chunking/chunk_quality.py` | 이상 청크 판정(`chunking.validation`). 코어 청커가 초기·최종 검사로 호출한다 |
| `enrichment/`, `guardrail/` | custom_fields·LLM 보강, 민감정보 처리 |
| `converters/` | 입력 전처리 변환기 (`html_flatten`, `json_text`, `md_marker_headings` 등) |
