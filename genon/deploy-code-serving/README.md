# GenOS 코드서빙 배포

코드서빙 `529`를 배포하고 서비스 라우터 `79`의 인증키 기반 `ROUTING_RULE`을
새 리비전으로 전환하는 독립 배포 도구입니다. 다른 배포 스크립트를 import하지 않습니다.

## 최초 준비

```bash
cd genon/deploy-code-serving
cp config.yaml.serving.example config.yaml.serving
uv sync
```

`config.yaml.serving`에 실제 GenOS 접속 정보와 환경변수를 입력합니다. 이 파일은
`.gitignore`에 포함되므로 커밋되지 않습니다. GenOS 비밀번호는 파일 대신
`GENOS_PASSWORD` 환경변수로 주입할 수 있습니다.

## 배포

```bash
# 개발 설정(instance type 3, replica 1, worker 1) + 인증키 1873
uv run python deploy-servicerouter.py --env dev --branch develop

# 운영 설정(instance type 4, replica 2, worker 2) + 인증키 1876
uv run python deploy-servicerouter.py --env prod --branch main
```

특정 커밋을 지정하려면 `--commit`, 흐름만 확인하려면 `--dry-run`을 사용합니다.

배포와 라우팅을 분리할 수도 있습니다.

```bash
uv run python deploy-servicerouter.py --env dev --branch develop --deploy-only
uv run python deploy-servicerouter.py --env dev --route-only --revision REVISION_ID
```

