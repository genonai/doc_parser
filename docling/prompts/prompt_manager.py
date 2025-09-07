import json
import logging
import os
import requests
from pathlib import Path
from typing import Dict, Any, List, Optional

_log = logging.getLogger(__name__)

class PromptManager:
    """프롬프트 및 API 설정 관리 클래스"""

    def __init__(self, prompts_file: Optional[str] = None, custom_prompts: Optional[Dict[str, Any]] = None, custom_api_configs: Optional[Dict[str, Dict[str, Any]]] = None):
        """
        Args:
            prompts_file: 프롬프트 JSON 파일 경로 (기본값: prompts.json)
            custom_prompts: 사용자 정의 프롬프트 딕셔너리
            custom_api_configs: 카테고리별 사용자 정의 API 설정 딕셔너리
                예: {
                    "toc_extraction": {"provider": "openai", "api_key": "...", "model": "gpt-4"},
                    "metadata_extraction": {"provider": "openrouter", "api_key": "...", "model": "..."}
                }
        """
        if prompts_file is None:
            self.prompts_file = Path(__file__).parent / "prompts.json"
        else:
            self.prompts_file = Path(prompts_file)

        self._config = self._load_config()
        self.custom_prompts = custom_prompts or {}
        self.custom_api_configs = custom_api_configs or {}

    def _load_config(self) -> Dict[str, Any]:
        """설정 JSON 파일 로드"""
        try:
            with open(self.prompts_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            _log.error(f"설정 파일을 찾을 수 없습니다: {self.prompts_file}")
            return {}
        except json.JSONDecodeError as e:
            _log.error(f"설정 파일 JSON 파싱 오류: {e}")
            return {}

    def get_api_config(self, provider: str, category: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """API 설정 반환 (카테고리별 사용자 정의 우선)"""
        # 카테고리별 사용자 정의 API 설정 확인
        if category and category in self.custom_api_configs:
            custom_config = self.custom_api_configs[category]
            if "provider" in custom_config and custom_config["provider"] == provider:
                config = custom_config.copy()

                # 환경변수에서 API 키 우선 조회
                if config.get("api_key") is None:
                    env_key_name = f"{provider.upper()}_API_KEY"
                    env_api_key = os.getenv(env_key_name)
                    if env_api_key:
                        config["api_key"] = env_api_key
                        _log.info(f"환경변수에서 {provider} API 키 로드됨 (카테고리: {category})")

                return config

        # 기본 설정 사용
        try:
            api_config = self._config["api_config"][provider].copy()

            # 환경변수에서 API 키 우선 조회
            env_key_name = f"{provider.upper()}_API_KEY"
            env_api_key = os.getenv(env_key_name)
            if env_api_key:
                api_config["api_key"] = env_api_key
                _log.info(f"환경변수에서 {provider} API 키 로드됨")

            return api_config
        except KeyError:
            _log.error(f"API 설정을 찾을 수 없습니다: {provider}")
            return None

    def get_prompt_config(self, category: str, prompt_type: str) -> Optional[Dict[str, Any]]:
        """특정 프롬프트 설정 반환 (사용자 정의 프롬프트 및 카테고리별 모델 설정 우선)"""
        # 먼저 사용자 정의 프롬프트에서 찾기
        if category in self.custom_prompts and prompt_type in self.custom_prompts[category]:
            custom_config = self.custom_prompts[category][prompt_type]
            # 기본 설정과 병합
            try:
                default_config = self._config[category][prompt_type].copy()
                default_config.update(custom_config)

                # 카테고리별 사용자 정의 모델 설정 적용
                if category in self.custom_api_configs:
                    api_config = self.custom_api_configs[category]
                    if "model" in api_config:
                        default_config["model"] = api_config["model"]
                    if "provider" in api_config:
                        default_config["api_provider"] = api_config["provider"]
                    if "temperature" in api_config:
                        default_config["temperature"] = api_config["temperature"]
                    if "top_p" in api_config:
                        default_config["top_p"] = api_config["top_p"]
                    if "seed" in api_config:
                        default_config["seed"] = api_config["seed"]
                    if "max_tokens" in api_config:
                        default_config["max_tokens"] = api_config["max_tokens"]

                return default_config
            except KeyError:
                # 기본 설정이 없으면 사용자 정의만 사용
                return custom_config

        # 기본 프롬프트에서 찾기
        try:
            config = self._config[category][prompt_type].copy()

            # 카테고리별 사용자 정의 모델 설정 적용
            if category in self.custom_api_configs:
                api_config = self.custom_api_configs[category]
                if "model" in api_config:
                    config["model"] = api_config["model"]
                if "provider" in api_config:
                    config["api_provider"] = api_config["provider"]
                if "temperature" in api_config:
                    config["temperature"] = api_config["temperature"]
                if "top_p" in api_config:
                    config["top_p"] = api_config["top_p"]
                if "seed" in api_config:
                    config["seed"] = api_config["seed"]
                if "max_tokens" in api_config:
                    config["max_tokens"] = api_config["max_tokens"]

            return config
        except KeyError:
            _log.error(f"프롬프트를 찾을 수 없습니다: {category}.{prompt_type}")
            return None

    def get_system_prompt(self, category: str, prompt_type: str, custom_system: Optional[str] = None) -> Optional[str]:
        """시스템 프롬프트 반환 (사용자 정의 우선)"""
        if custom_system:
            return custom_system
        config = self.get_prompt_config(category, prompt_type)
        return config.get("system") if config else None

    def get_user_prompt_template(self, category: str, prompt_type: str, custom_user: Optional[str] = None) -> Optional[str]:
        """사용자 프롬프트 템플릿 반환 (사용자 정의 우선)"""
        if custom_user:
            return custom_user
        config = self.get_prompt_config(category, prompt_type)
        return config.get("user") if config else None

    def format_user_prompt(self, category: str, prompt_type: str, custom_user: Optional[str] = None, **kwargs) -> Optional[str]:
        """사용자 프롬프트 템플릿에 변수를 삽입하여 완성된 프롬프트 반환"""
        template = self.get_user_prompt_template(category, prompt_type, custom_user)
        if template is None:
            return None

        try:
            return template.format(**kwargs)
        except KeyError as e:
            _log.error(f"프롬프트 템플릿 변수 오류: {e}")
            return None

    def get_messages(self, category: str, prompt_type: str, custom_system: Optional[str] = None, custom_user: Optional[str] = None, **kwargs) -> Optional[List[Dict[str, Any]]]:
        """API 요청용 메시지 형식으로 반환"""
        system_prompt = self.get_system_prompt(category, prompt_type, custom_system)
        user_prompt = self.format_user_prompt(category, prompt_type, custom_user, **kwargs)

        if system_prompt is None or user_prompt is None:
            return None

        return [
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ]

    def get_model_config(self, category: str, prompt_type: str) -> Dict[str, Any]:
        """모델 설정 반환 (카테고리별 사용자 정의 우선)"""
        config = self.get_prompt_config(category, prompt_type)
        if config is None:
            return {
                "model": "google/gemma-3-27b-it:free",
                "seed": 3,
                "api_provider": "openrouter"
            }

        model_config = {
            "model": config.get("model", "google/gemma-3-27b-it:free"),
            "seed": config.get("seed", 3),
            "api_provider": config.get("api_provider", "openrouter")
        }

        # 선택적 파라미터들 추가
        if "temperature" in config:
            model_config["temperature"] = config["temperature"]
        if "top_p" in config:
            model_config["top_p"] = config["top_p"]
        if "max_tokens" in config:
            model_config["max_tokens"] = config["max_tokens"]

        return model_config

    def call_ai_model(self, category: str, prompt_type: str, custom_system: Optional[str] = None, custom_user: Optional[str] = None, **kwargs) -> Optional[str]:
        """AI 모델을 직접 requests.post로 호출하여 결과 반환"""
        # 메시지 구성
        messages = self.get_messages(category, prompt_type, custom_system, custom_user, **kwargs)
        if messages is None:
            return None

        # 모델 설정 가져오기
        model_config = self.get_model_config(category, prompt_type)
        api_provider = model_config["api_provider"]

        # 카테고리별 API 설정 가져오기
        api_config = self.get_api_config(api_provider, category)
        if api_config is None:
            return None

        try:
            # 요청 헤더 구성
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_config['api_key']}"
            }

            # 요청 본문 구성
            payload = {
                "model": model_config["model"],
                "messages": messages
            }

            # 선택적 파라미터 추가
            if "seed" in model_config:
                payload["seed"] = model_config["seed"]
            if "temperature" in model_config:
                payload["temperature"] = model_config["temperature"]
            if "top_p" in model_config:
                payload["top_p"] = model_config["top_p"]
            if "max_tokens" in model_config:
                payload["max_tokens"] = model_config["max_tokens"]

            # API URL 구성
            base_url = api_config.get("api_base_url", api_config.get("base_url"))
            if base_url.endswith("/chat/completions"):
                api_url = base_url
            else:
                api_url = f"{base_url}/chat/completions"

            _log.debug(f"API 요청 URL ({category}): {api_url}")
            _log.debug(f"API 요청 페이로드 ({category}): {json.dumps(payload, ensure_ascii=False, indent=2)}")

            # requests.post로 API 호출
            response = requests.post(
                api_url,
                headers=headers,
                json=payload,
                timeout=120
            )

            # 응답 상태 확인
            response.raise_for_status()

            # 응답 파싱
            response_data = response.json()

            # 응답에서 텍스트 추출
            if "choices" in response_data and len(response_data["choices"]) > 0:
                return response_data["choices"][0]["message"]["content"]
            else:
                _log.error(f"예상하지 못한 응답 형식 ({category}): {response_data}")
                return None

        except requests.exceptions.RequestException as e:
            _log.error(f"API 요청 실패 ({category}.{prompt_type}): {e}")
            if hasattr(e, 'response') and e.response is not None:
                _log.error(f"응답 상태: {e.response.status_code}")
                _log.error(f"응답 내용: {e.response.text}")
            return None
        except json.JSONDecodeError as e:
            _log.error(f"JSON 응답 파싱 실패 ({category}.{prompt_type}): {e}")
            return None
        except Exception as e:
            _log.error(f"AI 모델 호출 중 예상치 못한 오류 ({category}.{prompt_type}): {e}")
            return None

    def reload_config(self):
        """설정 파일 다시 로드"""
        self._config = self._load_config()
        _log.info("프롬프트 설정이 다시 로드되었습니다.")
