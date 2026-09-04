import unittest
from unittest.mock import patch

import app


class ProfuomoCommandWorkflowTests(unittest.TestCase):
    def test_failed_stock_retrieval_does_not_upload_stale_csv(self):
        self.assertTrue(hasattr(app, "run_profuomo_workflow"))

        failed_status = {"message": None, "error": "stock retrieval interrupted"}
        with patch.object(app, "profuomo", return_value=failed_status), patch.object(
            app, "upload"
        ) as upload:
            status = app.run_profuomo_workflow(headless=True)

        self.assertEqual(status, failed_status)
        upload.assert_not_called()

    def test_successful_stock_retrieval_starts_stock_upload(self):
        self.assertTrue(hasattr(app, "run_profuomo_workflow"))

        stock_status = {"message": "Finished", "error": None}
        upload_status = {"message": "Uploaded", "error": None}
        with patch.object(app, "profuomo", return_value=stock_status), patch.object(
            app, "upload", return_value=upload_status
        ) as upload:
            status = app.run_profuomo_workflow(headless=True)

        self.assertEqual(status, upload_status)
        upload.assert_called_once_with(False, True, headless=True)


if __name__ == "__main__":
    unittest.main()
