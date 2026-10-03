"""
Tests for Live Table Streaming & Execution Controls (Pause, Resume, Stop & Save).
"""

import os
import sys
import unittest
import time
import threading

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
AI_APP_DIR = os.path.abspath(os.path.join(TESTS_DIR, ".."))
PROJECT_ROOT = os.path.abspath(os.path.join(AI_APP_DIR, ".."))
for p in [AI_APP_DIR, PROJECT_ROOT]:
    if p not in sys.path:
        sys.path.insert(0, p)

from core.execution_controller import ExtractionController
from app import app, jobs, jobs_lock, job_controllers, job_controllers_lock


class TestLiveExecutionControls(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()
        self.test_job_id = f"test_job_{int(time.time()*1000)}"
        self.controller = ExtractionController()

        with jobs_lock:
            jobs[self.test_job_id] = {
                "job_id": self.test_job_id,
                "status": "processing",
                "progress": 25.0,
                "message": "Extracting student records live...",
                "live_students": [
                    {"Seat_No": "101", "Name": "ALICE SMITH", "SGPI": "9.50"},
                    {"Seat_No": "102", "Name": "BOB JONES", "SGPI": "8.80"}
                ],
                "audit_logs": [],
                "token_usage": {"input": 120, "output": 80, "total": 200}
            }

        with job_controllers_lock:
            job_controllers[self.test_job_id] = self.controller

    def tearDown(self):
        with jobs_lock:
            jobs.pop(self.test_job_id, None)
        with job_controllers_lock:
            job_controllers.pop(self.test_job_id, None)

    def test_controller_pause_resume(self):
        ctrl = ExtractionController()
        self.assertFalse(ctrl.is_paused)
        self.assertFalse(ctrl.is_stopped)

        ctrl.pause()
        self.assertTrue(ctrl.is_paused)

        # In a background thread, check pause
        paused_reached = threading.Event()
        resumed_reached = threading.Event()

        def worker():
            paused_reached.set()
            should_stop = ctrl.check_pause_and_stop()
            self.assertFalse(should_stop)
            resumed_reached.set()

        t = threading.Thread(target=worker)
        t.start()
        paused_reached.wait(timeout=1.0)
        time.sleep(0.05)
        # Thread should be paused waiting on Event
        self.assertFalse(resumed_reached.is_set())

        ctrl.resume()
        resumed_reached.wait(timeout=1.0)
        self.assertTrue(resumed_reached.is_set())
        t.join(timeout=1.0)

    def test_controller_stop(self):
        ctrl = ExtractionController()
        ctrl.stop()
        self.assertTrue(ctrl.is_stopped)
        self.assertTrue(ctrl.should_stop())
        self.assertTrue(ctrl.check_pause_and_stop())

    def test_api_get_job_live_status(self):
        res = self.client.get(f"/api/jobs/{self.test_job_id}")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["status"], "processing")
        self.assertEqual(data["live_count"], 2)
        self.assertEqual(len(data["live_students"]), 2)
        self.assertFalse(data["is_paused"])
        self.assertFalse(data["is_stopped"])

    def test_api_pause_and_resume_job(self):
        # Pause job
        p_res = self.client.post(f"/api/jobs/{self.test_job_id}/pause")
        self.assertEqual(p_res.status_code, 200)
        p_data = p_res.get_json()
        self.assertTrue(p_data["success"])

        # Check status shows paused
        st_res = self.client.get(f"/api/jobs/{self.test_job_id}")
        st_data = st_res.get_json()
        self.assertTrue(st_data["is_paused"])
        self.assertEqual(st_data["status"], "paused")

        # Resume job
        r_res = self.client.post(f"/api/jobs/{self.test_job_id}/resume")
        self.assertEqual(r_res.status_code, 200)
        r_data = r_res.get_json()
        self.assertTrue(r_data["success"])

        # Check status shows resumed
        st2_res = self.client.get(f"/api/jobs/{self.test_job_id}")
        st2_data = st2_res.get_json()
        self.assertFalse(st2_data["is_paused"])
        self.assertEqual(st2_data["status"], "processing")

    def test_api_stop_job(self):
        # Stop job
        s_res = self.client.post(f"/api/jobs/{self.test_job_id}/stop")
        self.assertEqual(s_res.status_code, 200)
        s_data = s_res.get_json()
        self.assertTrue(s_data["success"])

        # Check status shows stopped
        st_res = self.client.get(f"/api/jobs/{self.test_job_id}")
        st_data = st_res.get_json()
        self.assertTrue(st_data["is_stopped"])
        self.assertTrue(self.controller.is_stopped)


if __name__ == "__main__":
    unittest.main()
