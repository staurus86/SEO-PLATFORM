import unittest

from app.config import settings


class RuntimeResourceDefaultsTests(unittest.TestCase):
    def test_playwright_browsers_path_default_is_ms_playwright(self):
        self.assertEqual(settings.PLAYWRIGHT_BROWSERS_PATH, "/ms-playwright")


if __name__ == "__main__":
    unittest.main()
