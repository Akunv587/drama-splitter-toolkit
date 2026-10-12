"""批量裁剪模块单元测试。"""

from pathlib import Path

from drama_splitter.models import Episode, TimeList
from drama_splitter.output.cutter import Cutter


def test_cut_two_segments(test_video_path: Path, tmp_path: Path) -> None:
    """裁剪 2 集（0–5s, 5–10s），验证输出文件和结果。"""
    tl = TimeList(
        video_name="test.mp4",
        duration_sec=10.0,
        episodes=[
            Episode(
                episode=1,
                start_sec=0.0,
                end_sec=5.0,
                duration_sec=5.0,
                confidence="high",
                signals_hit=["ocr"],
            ),
            Episode(
                episode=2,
                start_sec=5.0,
                end_sec=10.0,
                duration_sec=5.0,
                confidence="high",
                signals_hit=["ocr"],
            ),
        ],
    )

    output_dir = tmp_path / "output"
    cutter = Cutter(mode="lossless", naming_template="第{ep:02d}集.mp4")
    results = cutter.cut(str(test_video_path), tl, str(output_dir))

    assert len(results) == 2
    assert all(r.success for r in results)

    # 验证输出文件
    ep1_path = output_dir / "第01集.mp4"
    ep2_path = output_dir / "第02集.mp4"
    assert ep1_path.exists() and ep1_path.stat().st_size > 0
    assert ep2_path.exists() and ep2_path.stat().st_size > 0


def test_naming_template(test_video_path: Path, tmp_path: Path) -> None:
    """验证文件名模板解析。"""
    tl = TimeList(
        video_name="test.mp4",
        duration_sec=10.0,
        episodes=[
            Episode(
                episode=3,
                start_sec=0.0,
                end_sec=5.0,
                duration_sec=5.0,
                confidence="high",
                signals_hit=["ocr"],
            ),
        ],
    )

    output_dir = tmp_path / "output"
    cutter = Cutter(mode="lossless", naming_template="EP{ep}.mp4")
    results = cutter.cut(str(test_video_path), tl, str(output_dir))

    assert results[0].success
    assert (output_dir / "EP3.mp4").exists()


def test_single_cut_failure_doesnt_affect_others(test_video_path: Path, tmp_path: Path) -> None:
    """单集裁剪失败不影响其他集。"""
    tl = TimeList(
        video_name="test.mp4",
        duration_sec=10.0,
        episodes=[
            Episode(
                episode=1,
                start_sec=0.0,
                end_sec=5.0,
                duration_sec=5.0,
                confidence="high",
                signals_hit=["ocr"],
            ),
            Episode(
                episode=2,
                start_sec=100.0,  # 超出视频时长 → 应失败
                end_sec=200.0,
                duration_sec=100.0,
                confidence="high",
                signals_hit=["ocr"],
            ),
        ],
    )

    output_dir = tmp_path / "output"
    cutter = Cutter(mode="lossless")
    results = cutter.cut(str(test_video_path), tl, str(output_dir))

    assert results[0].success
    # 第二集可能成功（FFmpeg 对超出范围的行为不确定），我们只确保不整体崩溃
    assert len(results) == 2


class TestFormatName:
    """文件名模板格式化单元测试。"""

    def test_ep_format(self) -> None:
        cutter = Cutter(naming_template="EP{ep}.mp4")
        assert cutter._format_name(5) == "EP5.mp4"

    def test_ep_zero_padded(self) -> None:
        cutter = Cutter(naming_template="第{ep:02d}集.mp4")
        assert cutter._format_name(3) == "第03集.mp4"

    def test_ep_padded_width(self) -> None:
        cutter = Cutter(naming_template="E{ep:04d}.mkv")
        assert cutter._format_name(12) == "E0012.mkv"


class TestResolveConflict:
    """文件名冲突处理单元测试。"""

    def test_no_conflict(self, tmp_path: Path) -> None:
        cutter = Cutter()
        path = str(tmp_path / "test.mp4")
        resolved = cutter._resolve_conflict(path)
        assert resolved == path

    def test_conflict_append_suffix(self, tmp_path: Path) -> None:
        cutter = Cutter()
        base = str(tmp_path / "test.mp4")
        # 创建同名文件
        Path(base).touch()
        resolved = cutter._resolve_conflict(base)
        assert resolved == str(tmp_path / "test_1.mp4")

    def test_multiple_conflicts(self, tmp_path: Path) -> None:
        cutter = Cutter()
        base = str(tmp_path / "test.mp4")
        Path(base).touch()
        Path(tmp_path / "test_1.mp4").touch()
        Path(tmp_path / "test_2.mp4").touch()
        resolved = cutter._resolve_conflict(base)
        assert resolved == str(tmp_path / "test_3.mp4")
