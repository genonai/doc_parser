# 🎯 설치 및 실행 가이드

## 🚀 5분 안에 시작하기

### 1단계: 자동 설치 (권장)

#### macOS/Linux:
```bash
bash quick_start.sh
```

#### Windows (PowerShell):
```powershell
.\quick_start.bat
```

### 2단계: 서버 실행

```bash
cd genon/preprocessor/src
python -m uvicorn intelligent_main:app --host 127.0.0.1 --port 7085 --reload
```

### 3단계: API 테스트

```bash
# 다른 터미널에서
curl http://127.0.0.1:7085/healthcheck
```

---

## 📋 수동 설치

### Step 1: 가상환경 생성

**macOS/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows:**
```cmd
python -m venv venv
venv\Scripts\activate
```

### Step 2: 의존성 설치

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3: 서버 실행

```bash
cd genon/preprocessor/src
python -m uvicorn intelligent_main:app --host 127.0.0.1 --port 7085 --reload
```

---

## ✅ 설치 확인

```bash
# 1. 가상환경 활성화 확인
which python  # macOS/Linux - venv 경로가 나와야 함
where python  # Windows

# 2. 패키지 확인
pip list | grep fastapi
pip list | grep uvicorn

# 3. 서버 시작 후 헬스체크
curl http://127.0.0.1:7085/healthcheck
# {"status":"ok"}
```

---

## 📁 프로젝트 구조

```
doc_parser/
├── genon/preprocessor/
│   ├── src/
│   │   ├── intelligent_main.py          # 🔴 메인 FastAPI 파일
│   │   ├── simple_processor.py          # PDF 텍스트 추출기
│   │   ├── logger.py                    # 로깅 유틸
│   │   ├── utils.py                     # 응답 포맷
│   │   └── config.py                    # CORS 설정
│   ├── facade/
│   │   └── intelligent_processor.py     # 지능형 프로세서 (선택)
│   └── docker/
│       └── Dockerfile.intelligent       # Docker 빌드 파일
├── dataset-ad-review/                   # 테스트 PDF 파일들
├── requirements.txt                     # 🔴 Python 의존성
├── README.md                            # 📖 기본 가이드
├── USAGE.md                             # 📖 상세 가이드
├── SETUP.md                             # 📖 이 파일
├── quick_start.sh                       # 🔴 자동 설치 (Mac/Linux)
├── quick_start.bat                      # 🔴 자동 설치 (Windows)
├── .gitignore                           # Git 무시 파일
└── venv/                                # (생성 후) 가상환경
```

---

## 🐛 문제 해결

### "Python3 not found"
```bash
# Python 3.9+ 설치 필요
python3 --version  # 3.9 이상이어야 함
```

### "No module named 'fastapi'"
```bash
# 가상환경 활성화 확인
source venv/bin/activate  # macOS/Linux
venv\Scripts\activate     # Windows

# 의존성 재설치
pip install -r requirements.txt
```

### "Port 7085 already in use"
```bash
# 다른 포트 사용
python -m uvicorn intelligent_main:app --port 8085
```

---

## 📞 지원

더 자세한 정보:
- README.md: 전체 개요 및 API 문서
- USAGE.md: 상세한 사용 예제 및 FAQ

