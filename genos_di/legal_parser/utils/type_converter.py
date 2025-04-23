from typeguard import check_type, TypeCheckError
from typing import Any, Union, get_origin, get_args, Type, TypeVar

T = TypeVar('T')

def type_key(tp):
    # list[str], typing.List[str] 등 동등성 체크
    origin = get_origin(tp) or tp
    args = get_args(tp)
    return (origin, args)


class TypeConversionError(TypeError):
    """커스텀 타입 변환 예외 클래스"""
    def __init__(self, src_type: type, dest_type: type):
        self.src_type = src_type
        self.dest_type = dest_type
        super().__init__(f"변환 실패: {src_type.__name__} → {dest_type,__name__}")


class TypeConverter:
    def __init__(self):
        self._registry = {}
        self.register(int, self._convert_to_int)
        self.register(str, self._convert_to_str)
        self.register(list[dict], self._convert_to_dict_list)
        self.register(list[str], self._convert_to_str_list)
        self._default_values = {
            type_key(int): 0,
            type_key(float): 0.0,
            type_key(str): '',
            type_key(list[dict]): [],
            type_key(list[str]): []
        }

    def register(self, tp, func):
        self._registry[type_key(tp)] = func
        
    def converter(self, value: Any, target_type:Union[type, tuple[type, ...]], use_default: bool = False) -> Any:
        
        if self.validator(value, target_type):
            return value
        
        for t in self._expand_types(target_type):
            if converter := self._registry.get(type_key(t)):
                try:
                    return converter(value)
                except Exception:
                    if use_default:
                        return self.get_default_value(target_type)
                    raise TypeConversionError(type(value), target_type)

        # 적절한 converter를 찾지 못함함
        if use_default:
            return self.get_default_value(target_type)
        raise TypeConversionError(type(value), target_type)
    
    def get_default_value(self, target_type: Type[T]) -> T:

        key = type_key(target_type)
        if key in self._default_values:
            return self._default_values[key]
        
        # 컬렉션 타입에 대한 처리
        origin, args = type_key(target_type)
        
        if origin is list:
            return []
        elif origin is dict:
            return {}
        elif origin is set:
            return set()
        elif origin is tuple:
            if args:
                return tuple(self.get_default_value(arg) for arg in args)
            return ()
        if origin is Union:
            for arg in args:
                if arg is not type(None):  # None 타입이 아닌 첫 번째 타입의 기본값 반환
                    return self.get_default_value(arg)
        return None
    
    def validator(self, value: Any, target_type: Union[type, tuple[type, ...]]) -> bool:
        """기존 타입 검증 로직"""
        if not value:
            return False
        try:
            check_type(value, target_type)
            return True
        except TypeCheckError:
            return False

    def _expand_types(self, target_type: Union[type, tuple[type, ...]]) -> list[type]:
        """타입 계층 구조 평탄화"""
        if isinstance(target_type, tuple):
            return list(target_type)
        return [target_type]
    
    def _convert_to_int(self, value: Any) -> int:
        if self.validator(value, str) and value.isdigit():
            return int(value)
        raise TypeConversionError(type(value), int)

    def _convert_to_str(self, value: Any) -> str:
        if self.validator(value, list[list[str]]):
            # if all(isinstance(i, str) for i in value):
            #     return ''.join(value)
            # if isinstance(value[0], list):
            return ''.join(value[0])
        elif self.validator(value, list[str]):
            return ''.join(value)
        raise TypeConversionError(type(value), str)

    def _convert_to_dict_list(self, value: Any) -> list[dict]:
        if self.validator(value, dict):
            return [value]
        raise TypeConversionError(type(value), list[dict])

    def _convert_to_str_list(self, value: Any) -> list[str]:
        if self.validator(value, str):
            return [value]
        elif self.validator(value, list[list[str]]):
            return value[0]
        raise TypeConversionError(type(value), list[str])
    


        
        