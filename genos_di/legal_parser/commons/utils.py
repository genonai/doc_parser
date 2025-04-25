from commons.type_converter import TypeConverter

type_converter = TypeConverter()

def replace_strip(data: list[str]) -> list[str]:
    return [x.strip() for x in data if x.strip()]

def format_date(year:str, month:str, day:str) -> str:
    if type_converter.validator((year, month, day), tuple[str, str, str]):
        return f"{year}{int(month):02d}{int(day):02d}"        
       