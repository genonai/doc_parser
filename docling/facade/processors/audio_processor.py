"""Audio Processor for mp3, m4a, wav files"""

import os
import subprocess
from datetime import datetime
from typing import List, Dict, Any
from pydub import AudioSegment
import requests

from docling.facade.models import GenOSVectorMeta
from docling.facade.utils.base import BaseProcessor


class AudioLoader:
    """서부발전전처리기.py의 AudioLoader 클래스"""
    
    def __init__(self, file_path: str, req_url: str, req_data: dict, 
                 chunk_sec: int = 29, tmp_path: str = "./tmp_audios"):
        self.file_path = file_path
        self.req_url = req_url
        self.req_data = req_data
        self.chunk_sec = chunk_sec
        self.tmp_path = tmp_path
        self.file_format = file_path.split('.')[-1]
    
    def split_file_as_chunks(self):
        audio = AudioSegment.from_file(self.file_path, format=self.file_format)
        
        chunk_time = self.chunk_sec * 1000
        
        if len(audio) <= chunk_time:
            return [self.file_path]
        
        audio_chunks = []
        for i in range(0, len(audio), chunk_time):
            chunk = audio[i:i + chunk_time]
            output_file = f"{self.tmp_path}/chunk_{str(i).zfill(5)}.{self.file_format}"
            chunk.export(output_file, format=self.file_format)
            audio_chunks.append(output_file)
        
        return audio_chunks
    
    def transcribe_audio(self, audio_chunks: list):
        transcribed_text_chunks = []
        
        for chunk_path in audio_chunks:
            files = {
                'file': (chunk_path, open(chunk_path, 'rb'), 'application/octet-stream')
            }
            
            resp = requests.post(self.req_url, data=self.req_data, files=files)
            files['file'][1].close()
            
            if resp.status_code == 200:
                file_name = os.path.basename(chunk_path)
                transcribed_text = resp.json()['text']
                transcribed_text_chunks.append({
                    'file_name': file_name,
                    'text': transcribed_text
                })
            else:
                print(f"Error transcribing {chunk_path}: {resp.status_code}")
        
        # Merge transcribed text snippets in order
        transcribed_text_chunks.sort(key=lambda x: x['file_name'])
        transcribed_text = "[AUDIO]" + ' '.join([t['text'] for t in transcribed_text_chunks])
        return transcribed_text
    
    def return_vectormeta_format(self):
        audio_chunks = self.split_file_as_chunks()
        transcribed_text = self.transcribe_audio(audio_chunks)
        res = [GenOSVectorMeta.model_validate({
            'text': transcribed_text,
            'n_char': len(transcribed_text),
            'n_word': len(transcribed_text.split()),
            'n_line': len(transcribed_text.splitlines()),
            'i_page': 0,
            'e_page': 0,
            'i_chunk_on_page': 0,
            'n_chunk_of_page': 1,
            'i_chunk_on_doc': 0,
            'n_chunk_of_doc': 1,
            'n_page': 1,
            'reg_date': datetime.now().isoformat(timespec='seconds') + 'Z',
            'chunk_bboxes': "[]",
            'media_files': "[]"
        })]
        return res


class AudioProcessor(BaseProcessor):
    """Process audio files (mp3, m4a, wav) using Whisper"""
    
    def __init__(self, options: Dict[str, Any] = None, whisper_url: str = None):
        super().__init__()
        self.options = options or {}
        
        # Get Whisper configuration
        whisper_config = self.options.get('whisper', {})
        self.whisper_url = whisper_url or whisper_config.get('url', "http://192.168.74.164:30100/v1/audio/transcriptions")
        self.whisper_options = whisper_config
    
    async def process(self, file_path: str, request: Any = None, **kwargs) -> List[Dict]:
        """Process audio file using Whisper transcription"""
        
        tmp_path = f"./tmp_audios_{os.path.basename(file_path).split('.')[0]}"
        if not os.path.exists(tmp_path):
            os.makedirs(tmp_path)
        
        try:
            # Merge options
            processing_options = {**self.options, **kwargs}
            whisper_config = processing_options.get('whisper', {})
            
            loader = AudioLoader(
                file_path=file_path,
                req_url=self.whisper_url,
                req_data={
                    'model': whisper_config.get('model', 'model'), 
                    'language': whisper_config.get('language', 'ko'),
                    'response_format': whisper_config.get('response_format', 'json'),
                    'temperature': str(whisper_config.get('temperature', 0)),
                    'stream': whisper_config.get('stream', 'false'),
                    'timestamp_granularities[]': whisper_config.get('timestamp_granularities', 'word')
                },
                chunk_sec=whisper_config.get('chunk_sec', 29),
                tmp_path=tmp_path
            )
            
            vector_objs = loader.return_vectormeta_format()
            
            # Convert objects to dict list
            vectors = [v.model_dump() for v in vector_objs]
            return vectors
            
        finally:
            # Cleanup temporary files
            try:
                subprocess.run(['rm', '-r', tmp_path], check=True)
            except:
                pass
    
    def supports(self, file_path: str) -> bool:
        """Check if this processor supports the file"""
        ext = os.path.splitext(file_path)[-1].lower()
        return ext in ('.wav', '.mp3', '.m4a')