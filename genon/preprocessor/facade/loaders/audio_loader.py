import logging
import math
import os
import threading
import uuid
from glob import glob
from pathlib import Path

from .base_loader import BaseLoader

_log = logging.getLogger(__name__)

try:
    import pydub
except ImportError:
    pydub = None

try:
    import requests
except ImportError:
    requests = None

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}


class AudioLoader(BaseLoader):
    """
    오디오 파일을 Whisper API로 STT 처리하여 전사 텍스트를 반환.

    config 예시:
        {
            "req_url": "http://whisper-service/api",
            "req_data": {"language": "ko"},
            "chunk_sec": 29,
        }

    load() 반환값: "[AUDIO] 전사된 텍스트 ..."
    """

    def __init__(self, config: dict) -> None:
        self.req_url: str = config["req_url"]
        self.req_data: dict = config.get("req_data", {})
        self.chunk_sec: int = config.get("chunk_sec", 29)

    def load(self, file_path: str) -> str:
        assert pydub is not None, "AudioLoader requires pydub: pip install pydub"
        assert requests is not None, "AudioLoader requires requests: pip install requests"

        ext = Path(file_path).suffix.lower()
        assert ext in SUPPORTED_EXTENSIONS, f"AudioLoader가 지원하지 않는 확장자: {ext}"

        tmp_path = os.path.join("/tmp", str(uuid.uuid4()))
        os.makedirs(tmp_path, exist_ok=True)

        try:
            chunk_files = self._split_into_chunks(file_path, tmp_path)
            return self._transcribe(chunk_files)
        finally:
            import shutil
            shutil.rmtree(tmp_path, ignore_errors=True)

    def _split_into_chunks(self, file_path: str, tmp_path: str) -> list[str]:
        audio = pydub.AudioSegment.from_file(file_path)
        chunk_ms = self.chunk_sec * 1000
        n_chunks = math.ceil(len(audio) / chunk_ms)

        for i in range(n_chunks):
            start = i * chunk_ms
            overlap_start = start - 300 if start > 0 else start
            chunk = audio[overlap_start : start + chunk_ms]
            chunk.export(os.path.join(tmp_path, f"chunk_{i:04d}.wav"), format="wav")

        return sorted(glob(os.path.join(tmp_path, "*.wav")))

    def _transcribe(self, chunk_files: list[str]) -> str:
        results: list[dict] = []
        lock = threading.Lock()

        def _request(file_path: str) -> None:
            with open(file_path, "rb") as f:
                files = {"file": (file_path, f, "audio/wav")}
                resp = requests.post(self.req_url, data=self.req_data, files=files)
            text = resp.json().get("text", "")
            with lock:
                results.append({"file": os.path.basename(file_path), "text": text})

        threads = [threading.Thread(target=_request, args=(fp,)) for fp in chunk_files]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        results.sort(key=lambda x: x["file"])
        return "[AUDIO] " + " ".join(r["text"] for r in results)
