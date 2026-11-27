import unittest,pyotp
from auth_service.mfa import matching_step
class MFA(unittest.TestCase):
 def test_window(self):
    secret=pyotp.random_base32();t=pyotp.TOTP(secret);now=3000
    self.assertEqual(matching_step(secret,t.at(now),now),100)
    self.assertEqual(matching_step(secret,t.at(now-30),now),99)
    self.assertIsNone(matching_step(secret,'invalid',now))
    self.assertIsNone(matching_step(secret,None,now))
