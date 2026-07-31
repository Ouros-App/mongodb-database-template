import os
import tempfile
import unittest
from pathlib import Path

from scripts.apply_mongo import load_config


class ApplyMongoTest(unittest.TestCase):
    def test_load_config_expands_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.yaml").write_text("database:\n  host: ${MONGODB_HOST}\n", encoding="utf-8")
            os.environ["MONGODB_HOST"] = "mongo.example.test"
            self.assertEqual(load_config(root)["database"]["host"], "mongo.example.test")


if __name__ == "__main__":
    unittest.main()
