from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.js"
APPLE_REQUIREMENTS = ROOT / "app" / "requirements-apple-silicon.txt"
STANDARD_REQUIREMENTS = ROOT / "app" / "requirements.txt"
TTS = ROOT / "app" / "tts.py"


class AppleSiliconInstallContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.install = INSTALL.read_text(encoding="utf-8")
        self.apple = APPLE_REQUIREMENTS.read_text(encoding="utf-8")
        self.standard = STANDARD_REQUIREMENTS.read_text(encoding="utf-8")
        self.tts = TTS.read_text(encoding="utf-8")

    def test_install_uses_platform_specific_dependency_branches(self) -> None:
        self.assertIn("platform === 'darwin' && arch === 'arm64'", self.install)
        self.assertIn("requirements-apple-silicon.txt", self.install)
        self.assertIn("!(platform === 'darwin' && arch === 'arm64')", self.install)
        self.assertIn("requirements.txt", self.install)

    def test_apple_branch_does_not_install_qwen_tts(self) -> None:
        apple_branch = self.install.split(
            "when: \"{{platform === 'darwin' && arch === 'arm64'}}\"", 1
        )[1].split(
            "when: \"{{!(platform === 'darwin' && arch === 'arm64')}}\"", 1
        )[0]
        self.assertNotIn("install qwen-tts", apple_branch)
        self.assertIn("uninstall google-genai qwen-tts", apple_branch)

    def test_apple_requirements_select_mlx_compatible_transformers(self) -> None:
        self.assertRegex(self.apple, r"(?m)^mlx-audio==0\.4\.5$")
        self.assertRegex(self.apple, r"(?m)^transformers==5\.12\.1$")
        self.assertRegex(self.apple, r"(?m)^mlx-whisper==0\.4\.3$")
        self.assertRegex(self.apple, r"(?m)^psutil==7\.2\.2$")
        self.assertNotRegex(self.apple, r"(?m)^qwen-tts")


    def test_apple_runtime_defaults_to_local_mlx_when_config_is_missing(self) -> None:
        self.assertIn('default_mode = "local" if apple_silicon else "external"', self.tts)
        self.assertIn('self._mode = tts_config.get("mode", default_mode)', self.tts)
        self.assertIn('and apple_silicon', self.tts)

    def test_standard_requirements_remain_qwen_compatible(self) -> None:
        self.assertRegex(self.standard, r"(?m)^transformers==4\.57\.3$")
        self.assertRegex(self.standard, r"(?m)^psutil==7\.2\.2$")
        non_apple_branch = self.install.split(
            "when: \"{{!(platform === 'darwin' && arch === 'arm64')}}\"", 1
        )[1]
        self.assertIn("uv pip install qwen-tts==0.1.1", non_apple_branch)

    def test_install_declares_ai_bundle(self) -> None:
        self.assertIn('requires: {', self.install)
        self.assertIn('bundle: "ai"', self.install)

    def test_install_keeps_existing_pinokio_structure(self) -> None:
        self.assertGreaterEqual(len(re.findall(r'method: "shell\.run"', self.install)), 4)
        self.assertIn('method: "script.start"', self.install)
        self.assertIn('uri: "torch.js"', self.install)
        self.assertIn('path: "app"', self.install)
        self.assertIn('venv: "env"', self.install)


if __name__ == "__main__":
    unittest.main()
