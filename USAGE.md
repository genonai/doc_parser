# 📖 상세 사용 가이드

## 목차

1. [설치 및 환경 설정](#설치-및-환경-설정)
2. [서버 실행](#서버-실행)
3. [API 사용 방법](#api-사용-방법)
4. [실제 예제](#실제-예제)
5. [문제 해결](#문제-해결)

---

## 설치 및 환경 설정

### 사전 요구사항

- **Python**: 3.9 이상
- **pip**: 최신 버전
- **OS**: Windows, macOS, Linux

### 단계별 설치

#### Step 1: 저장소 클론

```bash
git clone <repository-url>
cd doc_parser
```

#### Step 2: 가상환경 생성

**macOS/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows (PowerShell):**
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
```

**Windows (CMD):**
```cmd
python -m venv venv
venv\Scripts\activate
```

#### Step 3: 의존성 설치

```bash
# pip 업그레이드
pip install --upgrade pip setuptools wheel

# 의존성 설치 (5-10분 소요)
pip install -r requirements.txt
```

**설치 진행 상황:**
```
Collecting fastapi...
Downloading fastapi-...
...
Successfully installed fastapi-... uvicorn-... [등등]
```

#### Step 4: 설치 확인

```bash
python -c "import fastapi; import uvicorn; print('✅ 설치 완료')"
```

---

## 서버 실행

### 기본 실행

```bash
cd genon/preprocessor/src
python -m uvicorn intelligent_main:app --host 127.0.0.1 --port 7085 --reload
```

**출력:**
```
INFO:     Uvicorn running on http://127.0.0.1:7085
INFO:     Started reloader process [12345] using WatchFiles
INFO:     Started server process [12346]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

### 서버 중지

```bash
# Ctrl+C 누르기
^C
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
```

### 옵션으로 실행

**다른 호스트에서 접근 가능하게:**
```bash
python -m uvicorn intelligent_main:app --host 0.0.0.0 --port 7085
```

**프로덕션 모드 (reload 비활성):**
```bash
python -m uvicorn intelligent_main:app --host 0.0.0.0 --port 7085 --no-reload
```

**여러 워커 (프로덕션):**
```bash
pip install gunicorn
gunicorn intelligent_main:app -w 4 -b 0.0.0.0:7085
```

---

## API 사용 방법

### 1️⃣ 헬스체크 (Health Check)

서버가 정상적으로 실행 중인지 확인합니다.

**요청:**
```bash
curl http://127.0.0.1:7085/healthcheck
```

**응답:**
```json
{"status":"ok"}
```

---

### 2️⃣ 파일 업로드 (추천)

#### cURL 사용

```bash
curl -X POST http://127.0.0.1:7085/upload/run \
  -F "file=@document.pdf" \
  -F "save_images=true" \
  -F "log_level=4"
```

#### Python 사용

```python
import requests
import json

file_path = "document.pdf"
url = "http://127.0.0.1:7085/upload/run"

with open(file_path, 'rb') as f:
    files = {'file': f}
    response = requests.post(url, files=files)

result = response.json()
print(json.dumps(result, indent=2, ensure_ascii=False))
```

#### JavaScript/Node.js 사용

```javascript
const FormData = require('form-data');
const fs = require('fs');
const axios = require('axios');

async function processDocument(filePath) {
    const form = new FormData();
    form.append('file', fs.createReadStream(filePath));
    
    const response = await axios.post(
        'http://127.0.0.1:7085/upload/run',
        form,
        { headers: form.getHeaders() }
    );
    
    console.log(JSON.stringify(response.data, null, 2));
}

processDocument('document.pdf');
```

---

### 3️⃣ 파일 경로 기반 처리

이미 서버 내에 있는 파일을 처리할 때 사용합니다.

**요청:**
```bash
curl -X POST http://127.0.0.1:7085/run \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/path/to/document.pdf",
    "params": {
      "save_images": true,
      "log_level": 4
    }
  }'
```

**Python 사용:**
```python
import requests
import json

data = {
    "file_path": "/path/to/document.pdf",
    "params": {
        "save_images": True,
        "log_level": 4
    }
}

response = requests.post('http://127.0.0.1:7085/run', json=data)
result = response.json()
print(json.dumps(result, indent=2, ensure_ascii=False))
```

---

## 실제 예제

### 예제 1: 기본 PDF 처리

```bash
# 1. 서버 시작 (터미널 1)
cd genon/preprocessor/src
python -m uvicorn intelligent_main:app --host 127.0.0.1 --port 7085

# 2. 다른 터미널에서 파일 처리
curl -X POST http://127.0.0.1:7085/upload/run \
  -F "file=@dataset-ad-review/(5-1) 회사홈페이지(자동차)_검토용.pdf" \
  | python3 -m json.tool

# 3. 결과 보기 (출력)
{
  "code": 0,
  "errMsg": "success",
  "data": [
    {
      "text": "파일명/메뉴명 상세 메뉴명...",
      "title": "intelligent_...",
      "created_date": 0,
      "i_page": 1,
      "e_page": 1,
      "chunk_bboxes": "[]",
      "media_files": "[]"
    },
    ...
  ]
}
```

### 예제 2: 배치 처리 (여러 파일)

**Python 스크립트 `batch_process.py`:**

```python
#!/usr/bin/env python3
import os
import requests
import json
from pathlib import Path

def process_directory(directory, api_url='http://127.0.0.1:7085/upload/run'):
    """디렉토리의 모든 PDF를 처리"""
    
    pdf_files = list(Path(directory).glob('*.pdf'))
    print(f"📁 Found {len(pdf_files)} PDF files")
    
    for i, pdf_file in enumerate(pdf_files, 1):
        print(f"\n[{i}/{len(pdf_files)}] Processing: {pdf_file.name}")
        
        try:
            with open(pdf_file, 'rb') as f:
                files = {'file': f}
                response = requests.post(api_url, files=files, timeout=60)
            
            result = response.json()
            
            if result['code'] == 0:
                print(f"  ✅ Success: {len(result['data'])} chunks extracted")
                
                # 결과 저장
                output_file = pdf_file.with_suffix('.json')
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)
                print(f"  💾 Saved: {output_file}")
            else:
                print(f"  ❌ Error: {result['errMsg']}")
                
        except requests.exceptions.RequestException as e:
            print(f"  ❌ Request failed: {e}")
        except Exception as e:
            print(f"  ❌ Error: {e}")

if __name__ == '__main__':
    directory = 'dataset-ad-review'  # PDF 파일이 있는 디렉토리
    process_directory(directory)
```

**실행:**
```bash
python batch_process.py
```

### 예제 3: 결과 JSON 분석

**Python 스크립트 `analyze_result.py`:**

```python
import json

def analyze_response(json_file):
    """응답 JSON을 분석하고 통계 출력"""
    
    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    if data['code'] != 0:
        print(f"❌ Error: {data['errMsg']}")
        return
    
    chunks = data['data']
    
    print("=" * 60)
    print("📊 분석 결과")
    print("=" * 60)
    print(f"총 청크: {len(chunks)}")
    print(f"제목: {chunks[0]['title']}")
    print(f"페이지 범위: {chunks[0]['i_page']} ~ {chunks[-1]['e_page']}")
    
    total_chars = sum(len(chunk['text']) for chunk in chunks)
    print(f"총 문자: {total_chars:,}")
    print(f"평균/청크: {total_chars // len(chunks):,}")
    
    print("\n📄 청크별 정보:")
    print("-" * 60)
    
    for i, chunk in enumerate(chunks[:5], 1):  # 처음 5개만 표시
        text_preview = chunk['text'].replace('\n', ' ')[:60]
        print(f"[{i}] Page {chunk['i_page']}: {text_preview}...")
    
    if len(chunks) > 5:
        print(f"... (and {len(chunks) - 5} more)")

if __name__ == '__main__':
    analyze_response('result.json')
```

**실행:**
```bash
python analyze_result.py
```

---

## 문제 해결

### 1️⃣ 포트가 이미 사용 중

**증상:**
```
OSError: [Errno 48] Address already in use
```

**해결:**
```bash
# macOS/Linux: 포트 사용 프로세스 확인
lsof -i :7085

# 결과: PID 확인 후 종료
kill -9 <PID>

# 또는 다른 포트 사용
python -m uvicorn intelligent_main:app --port 8085
```

### 2️⃣ 모듈 임포트 오류

**증상:**
```
ModuleNotFoundError: No module named 'fastapi'
```

**해결:**
```bash
# 가상환경 활성화 확인
which python  # macOS/Linux
where python  # Windows

# venv/bin/python 경로가 나와야 함
# 그렇지 않으면 activate 스크립트 재실행

# 의존성 재설치
pip install -r requirements.txt
```

### 3️⃣ 연결 거부 오류

**증상:**
```
ConnectionRefusedError: [Errno 111] Connection refused
```

**해결:**
```bash
# 1. 서버 실행 확인
ps aux | grep uvicorn

# 2. 서버가 없으면 시작
cd genon/preprocessor/src
python -m uvicorn intelligent_main:app --host 127.0.0.1 --port 7085
```

### 4️⃣ 느린 처리

**현상:**
- 첫 요청: 2-5초 (모델 로딩)
- 이후 요청: 0.3-1초

**개선:**
```bash
# 서버 시작 후 대기 (모델 사전로드)
# 첫 요청이 느린 것은 정상 (모델 초기화)
# 이후 요청은 빠름
```

### 5️⃣ 메모리 부족

**증상:**
```
MemoryError or process killed
```

**원인:**
- 매우 큰 PDF 파일 (>100MB)
- 고해상도 이미지 포함

**해결:**
```bash
# 메모리 여유 확인
free -h  # Linux
vm_stat  # macOS
tasklist  # Windows

# 불필요한 프로그램 종료
# 또는 다른 시간에 처리
```

---

## FAQ

### Q1: API를 원격에서 접근할 수 있나요?

**A:** 네, 가능합니다.

```bash
# 모든 인터페이스에서 수신
python -m uvicorn intelligent_main:app --host 0.0.0.0 --port 7085

# 그 후 다른 컴퓨터에서:
curl http://<서버IP>:7085/healthcheck
```

### Q2: 파일 크기 제한이 있나요?

**A:** 이론적으로는 없지만, 메모리와 처리 시간에 따라 제한됩니다.

- **추천**: < 50MB
- **일반적**: < 100MB
- **한계**: > 500MB (메모리 부족 가능)

### Q3: 동시에 여러 파일을 처리할 수 있나요?

**A:** 현재는 순차 처리입니다 (한 번에 하나씩).

여러 요청을 보내면 대기열에 들어가 순서대로 처리됩니다.

### Q4: 결과를 데이터베이스에 저장할 수 있나요?

**A:** 네, 응답 JSON을 원하는 형식으로 저장할 수 있습니다.

```python
import json
import sqlite3

# JSON 결과 저장
with open('result.json', 'w') as f:
    json.dump(response.json(), f)

# 또는 데이터베이스 저장
conn = sqlite3.connect('documents.db')
cursor = conn.cursor()
cursor.execute('INSERT INTO results VALUES (...)')
conn.commit()
```

---

## 지원

문제가 발생하면:

1. README.md의 문제 해결 섹션 확인
2. 서버 로그 확인: `/tmp/api.log`
3. Python/pip 버전 확인: `python --version`, `pip --version`
4. 패키지 재설치: `pip install -r requirements.txt --force-reinstall`

---

**마지막 업데이트**: 2026-05-19  
**버전**: 1.0.0
