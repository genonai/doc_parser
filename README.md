# 📄 Intelligent Document Preprocessor API

지능형 문서 전처리기 FastAPI 서버입니다. PDF 및 기타 문서 파일에서 텍스트를 추출하고 처리된 데이터를 JSON 형식으로 반환합니다.

## ✨ 주요 기능

- 📄 **다중 파일 형식 지원**: PDF, HWP, DOCX 등
- 🔤 **텍스트 추출**: 페이지 단위로 정확한 텍스트 추출
- 📊 **JSON 응답**: 표준화된 구조화된 데이터 반환
- 🚀 **빠른 처리**: 대용량 문서도 빠르게 처리
- 🌐 **REST API**: 간단한 HTTP 인터페이스

## 🛠️ 빠른 시작 (5분)

### 1️⃣ 저장소 클론 및 진입

```bash
git clone <repository-url>
cd doc_parser
```

### 2️⃣ 환경 설정

#### Windows / macOS / Linux

```bash
# 가상환경 생성
python3 -m venv venv

# 가상환경 활성화
# macOS/Linux:
source venv/bin/activate
# Windows:
venv\Scripts\activate

# 의존성 설치 (약 5-10분)
pip install --upgrade pip
pip install -r requirements.txt
```

### 3️⃣ 서버 시작

```bash
cd genon/preprocessor/src
python -m uvicorn intelligent_main:app --host 127.0.0.1 --port 7085 --reload
```

**성공 메시지:**
```
INFO:     Uvicorn running on http://127.0.0.1:7085
INFO:     Application startup complete.
```

### 4️⃣ API 테스트

#### 헬스체크 (서버 상태 확인)

```bash
curl http://127.0.0.1:7085/healthcheck
# 응답: {"status":"ok"}
```

#### 파일 업로드 및 처리

```bash
curl -X POST http://127.0.0.1:7085/upload/run \
  -F "file=@/path/to/document.pdf" \
  -F "save_images=true" \
  -F "log_level=4"
```

## 📚 API 엔드포인트

### `GET /healthcheck`

서버 상태 확인

**응답:**
```json
{"status":"ok"}
```

---

### `POST /run`

파일 경로 기반 문서 처리

**요청:**
```json
{
  "file_path": "/path/to/document.pdf",
  "params": {
    "save_images": true,
    "include_wmf": false,
    "log_level": 4
  }
}
```

**응답:**
```json
{
  "code": 0,
  "errMsg": "success",
  "data": [
    {
      "text": "추출된 텍스트...",
      "title": "문서제목",
      "created_date": 0,
      "i_page": 1,
      "e_page": 1,
      "chunk_bboxes": "[]",
      "media_files": "[]"
    }
  ]
}
```

---

### `POST /upload/run`

파일 업로드 후 처리 (권장)

**Form 파라미터:**
- `file` (필수): 업로드할 문서 파일
- `save_images` (선택): 이미지 저장 여부 (기본값: true)
- `include_wmf` (선택): WMF 이미지 포함 (기본값: false)
- `log_level` (선택): 로그 레벨 0~5 (기본값: 4)

**응답:** `/run`과 동일

**예시 (Python):**
```python
import requests

with open('document.pdf', 'rb') as f:
    files = {'file': f}
    response = requests.post(
        'http://127.0.0.1:7085/upload/run',
        files=files
    )
    print(response.json())
```

## 📁 프로젝트 구조

```
doc_parser/
├── genon/preprocessor/
│   ├── src/
│   │   ├── intelligent_main.py          # FastAPI 메인 파일
│   │   ├── simple_processor.py          # 간단한 문서 프로세서
│   │   ├── logger.py                    # 로거 유틸
│   │   ├── utils.py                     # 응답 포맷 함수
│   │   └── config.py                    # CORS 설정
│   ├── facade/
│   │   └── intelligent_processor.py     # 지능형 프로세서 (선택)
│   └── docker/
│       └── Dockerfile.intelligent       # Docker 빌드 파일
├── dataset-ad-review/                   # 테스트 데이터 디렉토리
├── requirements.txt                     # Python 의존성
├── README.md                            # 이 파일
└── USAGE.md                             # 상세 사용 가이드
```

## 🔧 설정 및 커스터마이징

### 포트 변경

```bash
# 다른 포트에서 실행 (예: 8000)
python -m uvicorn intelligent_main:app --host 127.0.0.1 --port 8000
```

### 로그 레벨 조정

```bash
# 로그 레벨 설정
python -m uvicorn intelligent_main:app --log-level debug
```

### 프로덕션 배포

```bash
# Gunicorn 사용 (권장)
pip install gunicorn
gunicorn intelligent_main:app -w 4 -b 0.0.0.0:7085
```

## 🐛 문제 해결

### 포트이미 사용 중 오류

```bash
# 포트 확인
lsof -i :7085

# 프로세스 종료
kill -9 <PID>

# 다른 포트 사용
python -m uvicorn intelligent_main:app --port 8085
```

### 모듈 찾을 수 없음

```bash
# 가상환경 활성화 확인
which python  # 또는 where python (Windows)

# venv 폴더에서 python 경로가 나와야 함
# 그렇지 않으면 activate 스크립트 재실행
```

### 느린 처리 속도

- 첫 요청이 느린 이유: 모델 로딩 (1회만 발생)
- 이후 요청은 빠름

## 📊 응답 형식 설명

```json
{
  "code": 0,              // 0: 성공, 1: 실패
  "errMsg": "success",    // 상태 메시지
  "data": [
    {
      "text": "추출된 텍스트...",           // 페이지 내용
      "title": "문서제목",                   // 원본 파일명
      "created_date": 0,                   // 생성일 (YYYYmmdd 형식)
      "i_page": 1,                         // 시작 페이지 번호
      "e_page": 1,                         // 종료 페이지 번호
      "chunk_bboxes": "[]",                // 바운딩 박스 정보 (JSON)
      "media_files": "[]"                  // 미디어 파일 정보 (JSON)
    }
  ]
}
```

## 🚀 성능 팁

| 항목 | 성능 |
|------|------|
| **첫 요청 (모델 로딩)** | ~2-5초 |
| **일반 요청 (A4 28page)** | ~0.3초 |
| **메모리 사용** | ~2-3GB |
| **동시 처리** | 1개 요청 (순차 처리) |

## 📝 예제

### cURL로 테스트

```bash
# 1. 헬스체크
curl http://127.0.0.1:7085/healthcheck

# 2. 파일 업로드 및 처리
curl -X POST http://127.0.0.1:7085/upload/run \
  -F "file=@sample.pdf" \
  -F "save_images=true" \
  -F "log_level=4" \
  | python3 -m json.tool > result.json

# 3. 결과 확인
cat result.json
```

### Python 클라이언트

```python
import requests
import json

def process_document(file_path):
    """문서를 처리하고 결과를 반환"""
    url = 'http://127.0.0.1:7085/upload/run'
    
    with open(file_path, 'rb') as f:
        files = {'file': f}
        data = {
            'save_images': 'true',
            'log_level': '4'
        }
        response = requests.post(url, files=files, data=data)
    
    return response.json()

# 사용
result = process_document('document.pdf')
print(f"처리된 페이지: {len(result['data'])} pages")
print(f"상태: {result['errMsg']}")

# 첫 페이지 내용 출력
if result['data']:
    print(f"\n첫 페이지 텍스트:\n{result['data'][0]['text'][:200]}...")
```

## 📞 문제 보고

문제가 발생하면 다음 정보를 함께 제공해주세요:

- Python 버전: `python --version`
- OS: Windows/macOS/Linux
- 에러 메시지 (전체)
- 사용한 파일 타입
- 서버 로그

## 📄 라이센스

내부 사용 전용

---

**마지막 업데이트**: 2026-05-19  
**버전**: 1.0.0
