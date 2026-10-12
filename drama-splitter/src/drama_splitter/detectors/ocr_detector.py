"""OCR 识别引擎封装。

对抽帧模块产出的帧图片做文字识别，返回文本块及其位置信息。
"""

import logging
from dataclasses import dataclass, field
from typing import List

from drama_splitter.detectors.frame_sampler import Frame

logger = logging.getLogger(__name__)


@dataclass
class TextBlock:
    """单个识别文本块。"""

    text: str
    """识别到的文本。"""
    score: float
    """置信度分数（0–1）。"""
    bbox: tuple[int, int, int, int]
    """文本框坐标 (x1, y1, x2, y2)。"""


@dataclass
class OCRResult:
    """单帧 OCR 识别结果。"""

    frame_path: str
    timestamp_sec: float
    text_blocks: list[TextBlock] = field(default_factory=list)


class OCRDetector:
    """OCR 识别引擎。

    Args:
        engine: ``"rapidocr"`` 或 ``"paddleocr"``。
        region: 区域过滤，``"top"`` / ``"center"`` / ``"bottom"`` 或 ``None``（全图）。
    """

    def __init__(self, engine: str = "rapidocr", region: str | None = None) -> None:
        self.engine = engine
        self.region = region
        self._ocr = self._init_engine(engine)

    def _init_engine(self, engine: str):
        """初始化 OCR 引擎实例。"""
        if engine == "rapidocr":
            from rapidocr_onnxruntime import RapidOCR
            return RapidOCR()
        elif engine == "paddleocr":
            try:
                from paddleocr import PaddleOCR
                return PaddleOCR(lang="ch")
            except ImportError:
                raise ImportError(
                    "PaddleOCR 未安装。请运行: pip install paddleocr paddlepaddle"
                )
        else:
            raise ValueError(f"未知 OCR 引擎: {engine}，可选 'rapidocr' 或 'paddleocr'")

    def detect(
        self, frames: list[Frame], batch_size: int = 500
    ) -> list[OCRResult]:
        """对帧列表做 OCR。

        Args:
            frames: 待识别帧列表。
            batch_size: 分批大小，避免内存峰值。

        Returns:
            OCR 识别结果列表。
        """
        results: list[OCRResult] = []
        total = len(frames)

        for batch_start in range(0, total, batch_size):
            batch = frames[batch_start : batch_start + batch_size]
            for i, frame in enumerate(batch):
                global_idx = batch_start + i
                if global_idx % 100 == 0:
                    logger.info("OCR 进度: %d / %d", global_idx, total)

                text_blocks = self._detect_single(frame.path)
                results.append(
                    OCRResult(
                        frame_path=frame.path,
                        timestamp_sec=frame.timestamp_sec,
                        text_blocks=text_blocks,
                    )
                )

        logger.info("OCR 完成: 共处理 %d 帧", total)
        return results

    def _detect_single(self, image_path: str) -> list[TextBlock]:
        """对单张图片做 OCR 并返回文本块列表。"""
        raw_result = self._ocr(image_path)
        if raw_result is None:
            return []

        blocks: list[TextBlock] = []
        # RapidOCR 返回 (boxes, txts, scores) 或 (result, None)
        if isinstance(raw_result, tuple) and len(raw_result) >= 2:
            boxes = raw_result[0]
            if boxes is None:
                return []
            # boxes 可能是 list[tuple] 或 list[list]
            # RapidOCR onnxruntime 返回格式: [([[x1,y1],[x2,y2],[x3,y3],[x4,y4]], text, score), ...]
            for item in boxes:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    bbox_data = item[0]
                    text = item[1]
                    score = item[2] if len(item) > 2 else 1.0

                    # 提取边界框 (x1, y1, x2, y2)
                    if isinstance(bbox_data, (list, tuple)) and len(bbox_data) >= 4:
                        points: list[tuple[float, float]] = []
                        for pt in bbox_data[:4]:
                            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                                points.append((float(pt[0]), float(pt[1])))
                        if len(points) >= 2:
                            x1 = int(min(p[0] for p in points))
                            y1 = int(min(p[1] for p in points))
                            x2 = int(max(p[0] for p in points))
                            y2 = int(max(p[1] for p in points))
                            blocks.append(
                                TextBlock(text=str(text), score=float(score), bbox=(x1, y1, x2, y2))
                            )

        return self._filter_by_region(blocks)

    def _filter_by_region(self, blocks: list[TextBlock]) -> list[TextBlock]:
        """按指定区域过滤文本块。"""
        if self.region is None:
            return blocks

        if not blocks:
            return blocks

        # 需要图片高度来判断区域，从第一个块做粗略估算
        all_y = [b.bbox[1] for b in blocks] + [b.bbox[3] for b in blocks]
        if not all_y:
            return blocks
        img_height = max(all_y)

        filtered: list[TextBlock] = []
        for b in blocks:
            center_y = (b.bbox[1] + b.bbox[3]) / 2
            if self.region == "top" and center_y <= img_height / 3:
                filtered.append(b)
            elif self.region == "center" and img_height / 3 < center_y <= 2 * img_height / 3:
                filtered.append(b)
            elif self.region == "bottom" and center_y > 2 * img_height / 3:
                filtered.append(b)

        return filtered
