# 청크 bbox 시각화

파서 산출 JSON 의 청크 bounding box 를 원본 PDF 페이지 이미지 위에 그린다. 레이아웃 판정이
어디서 어긋났는지(문단이 잘렸는지, 표가 통째로 잡혔는지)를 눈으로 확인할 때 쓴다.

입력은 `facade/test.py` 가 남기는 `result.json` 처럼 `bbox` 와 `page` 를 가진 청크 목록이다.

```bash
python visualization.py \
  --result-json <파싱결과.json> \
  --pdf-path <원본.pdf> \
  --output-path ./results
```

`--pdf-path` 를 생략하면 `--pdf-folder`(기본값 현재 디렉터리)에서 결과 JSON 의 파일명과
맞는 PDF 를 찾는다. `--render-scale` 은 렌더 배율이고 1.0 이 72dpi 에 해당한다.

주의 — 모듈 상단의 `DEFAULT_RESULT_JSON` · `DEFAULT_PDF_PATH` 는 예전 작업 컨테이너의
개인 경로(`/workspace/...`)가 그대로 박혀 있어 지금은 존재하지 않는다. 인자를 반드시 준다.
