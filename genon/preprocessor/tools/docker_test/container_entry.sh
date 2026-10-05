#!/usr/bin/env bash
# 테스트 컨테이너(docker/Dockerfile.test)의 진입점. 호스트에서 직접 실행하지 않는다.
#
# 1) /cache volume 에 테스트 자산이 없으면 받는다(이미 있으면 건너뛴다). 단계는 CI(develop.yml)와 같다.
#      - NLTK 데이터 2종
#      - HWP SDK (HWP_SDK_TOKEN 필요. 없으면 경고만 남기고 HWP SDK 경로 테스트는 실패하거나 skip 된다)
#      - MNCAI 커스텀 레이아웃 모델
#    그 밖의 docling 모델은 테스트가 처음 사용할 때 HF_HOME(/cache)에 자동으로 받는다.
# 2) 인자를 pytest 에 그대로 넘긴다. 인자가 없으면 unit+smoke+regression 전체를 실행한다.
set -euo pipefail

mkdir -p "$HOME" "$HF_HOME" "$NLTK_DATA"

if [ ! -d "$NLTK_DATA/tokenizers/punkt_tab" ] || [ ! -d "$NLTK_DATA/taggers/averaged_perceptron_tagger_eng" ]; then
  echo "[INFO] NLTK 데이터 다운로드"
  python -c "import nltk, os; d=os.environ['NLTK_DATA']; [nltk.download(p, download_dir=d, quiet=True) for p in ('punkt_tab', 'averaged_perceptron_tagger_eng')]"
fi

if [ "$(uname -m)" != "x86_64" ]; then
  # HWP SDK(convtext)는 x86-64 전용 바이너리다. 다른 아키텍처에서는 연결하지 않아
  # docling 이 SDK 부재로 판정하게 한다(볼륨에 받아 둔 SDK 가 있어도 쓰지 않는다).
  echo "[WARN] $(uname -m) 에서는 HWP SDK 를 쓰지 않는다. HWP SDK 경로 검증은 linux/amd64 에서 실행한다."
  export HWP_SDK_DIR=/nonexistent/hwp_sdk
elif [ ! -x "$HWP_SDK_DIR/convtext" ]; then
  if [ -n "${HWP_SDK_TOKEN:-}" ]; then
    echo "[INFO] HWP SDK 다운로드"
    mkdir -p "$HWP_SDK_DIR"
    huggingface-cli download genon-search/hwp_sdk --repo-type dataset \
      --local-dir "$HWP_SDK_DIR" --token "$HWP_SDK_TOKEN" >/dev/null
    chmod +x "$HWP_SDK_DIR/convtext"
  else
    echo "[WARN] HWP_SDK_TOKEN 이 없어 HWP SDK 를 받지 않았다. HWP SDK 경로 테스트는 통과하지 못한다."
  fi
fi

LAYOUT_MARK="$HF_HOME/.mncai_custom_layout_ready"
if [ ! -f "$LAYOUT_MARK" ]; then
  echo "[INFO] MNCAI 커스텀 레이아웃 모델 다운로드"
  python -c "from docling.models.layout_model import LayoutModel; from docling.datamodel.layout_model_specs import MNCAI_CUSTOM_LAYOUT; LayoutModel.download_models(layout_model_config=MNCAI_CUSTOM_LAYOUT)"
  touch "$LAYOUT_MARK"
fi

if [ "$#" -eq 0 ]; then
  set -- tests -m "unit or smoke or regression" -q -ra --color=no -p no:warnings
fi

# x86-64 가 아니면 HWP SDK 가 없어 반드시 실패하는 테스트를 제외한다(이 테스트들은 SDK 부재 시
# skip 하지 않는다). --deselect 는 node id 접두사로 맞춘다. HWP SDK 경로는 linux/amd64 에서 검증한다.
if [ "$(uname -m)" != "x86_64" ]; then
  for id in \
    tests/smoke/test_hwp_smoke.py \
    tests/smoke/test_hwpx_smoke.py \
    tests/smoke/test_parser_processor_smoke.py::test_hwp_smoke \
    "tests/smoke/test_intelligent_processor_smoke.py::TestIntelligentProcessorSmoke::test_load_documents[hwpx_sample.hwpx]" \
    "tests/smoke/test_intelligent_processor_smoke.py::TestIntelligentProcessorSmoke::test_chunk_generation_with_real_files[hwpx_sample.hwpx]" \
    tests/regression/test_hwp_regression.py::test_hwp_regression_recursive \
    tests/regression/test_hwp_table_structure_regression.py::TestAttachmentHwpTableStructure
  do
    set -- "$@" --deselect "$id"
  done
  echo "[WARN] $(uname -m): HWP SDK 가 필요한 테스트를 제외하고 실행한다."
fi
exec python -m pytest "$@"
