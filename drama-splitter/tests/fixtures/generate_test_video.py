"""测试视频合成脚本。

使用 FFmpeg + PIL 生成包含多集标题卡的测试视频：
- 3 段不同颜色纯色画面
- 每段开头叠加"第X集"标题卡
- 段间插入 1 秒黑屏静音
- 输出 ~33 秒、3 集的 MP4 文件
"""

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

# 视频参数
FPS = 30
WIDTH = 640
HEIGHT = 360
EPISODE_DURATION = 10  # 每集时长（秒）
BLACK_DURATION = 1     # 集间黑屏（秒）
TOTAL_EPISODES = 3

# 输出路径
OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = OUTPUT_DIR / "test_drama.mp4"


def _run_ffmpeg(cmd: list[str], description: str = "") -> None:
    """运行 FFmpeg 命令。"""
    print(f"  [{description}] {' '.join(cmd[:6])}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  FFmpeg 错误: {result.stderr[-500:]}")
        sys.exit(1)


def generate_title_image(text: str, bg_color: str, path: str) -> None:
    """使用 PIL 生成带文字的标题卡图片。"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("请安装 Pillow: pip install Pillow")
        sys.exit(1)

    img = Image.new("RGB", (WIDTH, HEIGHT), bg_color)
    draw = ImageDraw.Draw(img)

    # 尝试使用系统字体
    font = None
    font_candidates = [
        "C:/Windows/Fonts/simhei.ttf",   # Windows 黑体
        "C:/Windows/Fonts/msyh.ttc",     # Windows 微软雅黑
        "/System/Library/Fonts/PingFang.ttc",  # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
    ]
    for fp in font_candidates:
        try:
            font = ImageFont.truetype(fp, 48)
            break
        except (OSError, IOError):
            continue

    if font is None:
        font = ImageFont.load_default()

    # 居中绘制文字
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (WIDTH - text_w) // 2
    y = (HEIGHT - text_h) // 2 - 20
    draw.text((x, y), text, fill="white", font=font)

    # 底部副标题
    try:
        small_font = ImageFont.truetype(font_candidates[0], 24)
    except (OSError, IOError, IndexError):
        small_font = ImageFont.load_default()
    sub = "短剧分集测试视频"
    sbbox = draw.textbbox((0, 0), sub, font=small_font)
    sw = sbbox[2] - sbbox[0]
    draw.text(((WIDTH - sw) // 2, y + 60), sub, fill="white", font=small_font)

    img.save(path)
    print(f"  生成标题卡: {path}")


def generate_test_video() -> None:
    """生成完整的测试视频。"""
    print("=== 生成测试视频 ===\n")

    colors = ["#4CAF50", "#2196F3", "#FF9800"]  # 绿、蓝、橙

    with TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        concat_file = tmp_path / "concat.txt"
        segment_paths: list[str] = []

        for ep in range(1, TOTAL_EPISODES + 1):
            color = colors[ep - 1]
            print(f"\n--- 第 {ep} 集 ---")

            # 1) 生成标题卡
            title_path = str(tmp_path / f"title_{ep}.png")
            generate_title_image(f"第{ep}集", color, title_path)

            # 2) 生成纯色背景视频片段
            seg_path = str(tmp_path / f"ep_{ep}.mp4")
            _run_ffmpeg([
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c={color}:s={WIDTH}x{HEIGHT}:d={EPISODE_DURATION}:r={FPS}",
                "-f", "lavfi",
                "-i", f"anullsrc=r=44100:cl=stereo",
                "-shortest",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                seg_path,
            ], description=f"生成第{ep}集背景")

            # 3) 叠加标题卡（前 3 秒显示）
            overlay_path = str(tmp_path / f"ep_{ep}_overlay.mp4")
            _run_ffmpeg([
                "ffmpeg", "-y",
                "-i", seg_path,
                "-i", title_path,
                "-filter_complex",
                f"[0:v][1:v]overlay=0:0:enable='between(t,0,3)'[v]",
                "-map", "[v]",
                "-map", "0:a",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                overlay_path,
            ], description=f"叠加第{ep}集标题")

            segment_paths.append(overlay_path)

            # 4) 生成集间黑屏
            if ep < TOTAL_EPISODES:
                black_path = str(tmp_path / f"black_{ep}.mp4")
                _run_ffmpeg([
                    "ffmpeg", "-y",
                    "-f", "lavfi",
                    "-i", f"color=c=black:s={WIDTH}x{HEIGHT}:d={BLACK_DURATION}:r={FPS}",
                    "-f", "lavfi",
                    "-i", "anullsrc=r=44100:cl=stereo",
                    "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    black_path,
                ], description=f"生成黑屏 {ep}")
                segment_paths.append(black_path)

        # 5) 拼接所有片段
        print("\n--- 拼接片段 ---")
        with open(concat_file, "w") as f:
            for sp in segment_paths:
                f.write(f"file '{sp}'\n")

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        _run_ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(OUTPUT_FILE),
        ], description="拼接最终视频")

    # 验证
    size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"\n=== 完成 ===")
    print(f"输出文件: {OUTPUT_FILE}")
    print(f"文件大小: {size_mb:.1f}MB")
    print(f"预期集数: {TOTAL_EPISODES}")
    print(f"预期时长: ~{TOTAL_EPISODES * EPISODE_DURATION + (TOTAL_EPISODES - 1) * BLACK_DURATION}s")


if __name__ == "__main__":
    generate_test_video()
