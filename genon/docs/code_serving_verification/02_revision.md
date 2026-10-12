# 묶음 2 — 코드서빙 리비전 생성과 기동 확인

묶음 1 에서 올린 배포본 커밋을 GenOS 코드서빙의 새 리비전으로 올린다. 대조군(develop)과 수정본(#481+#482)
리비전 두 개를 같은 코드서빙에 순서대로 만들고, 각각 기동·헬스·버전을 확인한다.

GenOS 화면은 같은 git 저장소를 연결한 코드서빙을 둘 만들 수 없다. 그래서 새 코드서빙이 아니라 **기존 코드서빙에
리비전을 추가**하고, 리비전이 바뀔 때마다 묶음 3 의 클라이언트를 한 번씩 돌린다.

## 설정은 저장소가 아니라 NFS 리소스 폴더에 둔다

배포본 저장소는 공개다. 서빙 ID·API 키·OCR 주소가 들어가는 `model_presets.yaml` 은 저장소에 커밋하지 않고,
사이트 NFS 의 리소스 폴더에 두고 리비전 환경변수 `GENOS_RESOURCE_DIR` 로 가리킨다. 파드는 기동 때 그 폴더의
프로세서 yaml 과 `model_presets.yaml` 을 읽는다.

이번 검증용 폴더는 운영 폴더를 복사해 만들고, 파서 yaml 의 `ocr_mode` 만 `disable` 로 두었다. 리비전 두 개가
같은 폴더를 가리키므로 "같은 설정, 다른 코드" 비교가 된다.

| 항목 | 값 |
|---|---|
| 리소스 폴더 | `<NFS_RESOURCE_DIR>` (예: `/nfs-root/<site>/<resource-folder>`) |
| 들어 있는 것 | 프로세서 yaml 5종, `custom_field_*.yaml`, 프롬프트, `model_presets.yaml`(값 채움) |
| 바꾼 것 | `parser_processor_config.yaml`, `parser_processor_config_simple.yaml` 의 `ocr.ocr_mode: "disable"` |

`model_presets.yaml` 의 자리표시자 5개(`<LAYOUT_SERVING_ID>`, `<ENRICHMENT_SERVING_ID>`,
`<IMAGE_DESCRIPTION_SERVING_ID>`, `<PAGE_DESCRIPTION_SERVING_ID>`, `<OCR_ENDPOINT>`)가 남아 있으면 PDF 가 레이아웃
호출에서 500 으로 전부 실패한다. 리비전을 만들기 전에 채워져 있는지 확인한다.

## 절차 — 리비전 하나

GenOS 화면에서 대상 코드서빙(`<CODE_SERVING_ID>`)을 열고 **리비전 생성**으로 들어간다. 돌고 있는 리비전의
설정을 그대로 두고 아래 항목만 바꾼다.

| 항목 | 대조군 리비전 | 수정본 리비전 |
|---|---|---|
| 저장소 | `https://github.com/genonai/doc_parser_code_serving` (외부 GitHub, 기존 연결 그대로) | 같음 |
| 브랜치 | `test/develop-base` | `test/481-482` |
| 커밋 | 묶음 1 의 6번 해시 | 묶음 1 표의 수정본 해시 |
| 이미지 | 돌고 있는 리비전과 동일 | 같음 |
| 인스턴스 | 돌고 있는 리비전과 동일(CPU 1, 메모리 8Gi, replica 1) | 같음 |
| 볼륨 | NFS 루트 볼륨을 `/nfs-root` 로 | 같음 |
| 환경변수 | `GENOS_RESOURCE_DIR=<NFS_RESOURCE_DIR>`, 나머지는 돌고 있는 리비전과 동일 | 같음 |

생성하면 파드가 base 이미지로 뜨고, 배포본 커밋을 clone 한 뒤 `packages/` 의 docling wheel 을 설치하고
`main.py` 를 기동한다. 2~5분 걸린다.

## 기동 확인

같은 클러스터의 코드스페이스 터미널에서 코드서빙 서비스에 직접 호출한다. 서비스 이름은
`code-serving-<CODE_SERVING_ID>-<REVISION>` 이고 포트는 8080 이다. 게이트웨이 인증키는 필요 없다.

```bash
curl -sS http://code-serving-<CODE_SERVING_ID>-<REVISION>:8080/health
```

```bash
curl -sS http://code-serving-<CODE_SERVING_ID>-<REVISION>:8080/version
```

`/version` 응답으로 어느 빌드가 떴는지 확정한다.

| 필드 | 대조군 | 수정본 |
|---|---|---|
| `commit` | develop 커밋(묶음 1 의 1번) | `28f8b94f…` |
| `docling` | `2.41.0+genon.0` | `2.41.0+genon.1` |

마지막으로 PDF 한 건을 파싱해 레이아웃 서빙 연결을 확인한다. 응답 `code` 가 0 이어야 한다.

```bash
curl -sS -X POST http://code-serving-<CODE_SERVING_ID>-<REVISION>:8080/parser -H 'Content-Type: application/json' -d '{"file_path":"/app/src/service/genon/preprocessor/sample_files/pdf_sample.pdf","params":{}}' | head -c 300
```

## 막혔을 때

| 증상 | 원인과 조치 |
|---|---|
| 파드가 `Pending` | 노드 CPU 부족. 인스턴스를 줄이거나 다른 리비전을 내린다 |
| `/health` 는 200 인데 `/parser` 가 500, 로그에 `rep/serving/%3CLAYOUT_SERVING_ID%3E` | `model_presets.yaml` 자리표시자가 남아 있다. 채운 뒤 리비전을 다시 만든다(설정은 기동 때 읽는다) |
| 로그에 `PaddleOcrOptions.ocr_endpoint must be set` | 재OCR 경로로 들어갔는데 OCR 주소가 없다. 리소스 폴더의 `ocr_mode` 가 `disable` 인지, 요청에 `ocr_mode: auto` 를 넣지 않았는지 확인 |
| 레이아웃 호출 401 `Bearer token required` | 레이아웃 클라이언트는 키가 비어 있어도 `Authorization` 헤더를 붙인다. `layout` 프리셋에 유효한 `api_key` 가 있어야 한다 |

## 이 검증에서 실제로 쓴 값

| 리비전 | 배포본 브랜치 / 커밋 | `/version` 의 `commit` | `docling` |
|---|---|---|---|
| 대조군 | `test/develop-base` / `3abe9ac2` | `49d8f176` | `2.41.0+genon.0` |
| 수정본 | `test/481-482` / `7239a563` | `28f8b94f` | `2.41.0+genon.1` |
