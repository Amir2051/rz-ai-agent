import unittest
from auth_service.identity_policy import validate_claims,OWNER
class IdentityPolicy(unittest.TestCase):
 def test_policy(self):
    c=dict(iss='https://accounts.google.com',aud='fixture',exp=1100,iat=990,nonce='nonce',email='customer@example.invalid',email_verified=True,sub='123')
    self.assertEqual(validate_claims(c,'fixture','nonce',1000),('123','customer@example.invalid'))
    for patch in [dict(iss='https://example.invalid'),dict(aud='other'),dict(azp='other'),dict(exp=999),dict(iat=1),dict(nonce='wrong'),dict(email_verified=False),dict(email=OWNER),dict(sub='')]:
      with self.subTest(patch=patch),self.assertRaises(ValueError):validate_claims(c|patch,'fixture','nonce',1000)
if __name__=='__main__':unittest.main()
