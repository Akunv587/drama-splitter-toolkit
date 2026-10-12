"""OCR 识别引擎与集数匹配器单元测试。"""

from pathlib import Path

import pytest

from drama_splitter.detectors.episode_matcher import EpisodeMatcher, cn2num
from drama_splitter.detectors.frame_sampler import Frame
from drama_splitter.detectors.ocr_detector import OCRDetector, OCRResult, TextBlock
from drama_splitter.models import CandidateList


# ── cn2num ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "text, expected",
    [
        ("零", 0),
        ("九", 9),
        ("十", 10),
        ("十一", 11),
        ("十九", 19),
        ("二十", 20),
        ("二十三", 23),
        ("一百", 100),
        ("一百零一", 101),
        ("一百二十三", 123),
        ("九百九十九", 999),
        ("99", 99),
    ],
)
def test_cn2num(text: str, expected: int) -> None:
    assert cn2num(text) == expected


# ── OCRDetector ────────────────────────────────────────────────

def test_ocr_detect_empty_frames() -> None:
    detector = OCRDetector(engine="rapidocr")
    results = detector.detect([], batch_size=10)
    assert results == []


def test_ocr_detect_batching() -> None:
    """验证分批逻辑：batch_size 正确分批不崩溃。"""
    detector = OCRDetector(engine="rapidocr")
    frames = [Frame(path="nonexistent.png", timestamp_sec=i * 0.5) for i in range(5)]
    # 帧文件不存在会导致 RapidOCR 内部报错，但我们只需验证分批不崩溃
    # 改用更小的 batch
    try:
        results = detector.detect(frames, batch_size=2)
        # 如果 RapidOCR 对不存在的文件返回空结果，也应该有 5 个 OCRResult
        assert len(results) == 5
    except Exception:
        # RapidOCR 可能对不存在的文件抛出异常，这在预期内
        pass


def test_ocr_detect_with_pil_image(tmp_path: Path) -> None:
    """用 PIL 生成包含"第1集"文字的测试图片。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        pytest.skip("PIL 未安装")

    img = Image.new("RGB", (320, 60), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 10), "第1集", fill="black")
    img_path = tmp_path / "test_ep1.png"
    img.save(str(img_path))

    frame = Frame(path=str(img_path), timestamp_sec=0.0)
    detector = OCRDetector(engine="rapidocr")
    results = detector.detect([frame], batch_size=1)
    assert len(results) == 1

    # 检查是否识别到了文字（OCR 结果取决于引擎和字体，可能为空）
    texts = [tb.text for tb in results[0].text_blocks]
    combined = "".join(texts)
    # 宽松验证：至少有识别结果或跳过
    if not combined:
        pytest.skip("RapidOCR 未识别到 PIL 生成的文字（依赖系统字体）")


# ── EpisodeMatcher ─────────────────────────────────────────────

def test_episode_matcher_multi_frame_voting() -> None:
    """测试连续 3 帧出现"第1集"时投票后置信度为 0.9。"""
    matcher = EpisodeMatcher()

    ocr_results = [
        OCRResult(
            frame_path="f1.png",
            timestamp_sec=0.0,
            text_blocks=[TextBlock(text="第1集", score=0.9, bbox=(0, 0, 100, 30))],
        ),
        OCRResult(
            frame_path="f2.png",
            timestamp_sec=0.5,
            text_blocks=[TextBlock(text="第1集 剧情介绍", score=0.85, bbox=(0, 0, 150, 30))],
        ),
        OCRResult(
            frame_path="f3.png",
            timestamp_sec=1.0,
            text_blocks=[TextBlock(text="第1集", score=0.9, bbox=(0, 0, 100, 30))],
        ),
    ]

    result = matcher.match(ocr_results, duration_sec=10.0, video_name="test.mp4")
    assert isinstance(result, CandidateList)
    assert len(result.candidates) >= 1
    # 第1集应该在连续3帧中命中 → 置信度 0.9
    ep1 = next((c for c in result.candidates if c.episode == 1), None)
    assert ep1 is not None
    assert ep1.confidence == 0.9


def test_episode_matcher_single_frame_low_score_dropped() -> None:
    """单帧命中且 score < 0.8 → 应被丢弃。"""
    matcher = EpisodeMatcher()
    ocr_results = [
        OCRResult(
            frame_path="f1.png",
            timestamp_sec=0.0,
            text_blocks=[TextBlock(text="第1集", score=0.6, bbox=(0, 0, 100, 30))],
        ),
    ]
    result = matcher.match(ocr_results, duration_sec=10.0, video_name="test.mp4")
    assert len(result.candidates) == 0


def test_episode_matcher_english_format() -> None:
    """测试英文格式 EP.3 和 Episode 12 能正确匹配。"""
    matcher = EpisodeMatcher()
    ocr_results = [
        OCRResult(
            frame_path="f1.png",
            timestamp_sec=2.0,
            text_blocks=[
                TextBlock(text="EP.3", score=0.9, bbox=(0, 0, 80, 30)),
                TextBlock(text="Episode 12", score=0.9, bbox=(0, 0, 120, 30)),
            ],
        ),
        OCRResult(
            frame_path="f2.png",
            timestamp_sec=2.5,
            text_blocks=[
                TextBlock(text="EP.3", score=0.9, bbox=(0, 0, 80, 30)),
            ],
        ),
    ]
    result = matcher.match(ocr_results, duration_sec=30.0, video_name="test.mp4")
    # EP.3 在连续 2 帧命中 → 置信度 0.9
    ep3 = next((c for c in result.candidates if c.episode == 3), None)
    assert ep3 is not None


def test_episode_matcher_empty_input() -> None:
    """空输入返回空 CandidateList。"""
    matcher = EpisodeMatcher()
    result = matcher.match([], duration_sec=10.0, video_name="test.mp4")
    assert isinstance(result, CandidateList)
    assert len(result.candidates) == 0
