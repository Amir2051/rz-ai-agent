import unittest
from uuid import uuid4
from datetime import datetime
from marketing.creator_media import Media, folder_name, prepare_publication, check_upload_quota

class CreatorMediaTests(unittest.TestCase):
    def setUp(self):
        self.w, self.a = str(uuid4()), str(uuid4())
        self.m = Media(self.w, str(uuid4()), 2_000_000_000, 5400, "a"*64)

    def draft(self, **kwargs):
        args = dict(media=self.m, workspace=self.w, platform="youtube", account=self.a,
                    title="Podcast", description="Episode", hashtags=["#AI", "#ai"])
        args.update(kwargs)
        return prepare_publication(**args)

    def test_long_form_draft(self):
        d = self.draft()
        self.assertEqual(d["hashtags"], ["#AI"])
        self.assertFalse(d["external_execution"])
        self.assertTrue(d["requires_review"])
        self.assertEqual(d["platform_validation"], "pending")

    def test_cross_tenant(self):
        with self.assertRaises(PermissionError):
            self.draft(workspace=str(uuid4()))

    def test_invalid_drafts(self):
        for args in (dict(platform="twitch"), dict(title=" "), dict(hashtags=["#bad tag"]),
                     dict(scheduled_at=datetime.now()), dict(account="invalid")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.draft(**args)

    def test_folders(self):
        self.assertEqual(folder_name(" Podcasts "), "Podcasts")
        for name in ("..", "../other", "a/b", "a\\b", "", "a\x00b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                folder_name(name)

    def test_quota(self):
        self.assertEqual(check_upload_quota(5, 5, 10), 10)
        for args in ((5,6,10), (-1,2,10), (0,0,10), (True,1,10)):
            with self.assertRaises(ValueError):
                check_upload_quota(*args)

if __name__ == "__main__":
    unittest.main()
