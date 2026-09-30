# sites — 고객사이트별 설정과 전용 전처리기

표준 설정은 `genon/preprocessor/resource/` 한 벌이다. 고객사이트마다 달라지는 설정은
`genon/sites/<site>/resource/` 에 **그대로 실행·배포할 수 있는 완성본**으로 git 관리한다.
사이트 전용 전처리기(facade) 코드는 `genon/sites/<site>/facade/` 에 둔다.

이 폴더는 코드서빙 배포본(`build-script/sync-serving-repo.sh` 의 `EXCLUDE_PATHS`)에 포함되지 않는다.
핫픽스 번들은 `--site <site>` 를 줄 때만 `resource/` 완성본을 싣는다.

## 폴더 구조

```
genon/sites/
  dev/
    model_presets.yaml   로컬(VPN) 모델 접속값. 로컬 도구만 쓴다
  <site>/
    manifest.yaml        owned: 이 사이트가 소유하는 파일 목록
    resource/            완성본 설정 폴더 = 표준 사본 + owned 파일
    facade/              사이트 전용 전처리기 코드(선택)
    docs/                사이트 전용 전처리기 매뉴얼(선택)
```

- `resource/` 에서 `owned:` 에 적힌 파일만 사이트가 직접 고친다. 모니모는 `model_presets.yaml`,
  파서 설정 2종(`parser_processor_config.yaml`, `parser_processor_config_simple.yaml`),
  `custom_field_*.yaml` 14종을 소유한다.
- 나머지 파일은 표준의 사본이다. **사본은 직접 고치지 않는다.** 표준을 고친 뒤 `sync-sites.sh` 로 맞춘다.
  사본이 표준과 다르면 `genon/preprocessor/tests/unit/test_sites_sync_unit.py` 가 실패한다.
- owned 파서 설정은 표준이 새 키를 추가해도 자동으로 들어오지 않는다. 표준 파서 설정을 바꿨으면
  사이트 파일에도 같은 변경을 반영한다.

## 사이트 전용 전처리기 (facade/)

기본 전처리기 서비스(`genon/preprocessor/src/main.py`)에 facade 하나로 등록되는 사이트 전용 코드다.
한국은행(`bok`), 삼성증권(`samsung-securities`), 서부발전(`west-power`), 다우기술(`daou`),
코레일(`korail`), OKDS(`okds`)와 법령(`law`), 초기 예시 전처리기(`legacy`)가 있다.

- 등록할 파일 하나가 `preprocessor.py` 로 이름이 바뀌어 마운트된다. 로컬 진입점은 없다.
- `resource/` **안에** 두지 않는다. `sync-sites.sh` 가 표준에 없는 파일을 지운다.
- 코드만 있는 사이트는 `manifest.yaml` 과 `resource/` 를 만들지 않는다. 빈 manifest 는
  `test_sites_sync_unit.py` 를 실패시킨다.
- 활성 facade 와 별도 배포 단위다. 공용 모듈(`genon/preprocessor/processing/`)을 바꿀 때 이 코드까지
  반영해야 하는지는 따로 판단한다.

## 모델 접속 정보

모델·외부 서비스 접속값은 설정 폴더의 `model_presets.yaml` 한 파일에 모여 있다. 메인 설정은
`model_presets_file: model_presets.yaml` 로 이 파일을 읽고, 각 블록은 `model_preset: <이름>` 으로 부른다.

- 표준 `resource/model_presets.yaml` 은 `<...>` 자리표를 담고, 사이트 `resource/model_presets.yaml` 은
  현장 값을 담는다.
- 로컬 실행에서는 환경변수 `GENOS_MODEL_PRESETS_FILE` 이 가리키는 파일이 그 위에 이름별·키 단위로 얹힌다.
  로컬 도구는 이 변수가 비어 있으면 `genon/sites/dev/model_presets.yaml` 로 자동 설정한다.
  **운영에서는 설정하지 않는다.**

## 표준 설정을 바꾼 뒤

```bash
build-script/sync-sites.sh            # manifest.yaml 이 있는 모든 사이트
build-script/sync-sites.sh monimo     # 지정한 사이트만
```

owned 파일을 제외한 모든 파일을 표준과 똑같이 맞추고, 표준에서 지운 파일은 사이트에서도 지운다.
결과로 바뀐 사본은 표준 변경과 같은 커밋에 넣는다.

## 로컬 테스트

| 목적 | 표준 | 모니모 |
|---|---|---|
| 한 건 파싱·청킹 | `parse_chunk_test.py 입력 출력` | `parse_chunk_test.py --site monimo 입력 출력` |
| 검증 케이스 일괄 | `parse_chunk_verify.sh --resource-dir ../../resource` | `parse_chunk_verify.sh` (기본값) |
| 서버 기동(`main.py`, 포트 7084) | `build-script/run-local.sh` | `build-script/run-local.sh monimo` |
| 설정 점검(파싱·LLM 없음) | `precheck_custom_fields.sh` | `precheck_custom_fields.sh <저장소>/sites/monimo/resource` |

- `parse_chunk_*` 는 `genon/preprocessor/examples/parse_chunk/` 에서, `precheck_custom_fields.sh` 는
  `genon/preprocessor/examples/config_precheck/` 에 있다. `parse_chunk_test.py` 는
  `genon/preprocessor/.venv/bin/python` 으로 실행한다(`parse_chunk_test.sh` 는 인자를 받지 않는 예제 모음이다).
- `parse_chunk_verify.sh` 는 표 표기형태를 검증하기 위해 청커 설정의 임시 사본에서 `table_text_formats` 를 켠다.
  저장소의 설정 파일은 바뀌지 않는다.
- `run-local.sh` 는 `GENOS_RESOURCE_DIR` 로 설정 폴더를 지정해 루트 `main.py` 를 기동한다.
  `GENOS_RESOURCE_DIR` 는 로컬 전용이다. 기동 후 호출은
  `genon/preprocessor/examples/code_serving/serving_gateway_test.sh` 로 한다.

## 배포

`genon/sites/<site>/resource/` 를 그대로 전달한다.

- 코드서빙: 배포본의 `genon/preprocessor/resource/` 를 이 폴더로 덮어쓴다.
- 기본 전처리기 서비스: 이 폴더를 MinIO 리소스로 올린다.
- 핫픽스 번들: `build-script/create-patch-bundle.sh <이름> --site <site>` 로 만들면 번들의 `resource/` 가 이 폴더다.
- 사이트 전용 전처리기는 `genon/sites/<site>/facade/` 의 해당 파일을 별도로 전달한다. 코드서빙 배포본과
  핫픽스 번들 어디에도 포함되지 않는다.

## 사이트 추가

1. `genon/sites/<site>/manifest.yaml` 을 만들고 `owned:` 에 사이트가 소유할 파일을 적는다(`genon/sites/monimo/manifest.yaml` 참고).
2. `build-script/sync-sites.sh <site>` 로 표준 사본을 채운다.
3. owned 파일(최소 `model_presets.yaml`)을 `genon/sites/<site>/resource/` 에 두고 현장 값으로 채운다.
4. 위 로컬 테스트로 확인한다.
