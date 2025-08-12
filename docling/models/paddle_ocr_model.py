import logging
from collections.abc import Iterable
from pathlib import Path
from typing import Optional, Type

import numpy
from docling_core.types.doc import BoundingBox, CoordOrigin
from docling_core.types.doc.page import BoundingRectangle, TextCell

from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.base_models import Page
from docling.datamodel.document import ConversionResult
from docling.datamodel.pipeline_options import (
    OcrOptions,
    PaddleOcrOptions,
)
from docling.datamodel.settings import settings
from docling.models.base_ocr_model import BaseOcrModel
from docling.utils.accelerator_utils import decide_device
from docling.utils.profiling import TimeRecorder

_log = logging.getLogger(__name__)

import itertools, io
import grpc
import docling.datamodel.ocr_pb2 as ocr_pb2
import docling.datamodel.ocr_pb2_grpc as ocr_pb2_grpc
import io
from PIL import Image

class PaddleOcrModel(BaseOcrModel):
    def __init__(
        self,
        enabled: bool,
        artifacts_path: Optional[Path],
        options: PaddleOcrOptions,
        accelerator_options: AcceleratorOptions,
    ):
        super().__init__(
            enabled=enabled,
            artifacts_path=artifacts_path,
            options=options,
            accelerator_options=accelerator_options,
        )
        self.options: PaddleOcrOptions

        self.scale = 1

        if self.enabled:
            # try:
            #     from paddleocr import PaddleOCR
            # except ImportError:
            #     raise ImportError(
            #         "PaddleOCR is not installed. Please install it via `pip install paddleocr` to use this OCR engine. "
            #         "Alternatively, Docling has support for other OCR engines. See the documentation."
            #     )

            # Decide the accelerator devices
            device = decide_device(accelerator_options.device)
            use_cuda = str(AcceleratorDevice.CUDA.value).lower() in device
            use_dml = accelerator_options.device == AcceleratorDevice.AUTO
            intra_op_num_threads = accelerator_options.num_threads

            # self.reader = PaddleOCR(
            #     lang=self.options.lang[0],
            #     use_doc_orientation_classify=self.options.use_doc_orientation_classify,
            #     use_doc_unwarping=self.options.use_doc_unwarping,
            #     use_textline_orientation=self.options.use_textline_orientation,
            #     cpu_threads=intra_op_num_threads,
            #     text_rec_score_thresh=self.options.text_score,
            #     text_detection_model_dir=self.options.det_model_dir,
            #     text_detection_model_name=self.options.det_model_name,
            #     text_recognition_model_dir=self.options.rec_model_dir,
            #     text_recognition_model_name=self.options.rec_model_name,
            # )

            PORTS = [50051 + i for i in range(self.options.grpc_server_count)]
            # print(f"Connecting to gRPC servers on ports: {PORTS}")
            channels = [grpc.insecure_channel(f"localhost:{p}") for p in PORTS]
            stubs = [(ocr_pb2_grpc.OCRServiceStub(ch), p) for ch, p in zip(channels, PORTS)]
            self.rr = itertools.cycle(stubs)


    def __call__(
        self, conv_res: ConversionResult, page_batch: Iterable[Page]
    ) -> Iterable[Page]:
        if not self.enabled:
            yield from page_batch
            return

        for page in page_batch:
            assert page._backend is not None
            if not page._backend.is_valid():
                yield page
            else:
                with TimeRecorder(conv_res, "ocr"):
                    ocr_rects = self.get_ocr_rects(page)

                    all_ocr_cells = []
                    for ocr_rect in ocr_rects:
                        # Skip zero area boxes
                        if ocr_rect.area() == 0:
                            continue
                        high_res_image = page._backend.get_page_image(
                            scale=self.scale, cropbox=ocr_rect
                        )
                        im = numpy.array(high_res_image)

                        # result = self.reader.predict(
                        #     im
                        # )
                        result = self.perform_ocr_with_grpc(im)

                        del high_res_image
                        del im

                        if result is not None:# and len(result) > 0:
                            cells = [
                                TextCell(
                                    index=ix,
                                    text=line["text"],
                                    orig=line["text"],
                                    confidence=line["confidence"],
                                    from_ocr=True,
                                    rect=BoundingRectangle.from_bounding_box(
                                        BoundingBox.from_tuple(
                                            coord=(
                                                (line["box"][0] / self.scale)
                                                + ocr_rect.l,
                                                (line["box"][1] / self.scale)
                                                + ocr_rect.t,
                                                (line["box"][2] / self.scale)
                                                + ocr_rect.l,
                                                (line["box"][3] / self.scale)
                                                + ocr_rect.t,
                                            ),
                                            origin=CoordOrigin.TOPLEFT,
                                        )
                                    ),
                                )
                                # for ix, (text, score, box) in enumerate(zip(result[0]["rec_texts"], result[0]["rec_scores"], result[0]["rec_boxes"]))
                                for ix, line in enumerate(result)
                            ]
                            all_ocr_cells.extend(cells)

                    # Post-process the cells
                    self.post_process_cells(all_ocr_cells, page)

                # DEBUG code:
                if settings.debug.visualize_ocr:
                    self.draw_ocr_rects_and_cells(conv_res, page, ocr_rects)

                yield page


    def perform_ocr_with_grpc(self, im):
        # 이미지를 메모리에서 바이너리로 변환
        img_byte_arr = io.BytesIO()
        pil_image = Image.fromarray(im)  # numpy 배열을 PIL 이미지로 변환
        pil_image.save(img_byte_arr, format='PNG')
        img_byte_arr = img_byte_arr.getvalue()  # 이미지 데이터를 바이너리로 추출

        # # gRPC 서버와 연결
        # channel = grpc.insecure_channel('localhost:50051')  # 서버 주소
        # stub = ocr_pb2_grpc.OCRServiceStub(channel)

        # # OCR 요청: 이미지 데이터를 바이너리로 전송
        # response = stub.PerformOCR(ocr_pb2.OCRRequest(image_data=img_byte_arr))

        req = ocr_pb2.OCRRequest(image_data=img_byte_arr)
        stub, port = next(self.rr)  # 라운드 로빈 방식으로 스텁 선택
        # print(f"[gRPC] call → {port}")
        response = stub.PerformOCR(req)

        # 결과 출력
        ocr_results = []
        for result in response.results:
            text = result.text
            confidence = result.confidence
            box = result.box
            ocr_results.append({
                'text': text,
                'confidence': confidence,
                'box': box
            })
        return ocr_results


    @classmethod
    def get_options_type(cls) -> Type[OcrOptions]:
        return PaddleOcrOptions
