import unittest

from sp500_ai.providers import ticker_for_yahoo


class ProviderTests(unittest.TestCase):
    def test_share_class_symbol_conversion(self):
        self.assertEqual(ticker_for_yahoo("BRK.B"), "BRK-B")


if __name__ == "__main__":
    unittest.main()
