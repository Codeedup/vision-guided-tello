"""Hash mismatch/exclusive-file tests using local fake downloads only."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from model_asset import fetch_model, verified_model


class TestModelAsset(unittest.TestCase):
    def test_missing_model_has_no_implicit_download(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                verified_model(Path(directory) / 'missing.task')

    def test_reject_download_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.task'
            opener = Mock(return_value=io.BytesIO(b'wrong model'))
            with self.assertRaises(ValueError):
                fetch_model(path, opener)
            self.assertFalse(path.exists())

    def test_existing_wrong_model_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'model.task'
            path.write_bytes(b'preserve')
            opener = Mock(side_effect=AssertionError('Unexpected download'))
            with self.assertRaises(ValueError):
                fetch_model(path, opener)
            self.assertEqual(path.read_bytes(), b'preserve')


if __name__ == '__main__':
    unittest.main()
