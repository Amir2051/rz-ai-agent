import unittest,time
from authlib.jose import JsonWebKey,JsonWebToken
from auth_service.google_verify import verify_token
class Verify(unittest.TestCase):
 def test_signature(self):
    key=JsonWebKey.generate_key('RSA',2048,is_private=True)
    other=JsonWebKey.generate_key('RSA',2048,is_private=True)
    now=int(time.time());claims=dict(iss='https://accounts.google.com',aud='fixture',sub='fixture-user',email='fixture@example.invalid',email_verified=True,nonce='fixture-nonce',iat=now,exp=now+60)
    jwt=JsonWebToken(['RS256']);token=jwt.encode({'alg':'RS256'},claims,key).decode()
    self.assertEqual(verify_token(token,key.as_dict(is_private=False),'fixture','fixture-nonce'),('fixture-user','fixture@example.invalid'))
    with self.assertRaises(Exception):verify_token(token,other.as_dict(is_private=False),'fixture','fixture-nonce')
    with self.assertRaises(ValueError):verify_token(token,key.as_dict(is_private=False),'fixture','wrong')
