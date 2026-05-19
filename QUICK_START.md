# ⚡ 5분 안에 시작하기

## 📦 필수 설치

```bash
# 1. Docker Desktop 설치 (https://www.docker.com/products/docker-desktop)
docker --version          # v24.0 이상 확인
docker-compose --version  # v2.0 이상 확인

# 2. 저장소 클론
git clone https://github.com/genonai/doc_parser.git
cd doc_parser
```

---

## 🔑 환경 설정 (1분)

### Step 1: HuggingFace 토큰 발급

1. https://huggingface.co/settings/tokens 방문
2. "New token" 클릭
3. 이름: `doc-parser` 입력
4. Type: `Write` 선택
5. 토큰 복사

### Step 2: .env 파일 생성

```bash
cp .env.example .env
```

`.env` 파일 편집:
```
HF_TOKEN=hf_paste_your_token_here
```

---

## 🚀 서버 실행 (1분)

```bash
# 이미지 빌드 + 컨테이너 실행
docker-compose up -d

# 로그 확인 (실시간)
docker-compose logs -f intelligent-preprocessor

# 서버 준비 완료 대기 (2-3분)
curl http://localhost:7085/healthcheck
# {"status":"ok"} 나오면 준비 완료!
```

---

## ✅ API 호출 테스트 (1분)

### 방법 1: curl 명령어

```bash
# 샘플 파일로 테스트
curl -X POST http://localhost:7085/run \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/workspace/input/pdf_sample.pdf",
    "params": {"log_level": 4}
  }' | jq .
```

### 방법 2: Python

```python
import requests

response = requests.post(
    "http://localhost:7085/run",
    json={
        "file_path": "/workspace/input/pdf_sample.pdf",
        "params": {"log_level": 4}
    }
)

result = response.json()
print(f"처리 상태: {'성공' if result['code'] == 0 else '실패'}")
print(f"청크 수: {len(result['data']) if result['code'] == 0 else 'N/A'}")
```

### 방법 3: 파일 업로드

```bash
curl -X POST http://localhost:7085/upload/run \
  -F "file=@~/Documents/document.pdf" \
  -F "log_level=4"
```

---

## 🎯 주요 명령어

| 명령어 | 설명 |
|--------|------|
| `docker-compose up -d` | 서비스 시작 (백그라운드) |
| `docker-compose down` | 서비스 중지 |
| `docker-compose logs -f` | 실시간 로그 |
| `docker-compose ps` | 상태 확인 |
| `docker-compose restart` | 재시작 |

---

## 📚 다음 단계

자세한 내용은:

1. **[Docker Compose 가이드](./DOCKER_COMPOSE_GUIDE.md)** ← 본격적인 사용
2. **[API 완전 가이드](./genon/preprocessor/INTELLIGENT_API_GUIDE.md)** ← 모든 옵션
3. **[FastAPI 서버 코드](./genon/preprocessor/src/intelligent_main.py)** ← 구현 상세

---

## 🔗 참고 자료

- 📖 [Docling 문서](https://docling-project.github.io/docling/)
- 🤖 [지능형 전처리기 상세](./genon/preprocessor/facade/gitbook_doc/intelligent_processor.md)
- 🐳 [Docker 공식 문서](https://docs.docker.com/)

---

## ❓ 자주 묻는 질문

**Q: 첫 실행이 오래 걸려요**
> A: 맞습니다. 첫 실행 시 모델 다운로드 (5-10GB)와 SDK 설치 (1-2GB)로 5-15분 걸립니다.

**Q: 포트를 바꾸고 싶어요**
> A: `.env` 파일에서 `API_PORT=8080` 으로 변경하세요.

**Q: 메모리가 부족해요**
> A: `.env`에서 `MEMORY_LIMIT=8G`로 감소시키세요.

**Q: HF_TOKEN 없이도 되나요?**
> A: 아니오. HWP/DOCX 변환과 SDK 다운로드에 필수입니다.

**Q: GPU를 사용할 수 있나요?**
> A: docker-compose.yml에서 `runtime: nvidia` 추가 후 `docker run --gpus all` 사용.

---

## 🆘 문제가 생겼을 때

```bash
# 1. 컨테이너 상태 확인
docker-compose ps

# 2. 로그 확인
docker-compose logs intelligent-preprocessor

# 3. 완전히 재시작
docker-compose down -v
docker-compose up -d --build

# 4. 이미지 강제 재빌드
docker system prune -a
docker-compose build --no-cache
docker-compose up -d
```

---

**준비 완료! 🎉**

질문이나 버그 리포트는 [GitHub Issues](https://github.com/genonai/doc_parser/issues)에 올려주세요.
