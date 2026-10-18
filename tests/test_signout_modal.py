"""
Unit & Integration Test for Sign Out Confirmation Modal
========================================================
Verifies:
1. Web server renders index.html with HTTP 200.
2. Header and Sidebar include sign out trigger buttons (#btnSignOutHeader, #btnSignOutSidebar).
3. The in-app confirmation modal (#signOutModalBackdrop) is included in the DOM.
4. Modal is hidden by default and includes all required interactive elements:
   - Close button (#btnCloseSignOutModal)
   - Cancel button (#btnCancelSignOutModal)
   - Confirm button (#btnConfirmSignOutModal)
   - Title and User account display (#signOutModalTitle, #signOutModalUserEmail)
"""

import unittest
from app import app


class TestSignOutConfirmationModal(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_signout_modal_dom_structure(self):
        """Assert index.html contains the in-app confirmation modal and trigger buttons."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode("utf-8")

        # 1. Triggers exist
        self.assertIn('id="btnSignOutHeader"', html, "Header sign-out button missing.")
        self.assertIn('id="btnSignOutSidebar"', html, "Sidebar sign-out button missing.")

        # 2. Modal backdrop exists with hidden class
        self.assertIn('id="signOutModalBackdrop"', html, "Sign-out modal backdrop missing.")
        self.assertIn('modal-backdrop hidden', html, "Modal backdrop should be hidden by default.")

        # 3. Modal elements exist
        self.assertIn('id="signOutModalTitle"', html, "Modal title missing.")
        self.assertIn('Sign Out Confirmation', html, "Modal title text missing.")
        self.assertIn('id="btnCloseSignOutModal"', html, "Modal close button missing.")
        self.assertIn('id="btnCancelSignOutModal"', html, "Modal cancel button missing.")
        self.assertIn('id="btnConfirmSignOutModal"', html, "Modal confirm button missing.")
        self.assertIn('id="signOutModalUserEmail"', html, "Modal user email placeholder missing.")


if __name__ == "__main__":
    unittest.main()
