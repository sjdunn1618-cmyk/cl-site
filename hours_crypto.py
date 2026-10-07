"""Authenticated encrypted Homebase snapshot transport, with no plaintext logging."""
import base64,json,os
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey,X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
CONTEXT=b'cl-site-homebase-hours-v1'
def derive(shared):return HKDF(algorithm=hashes.SHA256(),length=32,salt=None,info=CONTEXT).derive(shared)
def encrypt(payload,public):
    ephemeral=X25519PrivateKey.generate();nonce=os.urandom(12)
    e=ephemeral.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    key=derive(ephemeral.exchange(X25519PublicKey.from_public_bytes(base64.b64decode(public))))
    raw=json.dumps(payload,separators=(',',':')).encode()
    return base64.b64encode(e+nonce+AESGCM(key).encrypt(nonce,raw,CONTEXT)).decode()
def decrypt(ciphertext,private):
    blob=base64.b64decode(ciphertext,validate=True)
    key=X25519PrivateKey.from_private_bytes(base64.b64decode(private,validate=True))
    aes=derive(key.exchange(X25519PublicKey.from_public_bytes(blob[:32])))
    return json.loads(AESGCM(aes).decrypt(blob[32:44],blob[44:],CONTEXT))
