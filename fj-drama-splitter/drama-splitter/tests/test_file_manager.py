"""文件管理模块单元测试"""

import pytest
import sys
import os
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from modules.file_manager import FileManager


class TestFileManagerSanitize:
    """测试剧名清理"""

    def test_normal_name(self):
        result = FileManager._sanitize_name("MyDrama")
        assert result == "MyDrama"

    def test_special_chars(self):
        """特殊字符被替换为下划线"""
        result = FileManager._sanitize_name("test/\\:*?\"<>|name")
        assert result == "test_________name"

    def test_chinese_name(self):
        result = FileManager._sanitize_name("我的短剧")
        assert result == "我的短剧"

    def test_empty_name(self):
        result = FileManager._sanitize_name("")
        assert result == ""


class TestFileManagerBasic:
    """测试文件管理基本功能"""

    def setup_method(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.upload_dir = os.path.join(self.tmp_dir, "uploads")
        self.output_dir = os.path.join(self.tmp_dir, "outputs")
        self.fm = FileManager(
            upload_dir=self.upload_dir,
            output_dir=self.output_dir
        )

    def teardown_method(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_directories_created(self):
        """上传和输出目录被自动创建"""
        assert os.path.exists(self.upload_dir)
        assert os.path.exists(self.output_dir)

    def test_save_upload(self):
        """文件保存到正确目录"""
        # 创建测试文件
        test_file = os.path.join(self.tmp_dir, "test.mp4")
        with open(test_file, 'w') as f:
            f.write("test content")

        saved_path = self.fm.save_upload(test_file, "task-001")
        assert os.path.exists(saved_path)
        assert "task-001" in saved_path
        assert os.path.basename(saved_path) == "test.mp4"

    def test_save_upload_duplicate_name(self):
        """重名文件添加后缀"""
        test_file = os.path.join(self.tmp_dir, "test.mp4")
        with open(test_file, 'w') as f:
            f.write("content A")

        saved_1 = self.fm.save_upload(test_file, "task-001")

        # 创建另一个同名文件但不同内容
        test_file2 = os.path.join(self.tmp_dir, "test2.mp4")
        with open(test_file2, 'w') as f:
            f.write("content B")

        # 手动复制到相同路径模拟冲突
        task_dir = os.path.join(self.upload_dir, "task-001")
        os.makedirs(task_dir, exist_ok=True)
        shutil.copy(test_file2, os.path.join(task_dir, "test.mp4"))

        # 再次保存同名文件
        saved_2 = self.fm.save_upload(test_file, "task-001")
        # 第二次保存的文件应带后缀
        assert saved_1 != saved_2
        assert "test_1" in os.path.basename(saved_2) or os.path.basename(saved_2) != "test.mp4"

    def test_create_output_dir(self):
        """输出目录创建"""
        output_path = self.fm.create_output_dir("My Drama!")
        assert os.path.exists(output_path)
        # 感叹号不替换
        assert "My Drama!" in output_path

    def test_create_output_dir_special_chars(self):
        """特殊字符剧名被清理"""
        output_path = self.fm.create_output_dir("test/name")
        assert os.path.exists(output_path)
        assert "test_name" in output_path
        assert "/" not in output_path

    def test_get_output_path(self):
        """输出文件路径生成"""
        path = self.fm.get_output_path("/output/MyDrama", 3, "MyDrama")
        assert path.endswith("MyDrama_S01E03.mp4")

    def test_get_task_files_empty(self):
        """空任务的返回"""
        files = self.fm.get_task_files("nonexistent")
        assert files == {"video": None, "audio": None, "subtitle": None}

    def test_get_task_files(self):
        """获取任务文件"""
        task_dir = os.path.join(self.upload_dir, "task-002")
        os.makedirs(task_dir, exist_ok=True)

        # 创建测试文件
        with open(os.path.join(task_dir, "video.mp4"), 'w') as f:
            f.write("video")
        with open(os.path.join(task_dir, "audio.mp3"), 'w') as f:
            f.write("audio")
        with open(os.path.join(task_dir, "subtitle.vtt"), 'w') as f:
            f.write("subtitle")

        files = self.fm.get_task_files("task-002")
        assert files["video"] is not None
        assert files["audio"] is not None
        assert files["subtitle"] is not None

    def test_cleanup_task_files(self):
        """清理任务文件"""
        task_dir = os.path.join(self.upload_dir, "task-003")
        os.makedirs(task_dir, exist_ok=True)
        with open(os.path.join(task_dir, "test.txt"), 'w') as f:
            f.write("test")

        self.fm.cleanup_task_files("task-003")
        assert not os.path.exists(task_dir)
