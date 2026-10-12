"""文件管理器

管理上传文件的保存、输出目录创建、任务与文件的关联关系。
"""

import os
import re
import shutil
import logging

logger = logging.getLogger(__name__)


class FileManager:
    """文件管理器"""

    def __init__(self, upload_dir: str = "uploads", output_dir: str = "outputs"):
        """初始化，创建目录。

        Args:
            upload_dir: 上传文件目录
            output_dir: 输出文件目录
        """
        self.upload_dir = upload_dir
        self.output_dir = output_dir
        os.makedirs(upload_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """清理特殊字符，防止路径遍历攻击。

        替换 / \\ : * ? " < > | 为 _

        Args:
            name: 原始名称

        Returns:
            清理后的安全名称
        """
        return re.sub(r'[\/\\:*?"<>|]', '_', name)

    def save_upload(self, file_path: str, task_id: str) -> str:
        """将上传的文件保存到 {upload_dir}/{task_id}/ 目录下。

        Args:
            file_path: 原始文件路径
            task_id: 任务 ID

        Returns:
            保存后的绝对路径

        Raises:
            FileNotFoundError: 源文件不存在时抛出
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"源文件不存在: {file_path}")

        task_dir = os.path.join(self.upload_dir, task_id)
        os.makedirs(task_dir, exist_ok=True)

        filename = os.path.basename(file_path)
        dest_path = os.path.join(task_dir, filename)

        # 处理重名：添加序号后缀
        counter = 1
        base, ext = os.path.splitext(filename)
        while os.path.exists(dest_path):
            dest_path = os.path.join(task_dir, f"{base}_{counter}{ext}")
            counter += 1

        shutil.copy2(file_path, dest_path)
        logger.info(f"文件已保存: {file_path} → {dest_path}")

        return os.path.abspath(dest_path)

    def create_output_dir(self, drama_name: str) -> str:
        """为剧集创建输出目录 {output_dir}/{drama_name}/。

        Args:
            drama_name: 剧名（会经过特殊字符清理）

        Returns:
            输出目录路径
        """
        safe_name = self._sanitize_name(drama_name)
        if not safe_name:
            safe_name = "unknown"

        output_path = os.path.join(self.output_dir, safe_name)
        os.makedirs(output_path, exist_ok=True)
        logger.info(f"输出目录: {output_path}")

        return output_path

    def get_output_path(
        self, output_dir: str, episode_num: int, drama_name: str
    ) -> str:
        """生成输出文件路径。

        Args:
            output_dir: 输出目录
            episode_num: 集数
            drama_name: 剧名

        Returns:
            完整输出路径: {output_dir}/{drama_name}_S01E{episode_num:02d}.mp4
        """
        safe_name = self._sanitize_name(drama_name)
        filename = f"{safe_name}_S01E{episode_num:02d}.mp4"
        return os.path.join(output_dir, filename)

    def cleanup_task_files(self, task_id: str) -> None:
        """清理指定任务的上传临时文件。

        Args:
            task_id: 任务 ID
        """
        task_dir = os.path.join(self.upload_dir, task_id)
        if os.path.exists(task_dir):
            shutil.rmtree(task_dir)
            logger.info(f"已清理任务文件: {task_dir}")

    def get_task_files(self, task_id: str) -> dict:
        """获取指定任务的所有上传文件信息。

        Args:
            task_id: 任务 ID

        Returns:
            {'video': path | None, 'audio': path | None, 'subtitle': path | None}
        """
        task_dir = os.path.join(self.upload_dir, task_id)
        result: dict = {"video": None, "audio": None, "subtitle": None}

        if not os.path.exists(task_dir):
            return result

        for fname in os.listdir(task_dir):
            fpath = os.path.join(task_dir, fname)
            if not os.path.isfile(fpath):
                continue

            lower = fname.lower()
            if lower.endswith('.mp4'):
                result["video"] = os.path.abspath(fpath)
            elif lower.endswith('.mp3'):
                result["audio"] = os.path.abspath(fpath)
            elif lower.endswith(('.txt', '.vtt')):
                result["subtitle"] = os.path.abspath(fpath)

        return result

    def check_disk_space(self, output_dir: str, estimated_size: int) -> bool:
        """检查磁盘可用空间是否充足。

        Args:
            output_dir: 输出目录路径
            estimated_size: 预估输出总大小（字节）

        Returns:
            True 表示空间充足，False 表示空间不足
        """
        try:
            usage = shutil.disk_usage(output_dir)
            return usage.free >= estimated_size
        except Exception:
            # 无法获取磁盘信息时，不阻止操作
            return True
