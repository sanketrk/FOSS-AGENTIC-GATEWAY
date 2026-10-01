"""Generate disposable certificates and separate service credentials for this demo."""
import datetime
import json
import os
import secrets
from pathlib import Path

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID


def private_bytes(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def prepare(root):
    root = Path(root)
    for name in ("issuer", "gateway", "backend", "client"):
        (root / name).mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Disposable banking demo CA")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                     data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                     encipher_only=False, decipher_only=False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    for name in ("issuer", "gateway", "client"):
        (root / name / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for name in ("issuer", "gateway"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
                .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(minutes=1)).not_valid_after(now + datetime.timedelta(days=2))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
                .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
                                            data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                                            encipher_only=False, decipher_only=False), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(name), x509.DNSName("localhost")]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .sign(ca_key, hashes.SHA256()))
        (root / name / "tls.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root / name / "tls.key").write_bytes(private_bytes(key))
    signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    (root / "issuer" / "signing.key").write_bytes(private_bytes(signing_key))
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    jwk.update(kid="banking-demo", use="sig", alg="RS256")
    for name in ("issuer", "backend"):
        (root / name / "jwks.json").write_text(json.dumps({"keys": [jwk]}))
    for credential, consumer in (("agent-secret", "client"), ("exchange-secret", "gateway")):
        secret = secrets.token_urlsafe(32)
        for name in ("issuer", consumer): (root / name / credential).write_text(secret)
    for name in ("issuer", "gateway", "backend", "client"):
        for path in (root / name).iterdir(): path.chmod(0o640)
    print("Prepared disposable demo certificates and credentials; no secrets printed.")


if __name__ == "__main__":
    prepare(os.environ.get("DEMO_RUNTIME", "/runtime"))
