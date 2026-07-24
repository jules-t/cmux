from __future__ import annotations

import unittest

from personal.package_bundle import patch_bundle_plist


class PackageBundleTests(unittest.TestCase):
    def test_isolates_identity_and_removes_official_update_feed(self) -> None:
        plist = {
            "CFBundleName": "cmux",
            "CFBundleDisplayName": "cmux",
            "CFBundleIdentifier": "com.cmuxterm.app",
            "SUFeedURL": "https://example.invalid/appcast.xml",
            "SUPublicEDKey": "key",
            "CFBundleURLTypes": [
                {
                    "CFBundleURLName": "com.cmuxterm.app.auth",
                    "CFBundleURLSchemes": ["cmux"],
                },
                {
                    "CFBundleURLName": "com.cmuxterm.app.web",
                    "CFBundleURLSchemes": ["http", "https"],
                    "LSHandlerRank": "Default",
                },
            ],
        }
        patched = patch_bundle_plist(
            plist,
            app_name="cmux Personal",
            bundle_identifier="com.cmuxterm.app.staging.personal",
            auth_callback_scheme="cmux-personal",
            source_sha="abc",
            base_tag="v0.64.20",
            personal_tag="personal-v0.64.20-r1",
        )
        self.assertEqual(patched["CFBundleIdentifier"], "com.cmuxterm.app.staging.personal")
        self.assertNotIn("SUFeedURL", patched)
        self.assertNotIn("SUPublicEDKey", patched)
        self.assertEqual(
            patched["CFBundleURLTypes"][0]["CFBundleURLSchemes"],
            ["cmux-personal"],
        )
        self.assertEqual(patched["CFBundleURLTypes"][1]["LSHandlerRank"], "Alternate")
        self.assertEqual(
            patched["LSEnvironment"]["CMUX_BUNDLE_ID"],
            "com.cmuxterm.app.staging.personal",
        )


if __name__ == "__main__":
    unittest.main()
