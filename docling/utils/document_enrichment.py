import json
import logging
import re
import difflib
from typing import Dict, Any, Optional
from copy import deepcopy

from docling_core.types.doc import (
    DocItemLabel, SectionHeaderItem, TextItem, DoclingDocument
)
from docling_core.types.doc.document import (
    GraphData, GraphCell, GraphCellLabel
)
from docling.prompts import PromptManager
from docling.datamodel.pipeline_options import DataEnrichmentOptions

_log = logging.getLogger(__name__)


class DocumentEnrichmentUtils:
    """문서 enrichment 유틸리티 클래스 - TOC 추출 및 메타데이터 추출 기능 제공"""
    
    def __init__(self, enrichment_options: DataEnrichmentOptions):
        """
        Args:
            enrichment_options: 데이터 enrichment 옵션
        """
        self.enrichment_options = enrichment_options
        self.prompt_manager = None
        
        # 개별 기능이 하나라도 활성화되어 있으면 프롬프트 매니저 초기화
        if enrichment_options.do_toc_enrichment or enrichment_options.extract_metadata:
            self._initialize_prompt_manager()
    
    def _initialize_prompt_manager(self):
        """프롬프트 매니저 초기화"""
        custom_prompts = self._build_custom_prompts()
        custom_api_configs = self._build_custom_api_configs()
        
        self.prompt_manager = PromptManager(
            custom_prompts=custom_prompts,
            custom_api_configs=custom_api_configs
        )
    
    def _build_custom_prompts(self) -> Dict[str, Any]:
        """사용자 정의 프롬프트 딕셔너리 구성"""
        custom_prompts = {}
        
        # TOC 관련 사용자 정의 프롬프트
        if (self.enrichment_options.toc_system_prompt or 
            self.enrichment_options.toc_user_prompt):
            if "toc_extraction" not in custom_prompts:
                custom_prompts["toc_extraction"] = {}
            if "korean_document" not in custom_prompts["toc_extraction"]:
                custom_prompts["toc_extraction"]["korean_document"] = {}
            
            if self.enrichment_options.toc_system_prompt:
                custom_prompts["toc_extraction"]["korean_document"]["system"] = self.enrichment_options.toc_system_prompt
            
            if self.enrichment_options.toc_user_prompt:
                custom_prompts["toc_extraction"]["korean_document"]["user"] = self.enrichment_options.toc_user_prompt
        
        # 메타데이터 관련 사용자 정의 프롬프트
        if (self.enrichment_options.metadata_system_prompt or 
            self.enrichment_options.metadata_user_prompt):
            if "metadata_extraction" not in custom_prompts:
                custom_prompts["metadata_extraction"] = {}
            if "korean_financial" not in custom_prompts["metadata_extraction"]:
                custom_prompts["metadata_extraction"]["korean_financial"] = {}
            
            if self.enrichment_options.metadata_system_prompt:
                custom_prompts["metadata_extraction"]["korean_financial"]["system"] = self.enrichment_options.metadata_system_prompt
            
            if self.enrichment_options.metadata_user_prompt:
                custom_prompts["metadata_extraction"]["korean_financial"]["user"] = self.enrichment_options.metadata_user_prompt
        
        return custom_prompts
    
    def _build_custom_api_configs(self) -> Dict[str, Dict[str, Any]]:
        """카테고리별 사용자 정의 API 설정 딕셔너리 구성"""
        custom_api_configs = {}
        
        # TOC API 설정
        if (self.enrichment_options.toc_api_provider or 
            self.enrichment_options.toc_api_key or 
            self.enrichment_options.toc_api_base_url or 
            self.enrichment_options.toc_model):
            
            toc_config = {}
            toc_config["provider"] = self.enrichment_options.toc_api_provider or "openrouter"
            toc_config["api_base_url"] = self.enrichment_options.toc_api_base_url or "https://openrouter.ai/api/v1"
            toc_config["model"] = self.enrichment_options.toc_model or "google/gemma-3-27b-it:free"
            
            if self.enrichment_options.toc_api_key:
                toc_config["api_key"] = self.enrichment_options.toc_api_key
            
            # TOC 선택적 파라미터들
            if self.enrichment_options.toc_temperature is not None:
                toc_config["temperature"] = self.enrichment_options.toc_temperature
            if self.enrichment_options.toc_top_p is not None:
                toc_config["top_p"] = self.enrichment_options.toc_top_p
            if self.enrichment_options.toc_seed is not None:
                toc_config["seed"] = self.enrichment_options.toc_seed
            if self.enrichment_options.toc_max_tokens is not None:
                toc_config["max_tokens"] = self.enrichment_options.toc_max_tokens
            
            custom_api_configs["toc_extraction"] = toc_config
        
        # Metadata API 설정
        if (self.enrichment_options.metadata_api_provider or 
            self.enrichment_options.metadata_api_key or 
            self.enrichment_options.metadata_api_base_url or 
            self.enrichment_options.metadata_model):
            
            metadata_config = {}
            metadata_config["provider"] = self.enrichment_options.metadata_api_provider or "openrouter"
            metadata_config["api_base_url"] = self.enrichment_options.metadata_api_base_url or "https://openrouter.ai/api/v1"
            metadata_config["model"] = self.enrichment_options.metadata_model or "google/gemma-3-27b-it:free"
            
            if self.enrichment_options.metadata_api_key:
                metadata_config["api_key"] = self.enrichment_options.metadata_api_key
            
            # Metadata 선택적 파라미터들
            if self.enrichment_options.metadata_temperature is not None:
                metadata_config["temperature"] = self.enrichment_options.metadata_temperature
            if self.enrichment_options.metadata_top_p is not None:
                metadata_config["top_p"] = self.enrichment_options.metadata_top_p
            if self.enrichment_options.metadata_seed is not None:
                metadata_config["seed"] = self.enrichment_options.metadata_seed
            if self.enrichment_options.metadata_max_tokens is not None:
                metadata_config["max_tokens"] = self.enrichment_options.metadata_max_tokens
            
            custom_api_configs["metadata_extraction"] = metadata_config
        
        return custom_api_configs
    
    def apply_toc_enrichment(self, document: DoclingDocument) -> int:
        """
        문서에 TOC enrichment 적용
        
        Args:
            document: DoclingDocument 객체
            
        Returns:
            int: 생성된 섹션 헤더 개수
        """
        if not self.enrichment_options.do_toc_enrichment or not self.prompt_manager:
            return 0
        
        try:
            _log.info("TOC 추출 시작...")
            
            # 모든 SectionHeaderItem을 TextItem으로 변환
            self._convert_section_headers_to_text(document)
            
            # 원시 텍스트 추출
            raw_text = self._extract_raw_text_for_toc(document)
            
            # 사용자 정의 프롬프트 가져오기
            custom_system = self.enrichment_options.toc_system_prompt
            custom_user = self.enrichment_options.toc_user_prompt
            
            # AI로 목차 생성
            toc_content = self.prompt_manager.call_ai_model(
                category="toc_extraction",
                prompt_type="korean_document",
                custom_system=custom_system,
                custom_user=custom_user,
                raw_text=raw_text
            )

            if toc_content:
                # 목차를 기반으로 SectionHeader 적용
                matched_count = self._apply_toc_to_document(document, toc_content)
                _log.info(f"TOC 추출 완료 - {matched_count}개 섹션 헤더 생성")
                return matched_count
            else:
                _log.warning("TOC 생성 실패")
                return 0
                
        except Exception as e:
            _log.error(f"TOC 추출 중 오류 발생: {str(e)}")
            return 0
    
    def apply_metadata_enrichment(self, document: DoclingDocument) -> bool:
        """
        문서에 메타데이터 enrichment 적용
        
        Args:
            document: DoclingDocument 객체
            
        Returns:
            bool: 메타데이터 추출 성공 여부
        """
        if not self.enrichment_options.extract_metadata or not self.prompt_manager:
            return False
        
        try:
            # 문서의 처음 2페이지에서 텍스트 추출
            temp_content = ""
            total_pages = len(document.pages)
            for page in range(1, min(3, total_pages + 1)):
                temp_content += document.export_to_markdown(page_no=page)
            
            metadata = self._extract_document_metadata(temp_content)
            if metadata:
                _log.info(f"추출된 메타데이터: {json.dumps(metadata, ensure_ascii=False, indent=2)}")
                
                # KeyValueItem 생성을 위한 GraphData 구성
                graph_cells = []
                cell_id = 0
                
                # 메타데이터 딕셔너리를 그대로 key-value로 변환
                for key, value in metadata.items():
                    graph_cells.append(GraphCell(
                        label=GraphCellLabel.KEY,
                        cell_id=cell_id,
                        text=key,
                        orig=key
                    ))
                    cell_id += 1
                    
                    graph_cells.append(GraphCell(
                        label=GraphCellLabel.VALUE,
                        cell_id=cell_id,
                        text=json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value),
                        orig=json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
                    ))
                    cell_id += 1
                
                # GraphData 생성
                graph_data = GraphData(cells=graph_cells, links=[])
                
                # KeyValueItem을 문서에 추가
                document.add_key_values(
                    graph=graph_data,
                    prov=None,
                    parent=None
                )
                return True
            else:
                return False
                
        except Exception as e:
            _log.error(f"메타데이터 추출 중 오류 발생: {str(e)}")
            return False
    
    def _convert_section_headers_to_text(self, document):
        """문서의 모든 SectionHeaderItem을 TextItem으로 변환"""
        new_texts = []
        
        for item in document.texts:
            if isinstance(item, SectionHeaderItem):
                new_item = TextItem(
                    self_ref=item.self_ref,
                    parent=item.parent,
                    children=item.children,
                    content_layer=item.content_layer,
                    label=DocItemLabel.TEXT,
                    prov=item.prov,
                    orig=item.orig,
                    text=item.text,
                    formatting=item.formatting,
                    hyperlink=getattr(item, 'hyperlink', None)
                )
                new_texts.append(new_item)
            else:
                new_texts.append(item)
        
        document.texts = new_texts
    
    def _extract_raw_text_for_toc(self, document):
        """문서에서 원시 텍스트 추출"""
        raw_texts = ""
        for text in document.texts:
            cleaned_text = re.sub(r'\s+', ' ', text.text.strip())
            raw_texts += cleaned_text + "\n"
        return raw_texts
    
    def _parse_toc_content(self, toc_content: str):
        """목차 내용을 파싱해서 구조화된 데이터로 변환 (문서 제목 포함)"""
        toc_items = []
        document_title = None
        lines = toc_content.split('\n')
        
        for line in lines:
            cleaned_line = line.strip()
            if not cleaned_line:
                continue
            
            # 문서 제목 추출
            if cleaned_line.startswith('TITLE:'):
                document_title = cleaned_line[6:].strip()  # 'TITLE: ' 제거
                continue
            
            # 숫자 패턴들 (레벨별 매칭)
            patterns = [
                r'^(\d+\.\d+\.\d+\.\d+)\.\s*(.+)$',   # 1.1.1.1. 제목 (4단계)
                r'^(\d+\.\d+\.\d+)\.\s*(.+)$',        # 1.1.1. 제목 (3단계)
                r'^(\d+\.\d+)\.\s*(.+)$',              # 1.1. 제목 (2단계)
                r'^(\d+)\.\s*(.+)$',                    # 1. 제목 (1단계)
            ]
            
            matched = False
            for pattern in patterns:
                match = re.match(pattern, cleaned_line)
                if match:
                    number_part = match.group(1)
                    title_part = match.group(2).strip()
                    level = number_part.count('.') + 1
                    
                    toc_items.append({
                        'number': number_part,
                        'title': title_part,
                        'level': level,
                        'full_text': cleaned_line
                    })
                    matched = True
                    break
            
            if not matched and cleaned_line:
                # 패턴에 맞지 않는 줄은 레벨 1로 처리
                toc_items.append({
                    'number': '',
                    'title': cleaned_line,
                    'level': 1,
                    'full_text': cleaned_line
                })
        
        return {'title': document_title, 'toc_items': toc_items}
    
    def _apply_toc_to_document(self, document, toc_content: str, threshold: float = 0.7):
        """생성된 목차를 기반으로 문서에 SectionHeader 적용 (문서 제목 포함)"""
        # 목차 파싱 (제목과 목차 항목 분리)
        parsed_data = self._parse_toc_content(toc_content)
        document_title = parsed_data['title']
        toc_items = parsed_data['toc_items']
        
        converted_indices = set()
        
        # 텍스트 아이템들 준비
        text_items = [
            (i, item.text.strip()) 
            for i, item in enumerate(document.texts)
            if (isinstance(item, TextItem) and 
                item.label == DocItemLabel.TEXT and 
                len(item.text.strip()) >= 2)
        ]
        
        matched_count = 0
        
        # 문서 제목 처리 - 첫 번째 텍스트 항목을 TITLE로 변환
        if document_title and text_items:
            title_clean = document_title.strip()
            
            # 문서 제목과 가장 유사한 텍스트 찾기
            text_only = [text for _, text in text_items]
            close_matches = difflib.get_close_matches(
                title_clean, text_only, n=3, cutoff=0.3
            )
            
            if close_matches:
                best_match_text = close_matches[0]
                best_match_idx = next(
                    (idx for idx, text in text_items if text == best_match_text), 
                    None
                )
                
                if best_match_idx is not None and best_match_idx not in converted_indices:
                    similarity = difflib.SequenceMatcher(
                        None, title_clean.lower(), best_match_text.lower()
                    ).ratio()
                    
                    if similarity >= 0.5:  # 제목은 조금 더 관대한 임계값 사용
                        # TextItem을 TITLE로 변환
                        original_item = document.texts[best_match_idx]
                        original_item.label = DocItemLabel.TITLE
                        converted_indices.add(best_match_idx)
                        matched_count += 1
                        _log.info(f"문서 제목 설정: {title_clean}")
        
        # 각 목차 항목을 문서의 텍스트와 매칭 (기존 코드와 동일)
        for toc_item in toc_items:
            toc_clean = toc_item['title']
            target_level = toc_item['level']
            
            if len(toc_clean) < 2:
                continue
            
            # difflib을 사용한 유사 텍스트 찾기
            text_only = [text for _, text in text_items]
            close_matches = difflib.get_close_matches(
                toc_clean, text_only, n=3, cutoff=0.3
            )
            
            if close_matches:
                best_match_text = close_matches[0]
                best_match_idx = next(
                    (idx for idx, text in text_items if text == best_match_text), 
                    None
                )
                
                if best_match_idx is not None and best_match_idx not in converted_indices:
                    similarity = difflib.SequenceMatcher(
                        None, toc_clean.lower(), best_match_text.lower()
                    ).ratio()
                    
                    if similarity >= threshold:
                        # TextItem을 SectionHeaderItem으로 변환
                        original_item = document.texts[best_match_idx]
                        section_header = SectionHeaderItem(
                            self_ref=original_item.self_ref,
                            parent=original_item.parent,
                            children=original_item.children,
                            content_layer=original_item.content_layer,
                            prov=original_item.prov,
                            orig=original_item.orig,
                            text=original_item.text,
                            formatting=original_item.formatting,
                            hyperlink=getattr(original_item, 'hyperlink', None),
                            level=target_level
                        )
                        document.texts[best_match_idx] = section_header
                        converted_indices.add(best_match_idx)
                        matched_count += 1
        
        return matched_count
    
    def _extract_document_metadata(self, document_content):
        """
        문서 내용에서 메타데이터 정보를 추출하는 함수
        
        Args:
            document_content (str): 문서 내용
            
        Returns:
            dict: 추출된 메타데이터 딕셔너리
        """
        try:
            # 사용자 정의 프롬프트 가져오기
            custom_system = self.enrichment_options.metadata_system_prompt
            custom_user = self.enrichment_options.metadata_user_prompt
            
            # 프롬프트 매니저를 사용하여 AI 모델 호출
            response = self.prompt_manager.call_ai_model(
                category="metadata_extraction",
                prompt_type="korean_financial",
                custom_system=custom_system,
                custom_user=custom_user,
                document_content=document_content
            )
            
            if not response:
                return {"작성일": None, "작성자": []}
            
            # JSON 찾기
            json_pattern = r'```json\s*([\s\S]*?)\s*```'
            match = re.search(json_pattern, response)
            
            if match:
                try:
                    metadata = json.loads(match.group(1))
                    return metadata
                except:
                    return {"작성일": None, "작성자": []}
            else:
                try:
                    # JSON 블록이 없는 경우 전체 응답을 JSON으로 파싱 시도
                    return json.loads(response)
                except:
                    return {"작성일": None, "작성자": []}
                    
        except Exception as e:
            _log.error(f"메타데이터 추출 중 오류 발생: {str(e)}")
            return {"작성일": None, "작성자": []}


# 간단한 함수형 API
def enrich_document(document: DoclingDocument, enrichment_options: DataEnrichmentOptions) -> DoclingDocument:
    """
    DoclingDocument에 enrichment를 적용한 새로운 DoclingDocument를 반환
    
    Args:
        document: 원본 DoclingDocument 객체
        enrichment_options: 데이터 enrichment 옵션
        
    Returns:
        DoclingDocument: enrichment가 적용된 새로운 DoclingDocument
    """
    if not document:
        return document
    
    # 개별 기능이 하나도 활성화되지 않았으면 원본 반환
    if not enrichment_options.do_toc_enrichment and not enrichment_options.extract_metadata:
        return document
    
    try:
        # DoclingDocument 복사 (일반적으로 pickle 가능)
        enriched_doc = deepcopy(document)
        enricher = DocumentEnrichmentUtils(enrichment_options)
        
        # enrichment 적용
        toc_count = 0
        metadata_extracted = False
        
        if enrichment_options.do_toc_enrichment:
            toc_count = enricher.apply_toc_enrichment(enriched_doc)
        
        if enrichment_options.extract_metadata:
            metadata_extracted = enricher.apply_metadata_enrichment(enriched_doc)
        _log.info(f"Document enrichment 완료: TOC {toc_count}개, 메타데이터 {metadata_extracted}")
        
        return enriched_doc
        
    except Exception as e:
        _log.error(f"Document enrichment 중 오류 발생: {str(e)}")
        # 실패 시 원본 반환
        return document


def enrich_document_inplace(document: DoclingDocument, enrichment_options: DataEnrichmentOptions) -> Dict[str, Any]:
    """
    원본 DoclingDocument를 직접 수정하는 방식 (복사 없음)
    
    Args:
        document: 수정할 DoclingDocument 객체
        enrichment_options: 데이터 enrichment 옵션
        
    Returns:
        dict: enrichment 결과 정보
    """
    if not document:
        return {'toc_count': 0, 'metadata_extracted': False}
    
    # 개별 기능이 하나도 활성화되지 않았으면 빈 결과 반환
    if not enrichment_options.do_toc_enrichment and not enrichment_options.extract_metadata:
        return {'toc_count': 0, 'metadata_extracted': False}
    
    enricher = DocumentEnrichmentUtils(enrichment_options)
    
    result = {
        'toc_count': 0,
        'metadata_extracted': False
    }
    
    try:
        # 원본 document 직접 수정
        if enrichment_options.do_toc_enrichment:
            result['toc_count'] = enricher.apply_toc_enrichment(document)
        
        if enrichment_options.extract_metadata:
            result['metadata_extracted'] = enricher.apply_metadata_enrichment(document)
        
        _log.info(f"Document enrichment 완료 (in-place): {result}")
        
    except Exception as e:
        _log.error(f"Document enrichment 중 오류 발생: {str(e)}")
    
    return result


def add_toc(document: DoclingDocument, enrichment_options: DataEnrichmentOptions) -> DoclingDocument:
    """TOC만 추가한 새로운 DoclingDocument 반환"""
    toc_options = DataEnrichmentOptions(
        do_toc_enrichment=True,
        extract_metadata=False,
        **{k: v for k, v in enrichment_options.model_dump().items() if k.startswith('toc_')}
    )
    
    return enrich_document(document, toc_options)


def add_metadata(document: DoclingDocument, enrichment_options: DataEnrichmentOptions) -> DoclingDocument:
    """메타데이터만 추가한 새로운 DoclingDocument 반환"""
    metadata_options = DataEnrichmentOptions(
        do_toc_enrichment=False,
        extract_metadata=True,
        **{k: v for k, v in enrichment_options.model_dump().items() if k.startswith('metadata_')}
    )
    
    return enrich_document(document, metadata_options) 