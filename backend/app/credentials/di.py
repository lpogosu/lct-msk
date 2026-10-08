"""Data Integrity: криптосьют eddsa-jcs-2022 (W3C VC Data Integrity EdDSA Cryptosuites v1.0).

Подпись = Ed25519( SHA-256(JCS(proofConfig)) ‖ SHA-256(JCS(документ без proof)) ), proofValue — multibase base58btc.
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.credentials.jcs import canonicalize
from app.credentials.multiformats import multibase_b58_decode, multibase_b58_encode

CRYPTOSUITE = "eddsa-jcs-2022"


def iso_z(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash_data(unsecured: dict[str, Any], proof_config: dict[str, Any]) -> bytes:
    return hashlib.sha256(canonicalize(proof_config)).digest() + hashlib.sha256(canonicalize(unsecured)).digest()


def _proof_config(doc: dict[str, Any], proof: dict[str, Any]) -> dict[str, Any]:
    cfg = {k: v for k, v in proof.items() if k != "proofValue"}
    cfg["@context"] = doc["@context"]
    return cfg


def sign_document(doc: dict[str, Any], key: Ed25519PrivateKey, verification_method: str, created: datetime) -> dict[str, Any]:
    unsecured = {k: v for k, v in doc.items() if k != "proof"}
    proof = {
        "type": "DataIntegrityProof",
        "cryptosuite": CRYPTOSUITE,
        "verificationMethod": verification_method,
        "proofPurpose": "assertionMethod",
        "created": iso_z(created),
    }
    sig = key.sign(_hash_data(unsecured, _proof_config(unsecured, proof)))
    out = copy.deepcopy(unsecured)
    out["proof"] = {**proof, "proofValue": multibase_b58_encode(sig)}
    return out


@dataclass(frozen=True)
class ProofCheck:
    ok: bool
    reason: str | None = None  # no_proof | bad_suite | bad_proof | bad_signature


def verify_proof(doc: dict[str, Any], public: Ed25519PublicKey) -> ProofCheck:
    proof = doc.get("proof")
    if not isinstance(proof, dict) or "@context" not in doc:
        return ProofCheck(False, "no_proof")
    if proof.get("type") != "DataIntegrityProof" or proof.get("cryptosuite") != CRYPTOSUITE:
        return ProofCheck(False, "bad_suite")
    if proof.get("proofPurpose") != "assertionMethod":
        return ProofCheck(False, "bad_proof")
    value = proof.get("proofValue")
    if not isinstance(value, str):
        return ProofCheck(False, "bad_proof")
    try:
        sig = multibase_b58_decode(value)
        unsecured = {k: v for k, v in doc.items() if k != "proof"}
        public.verify(sig, _hash_data(unsecured, _proof_config(unsecured, proof)))
    except (InvalidSignature, ValueError, TypeError):
        return ProofCheck(False, "bad_signature")
    return ProofCheck(True)
