import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


@unittest.skipUnless(os.name == "nt" or sys.platform == "darwin", "supported desktop OS")
class SingleInstanceTest(unittest.TestCase):
    def test_duplicate_skips_app_startup_and_crash_releases_mutex(self):
        name = "Local\\pet-Lapland.test." + uuid4().hex
        # Launch the interpreter directly so killing the owner simulates a crash.
        python = getattr(sys, "_base_executable", sys.executable)
        command = [python, "-c"]
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = {**os.environ, "HOME": directory.name}
        setup = "import sys; import pet.main as pet; pet.INSTANCE_MUTEX = sys.argv[1]\n"
        cwd = Path(__file__).resolve().parents[1]
        owner_code = setup + "assert pet.claim_instance(); print('ready', flush=True); sys.stdin.readline()"
        with subprocess.Popen(command + [owner_code, name], cwd=cwd, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True, env=env) as owner:
            try:
                self.assertEqual(owner.stdout.readline().strip(), "ready")
                duplicate = setup + "assert pet.main() == 0; assert 'pet.app' not in sys.modules"
                subprocess.run(command + [duplicate, name], cwd=cwd, env=env, check=True, timeout=10)
            finally:
                owner.kill()
                owner.wait(timeout=10)
        subprocess.run(command + [setup + "assert pet.claim_instance()", name], cwd=cwd, env=env, check=True, timeout=10)
