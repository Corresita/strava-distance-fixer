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

    @patch("app.fix_distance_web")
    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_strava_reverts_distance(self, mock_get, mock_put, mock_sleep, mock_web):
        # API 路径下 Strava 连续回滚 2 次后切换到 web 表单
        mock_get.side_effect = [
            make_response(200, {"distance": 23600.0}),   # initial GET
            make_response(200, {"distance": 23600.0}),   # verify: reverted!
            make_response(200, {"distance": 23600.0}),   # retry GET
            make_response(200, {"distance": 23600.0}),   # verify: reverted again
            make_response(200, {"distance": 23600.0}),   # retry GET → 走 web
        ]
        mock_put.return_value = make_response(200, {})
        mock_web.return_value = True

        strava_app.fix_distance(12345)

        self.assertEqual(mock_put.call_count, 2)
        mock_web.assert_called_once()
        print("  [PASS] Strava reverted 2x, switched to web form")

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

    @patch("app.fix_distance_web")
    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_gps_activity_routes_to_web(self, mock_get, mock_put, mock_sleep, mock_web):
        """GPS 活动（有 start_latlng、manual=False）应直接走 web 表单，不打 API PUT"""
        mock_get.return_value = make_response(200, {
            "distance": 13500.0,
            "manual": False,
            "start_latlng": [37.7749, -122.4194],
        })
        mock_web.return_value = True

        strava_app.fix_distance(12345)

        mock_put.assert_not_called()
        mock_web.assert_called_once()
        web_args = mock_web.call_args
        self.assertEqual(web_args.args[0], 12345)
        self.assertAlmostEqual(web_args.args[1], 13130.0, places=1)
        print("  [PASS] GPS 活动直接走 web 表单，跳过 API")

    @patch("app.fix_distance_web")
    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_manual_activity_uses_api(self, mock_get, mock_put, mock_sleep, mock_web):
        """手动活动（manual=True）应走 API PUT，不调用 web 表单"""
        mock_get.side_effect = [
            make_response(200, {
                "distance": 13500.0,
                "manual": True,
                "start_latlng": [],
            }),
            make_response(200, {"distance": 13130.0}),  # verify: stuck
        ]
        mock_put.return_value = make_response(200, {})

        strava_app.fix_distance(12345)

        mock_put.assert_called_once()
        mock_web.assert_not_called()
        print("  [PASS] manual=True 活动走 API")

    @patch("app.fix_distance_web")
    @patch("app.time.sleep")
    @patch("app.requests.put")
    @patch("app.requests.get")
    def test_no_start_latlng_uses_api(self, mock_get, mock_put, mock_sleep, mock_web):
        """没有 start_latlng 的活动（即使 manual 未声明）应走 API PUT"""
        mock_get.side_effect = [
            make_response(200, {
                "distance": 13500.0,
                "start_latlng": [],
            }),
            make_response(200, {"distance": 13130.0}),  # verify: stuck
        ]
        mock_put.return_value = make_response(200, {})

        strava_app.fix_distance(12345)

        mock_put.assert_called_once()
        mock_web.assert_not_called()
        print("  [PASS] 无 GPS 坐标的活动走 API")


EDIT_PAGE_HTML = """
<html>
  <head><meta name="csrf-token" content="csrf-from-edit-page"></head>
  <body>
    <form id="edit-activity" action="/activities/12345">
      <input name="authenticity_token" value="form-token">
      <input name="activity[distance]" value="13.50">
      <input name="activity[name]" value="Morning Run">
    </form>
  </body>
</html>
"""


def make_html_response(status_code, html):
    r = MagicMock()
    r.status_code = status_code
    r.text = html
    return r


class TestFixDistanceWeb(unittest.TestCase):
    def setUp(self):
        strava_app.token_store["access_token"] = "fake_token"
        strava_app.token_store["expires_at"] = time.time() + 9999

    def _build_session_mock(self):
        session = MagicMock()
        session.headers = {}
        session.get.return_value = make_html_response(200, EDIT_PAGE_HTML)
        session.post.return_value = make_response(200, {})
        return session

    @patch("app.time.sleep")
    @patch("app.get_web_session")
    @patch("app.requests.get")
    def test_metric_user_submits_km(self, mock_api_get, mock_web_sess, mock_sleep):
        mock_api_get.side_effect = [
            make_response(200, {"measurement_preference": "meters"}),  # athlete
            make_response(200, {"distance": 13130.0}),                  # verify
        ]
        session = self._build_session_mock()
        mock_web_sess.return_value = session

        strava_app.fix_distance_web(12345, 13130.0)

        post_data = session.post.call_args.kwargs["data"]
        self.assertAlmostEqual(float(post_data["activity[distance]"]), 13.13, places=2)
        print("  [PASS] 公制用户提交 km")

    @patch("app.time.sleep")
    @patch("app.get_web_session")
    @patch("app.requests.get")
    def test_imperial_user_submits_miles(self, mock_api_get, mock_web_sess, mock_sleep):
        mock_api_get.side_effect = [
            make_response(200, {"measurement_preference": "feet"}),    # athlete: imperial
            make_response(200, {"distance": 13130.0}),                  # verify (stored in m)
        ]
        session = self._build_session_mock()
        mock_web_sess.return_value = session

        strava_app.fix_distance_web(12345, 13130.0)

        post_data = session.post.call_args.kwargs["data"]
        submitted = float(post_data["activity[distance]"])
        # 13.13 km = 8.1582 miles. Critical: NOT 13.13 (which would be 21.13 km in miles!)
        self.assertAlmostEqual(submitted, 8.1582, places=3)
        self.assertLess(submitted, 10, "Imperial submission must be miles, not km")
        print(f"  [PASS] 英制用户提交 mi ({submitted})")

    @patch("app.time.sleep")
    @patch("app.get_web_session")
    @patch("app.requests.get")
    def test_csrf_token_set_in_header(self, mock_api_get, mock_web_sess, mock_sleep):
        mock_api_get.side_effect = [
            make_response(200, {"measurement_preference": "meters"}),
            make_response(200, {"distance": 13130.0}),
        ]
        session = self._build_session_mock()
        mock_web_sess.return_value = session

        strava_app.fix_distance_web(12345, 13130.0)

        self.assertEqual(session.headers.get("X-CSRF-Token"), "csrf-from-edit-page")
        print("  [PASS] CSRF token 写入 header")

    @patch("app.time.sleep")
    @patch("app.get_web_session")
    @patch("app.requests.get")
    def test_verify_catches_failed_persistence(self, mock_api_get, mock_web_sess, mock_sleep):
        mock_api_get.side_effect = [
            make_response(200, {"measurement_preference": "meters"}),
            make_response(200, {"distance": 13500.0}),  # verify: NOT persisted!
        ]
        session = self._build_session_mock()
        mock_web_sess.return_value = session

        with self.assertRaises(Exception) as ctx:
            strava_app.fix_distance_web(12345, 13130.0)
        self.assertIn("not persisted", str(ctx.exception))
        print("  [PASS] verify 捕获到 distance 未被持久化")

    @patch("app.time.sleep")
    @patch("app.get_web_session")
    @patch("app.requests.get")
    def test_verify_accepts_within_tolerance(self, mock_api_get, mock_web_sess, mock_sleep):
        # Imperial round-trip can produce ~1m drift; 2m tolerance should accept it.
        mock_api_get.side_effect = [
            make_response(200, {"measurement_preference": "feet"}),
            make_response(200, {"distance": 13130.5}),  # 0.5m off — within tolerance
        ]
        session = self._build_session_mock()
        mock_web_sess.return_value = session

        result = strava_app.fix_distance_web(12345, 13130.0)
        self.assertTrue(result)
        print("  [PASS] verify 容忍英制 round-trip 的微小误差")


if __name__ == "__main__":
    print("\n=== Strava Fixer Tests ===\n")
    loader = unittest.TestLoader()
    loader.sortTestMethodsUsing = None
    suite = loader.loadTestsFromTestCase(TestDistanceFormula)
    suite.addTests(loader.loadTestsFromTestCase(TestFixDistance))
    suite.addTests(loader.loadTestsFromTestCase(TestFixDistanceWeb))
    runner = unittest.TextTestRunner(verbosity=0, stream=sys.stdout)
    result = runner.run(suite)
    print()
    if result.wasSuccessful():
        print("所有测试通过 ✓")
    else:
        print(f"失败: {len(result.failures)} 个, 错误: {len(result.errors)} 个")
    sys.exit(0 if result.wasSuccessful() else 1)
