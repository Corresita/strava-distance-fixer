import unittest
from unittest.mock import patch, MagicMock, call
import time
import sys
import os

os.environ.setdefault("ACCESS_TOKEN", "fake_token")
os.environ.setdefault("REFRESH_TOKEN", "fake_refresh")

import app as strava_app


def make_response(status_code, json_data):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data
    r.text = str(json_data)
    return r


class TestDistanceFormula(unittest.TestCase):
    def _calc(self, original_km):
        n = int(original_km)
        return float(f"{n}.{n:02d}")

    def test_common_distances(self):
        cases = [
            (5.73,  5.05),
            (10.1,  10.10),
            (13.5,  13.13),
            (14.7,  14.14),
            (21.3,  21.21),
            (42.6,  42.42),
        ]
        for original, expected in cases:
            with self.subTest(original=original):
                self.assertAlmostEqual(self._calc(original), expected, places=5)

    def test_already_correct(self):
        self.assertAlmostEqual(self._calc(13.13), 13.13, places=5)
        self.assertAlmostEqual(self._calc(5.05), 5.05, places=5)


class TestFixDistance(unittest.TestCase):
    def setUp(self):
        strava_app.token_store["access_token"] = "fake_token"
        strava_app.token_store["expires_at"] = time.time() + 9999

    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_normal_fix(self, mock_get, mock_put, mock_sleep):
        # GET activity, then verify GET returns updated distance
        mock_get.side_effect = [
            make_response(200, {"distance": 13500.0}),
            make_response(200, {"distance": 13130.0}),  # verify: stuck
        ]
        mock_put.return_value = make_response(200, {})

        strava_app.fix_distance(12345)

        mock_put.assert_called_once()
        put_args = mock_put.call_args
        self.assertAlmostEqual(put_args.kwargs["json"]["distance"], 13130.0, places=1)
        print("  [PASS] 13.50 km -> 13.13 km")

    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_already_correct_no_update(self, mock_get, mock_put, mock_sleep):
        mock_get.return_value = make_response(200, {"distance": 13130.0})

        strava_app.fix_distance(12345)

        mock_put.assert_not_called()
        print("  [PASS] already correct, no update")

    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_under_1km_skipped(self, mock_get, mock_put, mock_sleep):
        mock_get.return_value = make_response(200, {"distance": 800.0})

        strava_app.fix_distance(12345)

        mock_put.assert_not_called()
        print("  [PASS] < 1 km skipped, not zeroed")

    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_retry_when_no_distance(self, mock_get, mock_put, mock_sleep):
        # first 2 GETs: distance=0, 3rd: activity ready, 4th: verify stuck
        mock_get.side_effect = [
            make_response(200, {"distance": 0}),
            make_response(200, {"distance": 0}),
            make_response(200, {"distance": 14700.0}),
            make_response(200, {"distance": 14140.0}),  # verify: stuck
        ]
        mock_put.return_value = make_response(200, {})

        strava_app.fix_distance(12345)

        self.assertEqual(mock_get.call_count, 4)
        mock_put.assert_called_once()
        put_args = mock_put.call_args
        self.assertAlmostEqual(put_args.kwargs["json"]["distance"], 14140.0, places=1)
        print("  [PASS] retry 2x then succeed, 14.70 km -> 14.14 km")

    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_strava_reverts_distance(self, mock_get, mock_put, mock_sleep):
        # Strava reverts first 2 times, then accepts on 3rd
        mock_get.side_effect = [
            make_response(200, {"distance": 23600.0}),   # initial GET
            make_response(200, {"distance": 23600.0}),   # verify: reverted!
            make_response(200, {"distance": 23600.0}),   # retry GET
            make_response(200, {"distance": 23600.0}),   # verify: reverted again
            make_response(200, {"distance": 23600.0}),   # retry GET
            make_response(200, {"distance": 23230.0}),   # verify: stuck!
        ]
        mock_put.return_value = make_response(200, {})

        strava_app.fix_distance(12345)

        self.assertEqual(mock_put.call_count, 3)
        print("  [PASS] Strava reverted 2x, succeeded on 3rd attempt")

    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_401_no_retry(self, mock_get, mock_put, mock_sleep):
        mock_get.return_value = make_response(401, {"message": "Authorization Error"})

        strava_app.fix_distance(12345)

        self.assertEqual(mock_get.call_count, 1)
        mock_put.assert_not_called()
        print("  [PASS] 401 auth error -> give up immediately, no retry")

    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_give_up_after_max_retries(self, mock_get, mock_put, mock_sleep):
        mock_get.return_value = make_response(200, {"distance": 0})

        strava_app.fix_distance(12345)

        self.assertEqual(mock_get.call_count, 5)
        mock_put.assert_not_called()
        print("  [PASS] gave up after 5 retries")

    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_manual_entry_14_70(self, mock_get, mock_put, mock_sleep):
        """模拟手动键入 14.70 km 的场景"""
        mock_get.side_effect = [
            make_response(200, {"distance": 14700.0}),
            make_response(200, {"distance": 14140.0}),  # verify: stuck
        ]
        mock_put.return_value = make_response(200, {})

        strava_app.fix_distance(18419099294)

        mock_put.assert_called_once()
        result_m = mock_put.call_args.kwargs["json"]["distance"]
        self.assertAlmostEqual(result_m, 14140.0, places=1)
        print(f"  [PASS] 手动 14.70 km -> {result_m/1000} km")


if __name__ == "__main__":
    print("\n=== Strava Fixer Tests ===\n")
    loader = unittest.TestLoader()
    loader.sortTestMethodsUsing = None
    suite = loader.loadTestsFromTestCase(TestDistanceFormula)
    suite.addTests(loader.loadTestsFromTestCase(TestFixDistance))
    runner = unittest.TextTestRunner(verbosity=0, stream=sys.stdout)
    result = runner.run(suite)
    print()
    if result.wasSuccessful():
        print("所有测试通过 ✓")
    else:
        print(f"失败: {len(result.failures)} 个, 错误: {len(result.errors)} 个")
    sys.exit(0 if result.wasSuccessful() else 1)
