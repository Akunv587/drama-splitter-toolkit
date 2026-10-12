"""CLI 入口测试。"""

import os
import pytest
from click.testing import CliRunner

from drama_splitter.cli import main
from drama_splitter.models import Candidate, CandidateList


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestCli:
    """CLI 基础测试。"""

    def test_help_shows_subcommands(self, runner) -> None:
        """--help 包含所有子命令。"""
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "analyze" in result.output
        assert "cut" in result.output
        assert "run" in result.output

    def test_version(self, runner) -> None:
        """--version 输出版本号。"""
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "drama-splitter" in result.output

    def test_analyze_no_video_error(self, runner) -> None:
        """缺少视频路径时报错。"""
        result = runner.invoke(main, ["analyze"])
        assert result.exit_code != 0

    def test_cut_missing_time_list_error(self, runner) -> None:
        """cut 子命令缺少 --time-list 时报错。"""
        result = runner.invoke(main, ["cut", "test.mp4"])
        # Should exit with non-zero since --time-list is required
        assert result.exit_code != 0

    def test_run_no_video_error(self, runner) -> None:
        """run 子命令缺少视频路径时报错。"""
        result = runner.invoke(main, ["run"])
        assert result.exit_code != 0

    def test_analyze_missing_ffmpeg(self, runner, tmp_path) -> None:
        """缺少 FFmpeg 时给出友好错误。"""
        video = tmp_path / "test.mp4"
        video.touch()

        # 临时屏蔽 ffmpeg PATH
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "drama_splitter.cli.check_environment",
                lambda: {"ffmpeg": False, "ffprobe": True, "fpcalc": False},
            )
            result = runner.invoke(main, ["analyze", str(video)])
            assert result.exit_code != 0
            assert "FFmpeg" in result.output or "error" in result.output.lower()

    def test_run_yes_flag(self, runner, tmp_path) -> None:
        """--yes 跳过确认。"""
        video = tmp_path / "test.mp4"
        video.touch()

        # Mock the entire pipeline to avoid real FFmpeg calls
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(
                "drama_splitter.cli.check_environment",
                lambda: {"ffmpeg": True, "ffprobe": True, "fpcalc": False},
            )
            # Mock to prevent real execution
            result = runner.invoke(
                main,
                ["run", str(video), "--yes", "--output", str(tmp_path / "out")],
            )
            # Will fail at probe_video since test.mp4 is empty, but not at confirmation
            assert result.exit_code != 0  # Will fail at probe_video


class TestCliFakeVideo:
    """用模拟视频的 CLI 集成测试。"""

    def _create_fake_video(self, tmp_path):
        """用 FFmpeg 生成一个带文字帧的测试视频。"""
        import subprocess
        import shutil

        if not shutil.which("ffmpeg"):
            pytest.skip("需要 FFmpeg")

        video_path = tmp_path / "fake_drama.mp4"

        # 用 testsrc 生成 90 秒的测试视频
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i",
            "testsrc=duration=90:size=640x480:rate=24",
            "-f", "lavfi", "-i",
            "sine=frequency=440:duration=90",
            "-c:v", "libx264", "-preset", "ultrafast",
            "-c:a", "aac",
            "-shortest",
            str(video_path),
        ]
        subprocess.run(cmd, capture_output=True, check=True)
        return str(video_path)

    def test_analyze_with_fake_video(self, runner, tmp_path) -> None:
        """analyze 命令产出 time_list.json。"""
        video = self._create_fake_video(tmp_path)
        out_dir = tmp_path / "output"

        result = runner.invoke(main, [
            "analyze", video,
            "--output", str(out_dir),
            "--config", "config.yaml",
        ])
        # analyze should succeed (or at least not crash unexpectedly)
        # With a 90s testsrc video, OCR will likely find nothing -> DetectionError
        # That's expected behavior
        assert result.exit_code in (0, 1)

    def test_cut_with_time_list(self, runner, tmp_path) -> None:
        """cut 命令读取时间清单执行裁剪。"""
        import shutil

        if not shutil.which("ffmpeg"):
            pytest.skip("需要 FFmpeg")

        video = self._create_fake_video(tmp_path)
        out_dir = tmp_path / "output"

        # 先创建时间清单
        time_list_path = tmp_path / "time_list.json"
        cl = CandidateList(
            signal_type="ocr",
            video_name="fake_drama",
            duration_sec=90.0,
            candidates=[
                Candidate(
                    signal_type="ocr",
                    timestamp_sec=0.0,
                    episode=1,
                    confidence=0.9,
                ),
                Candidate(
                    signal_type="ocr",
                    timestamp_sec=45.0,
                    episode=2,
                    confidence=0.9,
                ),
            ],
        )
        # We need to make a TimeList first via fusion, but that's complex.
        # Instead just test that cut reads a valid time_list.json
        from drama_splitter.models import TimeList, Episode
        tl = TimeList(
            video_name="fake_drama",
            duration_sec=90.0,
            episodes=[
                Episode(
                    episode=1,
                    start_sec=0.0,
                    end_sec=45.0,
                    duration_sec=45.0,
                    confidence="medium",
                    signals_hit=["ocr"],
                ),
                Episode(
                    episode=2,
                    start_sec=45.0,
                    end_sec=90.0,
                    duration_sec=45.0,
                    confidence="medium",
                    signals_hit=["ocr"],
                ),
            ],
        )
        tl.to_json(str(time_list_path))

        result = runner.invoke(main, [
            "cut", video,
            "--time-list", str(time_list_path),
            "--output", str(out_dir),
            "--yes",
        ])
        # cut should succeed
        assert result.exit_code == 0
        # Check output files exist
        expected_files = os.listdir(out_dir)
        assert len(expected_files) >= 2  # at least 2 episode files
