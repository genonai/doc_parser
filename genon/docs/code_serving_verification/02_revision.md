# 묶음 2 — 베이스 이미지 반입, 리비전 생성, 기동 확인

묶음 1 에서 올린 배포본 커밋을 GenOS 코드서빙의 새 리비전으로 올린다. 대조군과 수정본 리비전 두 개를 같은
코드서빙에 순서대로 만들고, 각각 기동·헬스·버전을 확인한다.

GenOS 화면은 같은 git 저장소를 연결한 코드서빙을 둘 만들 수 없다. 그래서 새 코드서빙이 아니라 **기존 코드서빙에
리비전을 추가**하고, 리비전이 바뀔 때마다 묶음 3 의 클라이언트를 한 번씩 돌린다.

## 리비전이 뜨는 방식

리비전은 화면에서 고른 **베이스 이미지**(`template-code-serving-doc-parser:<태그>`)로 파드를 띄우고, 그 안에서
배포본 커밋을 `/app/src/service` 로 clone 한 뒤 `packages/` 의 docling wheel 을 설치하고 `main.py` 를 기동한다.
기동 명령은 베이스 이미지의 `scripts/entrypoint.sh` 이고, 2.2.5 부터는 `uvicorn main:app --workers ${WEB_CONCURRENCY:-5}`
이다. 그래서 워커 수는 리비전 환경변수 `WEB_CONCURRENCY` 로 정한다.

배포본 루트의 `Dockerfile`(`ARG BASE_IMAGE=…`, `CMD … --workers 5`)은 부팅 단계에서 이미지를 빌드하는 사이트용이다.
화면에서 베이스 이미지를 고르는 사이트에서는 쓰이지 않는다. 실제로 쓰인 이미지는 파드 스펙의 image 로 확인한다.

같은 이유로 before·after 비교에서 코드만 다르게 하려면, 두 리비전이 **같은 베이스 이미지·같은 자원·같은 리소스 폴더**를
써야 한다. 배포본 커밋은 베이스 이미지와 무관하므로 이미 올린 배포본 브랜치를 그대로 쓸 수 있다.

## 0. 베이스 이미지를 사이트 레지스트리로 옮긴다 (태그당 1회)

새 베이스 이미지는 본사 레지스트리(`<HQ_REGISTRY>`)에 올라가고, 사이트 클러스터는 사이트 레지스트리(`<SITE_REGISTRY>`)
에서만 이미지를 받는다. 두 레지스트리 사이에 직접 길이 없으면 본사 망과 사이트 배스천 양쪽에 닿는 PC 를 중계로 쓴다.
PC 에 이미지를 내려받지 않고, SSH 역방향 터널로 배스천이 본사 레지스트리를 직접 당기게 한다.

| 자리표시자 | 뜻 |
|---|---|
| `<HQ_REGISTRY>` | 본사 레지스트리 `호스트:포트`. PC 에서 `curl http://<HQ_REGISTRY>/v2/` 가 200 이어야 한다 |
| `<BASTION>` | 사이트 배스천 SSH 호스트. docker 가 있고 사이트 레지스트리에 로그인돼 있어야 한다 |
| `<SITE_REGISTRY>/<PATH>` | 사이트 레지스트리와 경로. 돌고 있는 리비전의 파드 image 에서 확인한다 |

### 0-1. 본사 레지스트리에 태그가 있는지 확인 (PC)

```bash
curl -sS http://<HQ_REGISTRY>/v2/mnc/template-code-serving-doc-parser/tags/list
```

### 0-2. 역방향 터널을 연다 (PC, 이 터미널은 열어 둔다)

배스천의 `localhost:15000` 이 본사 레지스트리로 이어진다. docker 는 `localhost` 레지스트리를 TLS 없이 허용한다.

```bash
ssh -N -R 15000:<HQ_REGISTRY> <BASTION>
```

### 0-3. 배스천에서 당기고, 사이트 경로로 태그를 붙여 올린다 (다른 터미널)

```bash
ssh <BASTION>
```

```bash
docker pull localhost:15000/mnc/template-code-serving-doc-parser:2.2.5
```

```bash
docker tag localhost:15000/mnc/template-code-serving-doc-parser:2.2.5 <SITE_REGISTRY>/<PATH>/template-code-serving-doc-parser:2.2.5
```

```bash
docker push <SITE_REGISTRY>/<PATH>/template-code-serving-doc-parser:2.2.5
```

push 마지막 줄의 `digest: sha256:…` 를 적어 둔다. 끝나면 0-2 터미널을 닫고, 배스천 디스크가 빠듯하면
`docker rmi` 로 두 태그를 지운다(이미지 약 2.3 GB).

### 0-4. GenOS 에 이미지를 등록한다

GenOS 관리자 화면의 **도커 이미지 관리**(리소스 설정) 목록에서 **도커 이미지 생성**을 누르고, 파일은 올리지 않는다.
파일 없이 생성하면 GenOS 가 레지스트리에 그 이름·태그가 있는지 확인한 뒤 바로 `COMPLETED` 로 등록한다.

| 항목 | 넣을 값 |
|---|---|
| 제목(이미지 이름) | `mnc/template-code-serving-doc-parser` — 레지스트리 호스트와 네임스페이스 접두어는 넣지 않는다 |
| 태그 | `2.2.5` |
| 이미지 타입 | **`Code_Serving`**. 이 타입이 아니면 리비전 생성 화면의 이미지 목록에 나타나지 않는다 |
| 관리 그룹 | 코드서빙과 같은 그룹 |
| 파일 업로드 | 비워 둔다(이미 레지스트리에 있다) |

GenOS 는 이미지를 `<레지스트리>/<네임스페이스>/<이름>:<태그>` 로 조합해 띄운다. 네임스페이스는 사이트 admin-api 의
환경변수 `REGISTRY_NAMESPACE` 이고, 돌고 있는 파드의 image 에서 레지스트리 호스트 바로 뒤 경로 한 칸이 그 값이다.
0-3 의 `<PATH>` 는 `<네임스페이스>/mnc` 여야 등록 이름과 맞는다.

등록이 "레지스트리 내에서 찾을 수 없습니다" 로 실패하면 이름에 호스트나 네임스페이스를 넣었거나, 0-3 의 push 경로가
`<네임스페이스>/mnc/…` 가 아니다.

## 설정은 저장소가 아니라 NFS 리소스 폴더에 둔다

배포본 저장소는 공개다. 서빙 ID·API 키·OCR 주소가 들어가는 `model_presets.yaml` 은 저장소에 커밋하지 않고,
사이트 NFS 의 리소스 폴더에 두고 리비전 환경변수 `GENOS_RESOURCE_DIR` 로 가리킨다. 파드는 기동 때 그 폴더의
프로세서 yaml 과 `model_presets.yaml` 을 읽는다.

이번 검증용 폴더는 운영 폴더를 복사해 만들고, 파서 yaml 의 `ocr_mode` 만 `disable` 로 두었다. 두 리비전이 같은
폴더를 가리키므로 "같은 설정, 다른 코드" 비교가 된다.

| 항목 | 값 |
|---|---|
| 리소스 폴더 | `<NFS_RESOURCE_DIR>` (예: `/nfs-root/<site>/<resource-folder>`) |
| 들어 있는 것 | 프로세서 yaml 5종, `custom_field_*.yaml`, 프롬프트, `model_presets.yaml`(값 채움) |
| 바꾼 것 | `parser_processor_config.yaml`, `parser_processor_config_simple.yaml` 의 `ocr.ocr_mode: "disable"` |

`model_presets.yaml` 의 자리표시자 5개(`<LAYOUT_SERVING_ID>`, `<ENRICHMENT_SERVING_ID>`,
`<IMAGE_DESCRIPTION_SERVING_ID>`, `<PAGE_DESCRIPTION_SERVING_ID>`, `<OCR_ENDPOINT>`)가 남아 있으면 PDF 가 레이아웃
호출에서 500 으로 전부 실패한다. 리비전을 만들기 전에 채워져 있는지 확인한다.

## 1. 리비전을 만든다

GenOS 화면에서 대상 코드서빙(`<CODE_SERVING_ID>`)을 열고 **리비전 생성**으로 들어간다.

| 항목 | 대조군(before) | 수정본(after) |
|---|---|---|
| 저장소 | `https://github.com/genonai/doc_parser_code_serving` (외부 GitHub, 기존 연결 그대로) | 같음 |
| 브랜치 / 커밋 | 묶음 1 의 대조군 브랜치·해시 | 묶음 1 의 수정본 브랜치·해시 |
| 이미지 | 0-4 에서 등록한 `template-code-serving-doc-parser:2.2.5` | 같음 |
| 인스턴스 | CPU 1, 메모리 16Gi, replica 1(노드 여유에 맞춘다 — 아래) | 같음 |
| 볼륨 | NFS 루트 볼륨을 `/nfs-root` 로 | 같음 |
| 환경변수 | `GENOS_RESOURCE_DIR=<NFS_RESOURCE_DIR>`, `WEB_CONCURRENCY=5`, 나머지는 돌고 있는 리비전과 동일 | 같음 |

메모리는 워커 수에 비례한다. uvicorn 프로세스 하나가 문서 처리 뒤 최대 약 3 GB 를 쓰므로 워커 5개면 15 GB 안팎이다.
CPU 는 노드 여유가 정한다. 리비전을 만들기 전에 배스천에서 노드별 남은 CPU·메모리를 보고, 자리가 없으면 돌고 있는
리비전을 먼저 내린다(그 자리가 빈다). 워커 5개가 CPU 1 코어를 나눠 쓰면 CPU 구간은 빨라지지 않고 VLM·LLM 대기만
겹친다. 자원을 바꾸면 두 리비전을 같은 값으로 맞춘다.

```bash
kubectl describe nodes | grep -E '^Name:|cpu .*%|memory .*%' | paste - - -
```

생성하면 파드가 베이스 이미지로 뜨고 배포본 커밋을 clone·설치한 뒤 기동한다. 2~5분 걸린다.

## 2. 기동 확인

같은 클러스터의 코드스페이스 터미널에서 코드서빙 서비스에 직접 호출한다. 서비스 이름은
`code-serving-<CODE_SERVING_ID>-<REVISION>` 이고 포트는 8080 이다. 게이트웨이 인증키는 필요 없다.

```bash
curl -sS http://code-serving-<CODE_SERVING_ID>-<REVISION>:8080/health
```

```bash
curl -sS http://code-serving-<CODE_SERVING_ID>-<REVISION>:8080/version
```

`/version` 의 `commit`·`docling` 으로 어느 배포본이 떴는지 확정한다(아래 "실제로 쓴 값" 표). 베이스 이미지와 워커 수는
배스천에서 파드를 보고 확인한다.

```bash
kubectl -n <NAMESPACE> get pod <POD> -o jsonpath='{.spec.containers[0].image}'
```

```bash
kubectl -n <NAMESPACE> exec <POD> -c <CONTAINER> -- sh -lc 'pgrep -fa "uvicorn main:app"'
```

워커가 5개면 uvicorn 부모 1개와 자식 5개가 보인다.

마지막으로 PDF 한 건을 파싱해 레이아웃 서빙 연결을 확인한다. 응답 `code` 가 0 이어야 한다.

```bash
curl -sS -X POST http://code-serving-<CODE_SERVING_ID>-<REVISION>:8080/parser -H 'Content-Type: application/json' -d '{"file_path":"/app/src/service/genon/preprocessor/sample_files/pdf_sample.pdf","params":{}}' | head -c 300
```

## 막혔을 때

| 증상 | 원인과 조치 |
|---|---|
| 0-2 에서 `remote port forwarding failed` | 배스천의 15000 포트가 사용 중이다. 다른 포트로 바꾸고 0-3 의 `localhost:` 포트도 같이 바꾼다 |
| 0-3 `pull` 이 `connection refused` | 0-2 터미널이 닫혔거나 PC 가 본사 레지스트리에 닿지 않는다. 0-1 부터 다시 |
| 0-3 `push` 가 `unauthorized` | 배스천의 사이트 레지스트리 로그인이 만료됐다. `docker login <SITE_REGISTRY>` |
| 리비전 화면에 이미지가 안 보임 | 0-4 의 이미지 타입이 `Code_Serving` 이 아니다 |
| 파드가 `Pending` | 노드 CPU·메모리 부족. 다른 리비전을 내리거나 자원을 줄인다 |
| 파드가 `OOMKilled` 로 재시작 | 워커 수 대비 메모리 부족. 메모리를 올리거나 `WEB_CONCURRENCY` 를 줄인다 |
| `/health` 는 200 인데 `/parser` 가 500, 로그에 `rep/serving/%3CLAYOUT_SERVING_ID%3E` | `model_presets.yaml` 자리표시자가 남아 있다. 채운 뒤 리비전을 다시 만든다(설정은 기동 때 읽는다) |
| 로그에 `PaddleOcrOptions.ocr_endpoint must be set` | 재OCR 경로로 들어갔는데 OCR 주소가 없다. 리소스 폴더의 `ocr_mode` 가 `disable` 인지, 요청에 `ocr_mode: auto` 를 넣지 않았는지 확인 |
| 레이아웃 호출 401 `Bearer token required` | 레이아웃 클라이언트는 키가 비어 있어도 `Authorization` 헤더를 붙인다. `layout` 프리셋에 유효한 `api_key` 가 있어야 한다 |

## 이 검증에서 실제로 쓴 값

| 리비전 | 배포본 브랜치 / 커밋 | `/version` 의 `commit` | `docling` | 베이스 이미지 |
|---|---|---|---|---|
| 리비전 21(사전 측정) | `main` / `19eda41` | `5857a64e`(2.2.4) | `2.41.0+genon.0` | 2.2.3.1 사이트 빌드, 워커 1 |
| 대조군 | `test/develop-base` / `3abe9ac2` | `49d8f176` | `2.41.0+genon.0` | 2.2.5, 워커 5 |
| 수정본 | `test/481-482` / `7239a563` | `28f8b94f` | `2.41.0+genon.1` | 2.2.5, 워커 5 |
