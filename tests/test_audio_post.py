from __future__ import annotations

import importlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class AudioPostTest(unittest.TestCase):
    """音频后期处理与片头/片尾的离线验收。"""

    @classmethod
    def setUpClass(cls):
        import app.main as main
        cls.main = importlib.reload(main)
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="lawflow-audio-post-"))
        cls.main.DATA_DIR = cls.temp_dir / "data"
        cls.main.PROJECTS_DIR = cls.main.DATA_DIR / "projects"
        cls.main.EXPORTS_DIR = cls.main.DATA_DIR / "exports"
        cls.main.DB_PATH = cls.main.DATA_DIR / "app.db"
        cls.main.DEFAULT_EXPORT_DIR = cls.temp_dir / "Desktop" / "ai_law"
        for directory in (cls.main.DATA_DIR, cls.main.PROJECTS_DIR, cls.main.EXPORTS_DIR, cls.main.DATA_DIR / "temp", cls.main.DATA_DIR / "logs"):
            directory.mkdir(parents=True, exist_ok=True)
        cls.main.init_db()
        cls.client = TestClient(cls.main.app)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def make_sine(self, path: Path, duration: int = 1) -> None:
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}", "-y", str(path)], check=True)

    def probe_duration(self, path: Path) -> float:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            check=True, capture_output=True, text=True,
        )
        return float(result.stdout.strip().splitlines()[0])

    def test_post_process_audio_normalizes_sine_and_keeps_duration(self):
        fixture = self.main.DATA_DIR / "post-fixture.mp3"
        self.make_sine(fixture)
        result = self.main.post_process_audio(fixture, {"audio_post_enabled": True})
        self.assertEqual(result, fixture)
        self.assertTrue(result.is_file())
        self.assertAlmostEqual(self.probe_duration(result), 1.0, delta=0.2)

    def test_post_process_audio_disabled_keeps_file_untouched(self):
        fixture = self.main.DATA_DIR / "disabled-fixture.mp3"
        self.make_sine(fixture)
        original = fixture.read_bytes()
        result = self.main.post_process_audio(fixture, {"audio_post_enabled": False})
        self.assertEqual(result, fixture)
        self.assertEqual(result.read_bytes(), original)  # 关闭开关时完全不碰原文件

    def test_post_process_audio_failure_keeps_original_and_records_error(self):
        fixture = self.main.DATA_DIR / "failure-fixture.mp3"
        self.make_sine(fixture)
        original = fixture.read_bytes()
        errors_before = len(list(self.main.RECENT_ERRORS))
        with patch.object(self.main.subprocess, "run", side_effect=subprocess.SubprocessError("模拟 ffmpeg 失败")):
            result = self.main.post_process_audio(fixture, {"audio_post_enabled": True})
        self.assertEqual(result, fixture)
        self.assertEqual(result.read_bytes(), original)  # 失败时保留原文件
        errors = list(self.main.RECENT_ERRORS)
        self.assertGreater(len(errors), errors_before)
        self.assertIn("音频后期处理失败", errors[-1]["message"])

    def test_bookends_are_inserted_before_and_after_chapter_list(self):
        fixture = self.main.DATA_DIR / "bookend-fixture.mp3"
        self.make_sine(fixture)
        chapter = self.main.DATA_DIR / "bookend-chapter.mp3"
        self.make_sine(chapter)
        settings = {"audio_intro_text": "欢迎收听本期节目。", "audio_outro_text": "感谢收听，下期再见。", "tts_provider": "macos_say"}
        with patch.object(self.main, "speech_chunk", return_value=(fixture.read_bytes(), "fixture")):
            merged, temp_files = self.main.with_audio_bookends([("第一章", chapter)], settings)
        try:
            self.assertEqual([heading for heading, _ in merged], ["片头", "第一章", "片尾"])
            self.assertEqual(len(temp_files), 2)
            # 书挡文件已内置 0.4 秒静音：1s 语音 + 0.4s ≈ 1.4s
            self.assertAlmostEqual(self.probe_duration(merged[0][1]), 1.4, delta=0.2)
            self.assertAlmostEqual(self.probe_duration(merged[2][1]), 1.4, delta=0.2)
            output = self.main.DATA_DIR / "bookend-output.m4b"
            self.main.merge_audiobook(merged, None, "书挡测试", output)
            probe = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(output)], check=True, capture_output=True, text=True)
            chapters = json.loads(probe.stdout)["chapters"]
            self.assertEqual(len(chapters), 3)  # 章节列表首尾多出两项
            self.assertEqual(chapters[0]["tags"]["title"], "第1章 片头")
            self.assertEqual(chapters[1]["tags"]["title"], "第2章 第一章")
            self.assertEqual(chapters[2]["tags"]["title"], "第3章 片尾")
        finally:
            for temp_file in temp_files:
                temp_file.unlink(missing_ok=True)

    def test_bookends_skipped_when_texts_empty(self):
        chapter = self.main.DATA_DIR / "no-bookend-chapter.mp3"
        self.make_sine(chapter)
        merged, temp_files = self.main.with_audio_bookends([("第一章", chapter)], {"audio_intro_text": "", "audio_outro_text": ""})
        self.assertEqual([heading for heading, _ in merged], ["第一章"])
        self.assertEqual(temp_files, [])

    def test_direct_whole_article_audio_gets_intro_and_outro(self):
        project = self.client.post("/api/projects", json={"name": "整篇书挡项目", "verification_mode": "source_only"}).json()
        source = self.client.post(
            f"/api/projects/{project['id']}/text-sources",
            json={"title": "整篇素材", "content": "这一段原文内容足够长，可以直接朗读成一份完整的音频文件。"},
        ).json()
        audio = self.client.post(f"/api/projects/{project['id']}/documents/{source['id']}/direct-audio", json={})
        self.assertEqual(audio.status_code, 200)
        audio_id = audio.json()["id"]
        saved = self.client.put("/api/settings/provider", json={
            "audio_post_enabled": True,
            "audio_intro_text": "欢迎收听本期节目。",
            "audio_outro_text": "感谢收听，下期再见。",
            "tts_provider": "macos_say",
        })
        self.assertEqual(saved.status_code, 200)
        fixture = self.main.DATA_DIR / "direct-bookend-fixture.mp3"
        self.make_sine(fixture)
        with patch.object(self.main, "speech_chunk", return_value=(fixture.read_bytes(), "fixture")):
            synthesized = self.client.post(f"/api/audio-outputs/{audio_id}/synthesize", json={})
        self.assertEqual(synthesized.status_code, 200)
        output = self.client.get(f"/api/audio-outputs/{audio_id}").json()
        self.assertEqual(output["status"], "ready")
        # 片头(1s+0.4s 静音) + 正文(1s) + 片尾(0.4s 静音+1s) ≈ 3.8s
        self.assertAlmostEqual(self.probe_duration(Path(output["audio_path"])), 3.8, delta=0.4)

    def test_provider_settings_roundtrip_returns_audio_post_fields(self):
        # 未保存过设置时也返回默认值，前端弹窗可以安全渲染
        with self.main.db() as conn:  # 清掉同类其他用例保存过的设置，从干净状态开始
            conn.execute("DELETE FROM settings WHERE setting_key = 'provider'")
        data = self.client.get("/api/settings/provider").json()
        self.assertTrue(data["audio_post_enabled"])
        self.assertEqual(data["audio_intro_text"], "")
        self.assertEqual(data["audio_outro_text"], "")
        saved = self.client.put("/api/settings/provider", json={
            "audio_post_enabled": False,
            "audio_intro_text": "开场白文案",
            "audio_outro_text": "结尾语文案",
        })
        self.assertEqual(saved.status_code, 200)
        updated = self.client.get("/api/settings/provider").json()
        self.assertFalse(updated["audio_post_enabled"])
        self.assertEqual(updated["audio_intro_text"], "开场白文案")
        self.assertEqual(updated["audio_outro_text"], "结尾语文案")
