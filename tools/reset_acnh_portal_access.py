"""Reset only ACNH's Custom Designs Portal onboarding flags.

The tool uses the same encrypted-pair format and Murmur3 integrity hashes as
NHSE for the 3.0.3 personal save. It is intentionally limited to a known
``personal.dat``/``personalHeader.dat`` pair and requires ``--apply`` to write.
"""

from __future__ import annotations

import argparse
import secrets
import struct
from pathlib import Path

from inspect_acnh_save import XorShift128, decrypt, params


PERSONAL_HASH_OFFSET = 0x110
PERSONAL_HASH_LENGTH = 0x37ACC
EVENT_FLAGS_OFFSET = 0x110 + 0xC170
FIRST_ACCESS_INDEX = 0x34D
UPLOAD_ONCE_INDEX = 0x34E


def murmur3(data: bytes) -> int:
    if len(data) % 4:
        raise ValueError("Murmur3 input must be aligned")
    checksum = 0
    for (value,) in struct.iter_unpack("<I", data):
        # NHSE evaluates both products as overflowing uint32 values before
        # combining them. Masking only after the OR would silently generate
        # invalid integrity hashes for real save pairs.
        left = (value * 0x16A88000) & 0xFFFFFFFF
        right = ((value * 0xCC9E2D51) & 0xFFFFFFFF) >> 17
        value = left | right
        value = (value * 0x1B873593) & 0xFFFFFFFF
        checksum ^= value
        checksum = ((checksum >> 19) | (checksum << 13)) & 0xFFFFFFFF
        checksum = (checksum * 5 + 0xE6546B64) & 0xFFFFFFFF
    checksum ^= len(data)
    checksum ^= checksum >> 16
    checksum = (checksum * 0x85EBCA6B) & 0xFFFFFFFF
    checksum ^= checksum >> 13
    checksum = (checksum * 0xC2B2AE35) & 0xFFFFFFFF
    checksum ^= checksum >> 16
    return checksum & 0xFFFFFFFF


def encrypt(data: bytes, version_header: bytes, seed: int) -> tuple[bytes, bytes]:
    """Mirror NHSE's Encryption.Encrypt, returning (encrypted, header)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    rng = XorShift128(seed)
    words = [rng.next() for _ in range(128)]
    header = bytearray(0x300)
    header[:0x100] = version_header[:0x100]
    struct.pack_into("<128I", header, 0x100, *words)
    key = params(tuple(words), 0)
    counter = bytearray(params(tuple(words), 2))
    cipher = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    output = bytearray(data)
    for offset in range(0, len(output), 16):
        stream = cipher.update(bytes(counter))
        for index in range(min(16, len(output) - offset)):
            output[offset + index] ^= stream[index]
        for index in range(15, -1, -1):
            counter[index] = (counter[index] + 1) & 0xFF
            if counter[index]:
                break
    return bytes(output), bytes(header)


def reset(data: bytes) -> tuple[bytes, tuple[int, int]]:
    if len(data) != 0x74A40:
        raise ValueError(f"unexpected 3.0.3 personal.dat size: {len(data):#x}")
    result = bytearray(data)
    offsets = (EVENT_FLAGS_OFFSET + FIRST_ACCESS_INDEX * 2, EVENT_FLAGS_OFFSET + UPLOAD_ONCE_INDEX * 2)
    before = tuple(struct.unpack_from("<h", result, offset)[0] for offset in offsets)
    for offset in offsets:
        struct.pack_into("<h", result, offset, 0)
    checksum = murmur3(bytes(result[PERSONAL_HASH_OFFSET + 4 : PERSONAL_HASH_OFFSET + 4 + PERSONAL_HASH_LENGTH]))
    struct.pack_into("<I", result, PERSONAL_HASH_OFFSET, checksum)
    return bytes(result), before


def process(villager: Path, apply: bool, seed: int) -> None:
    data_path = villager / "personal.dat"
    header_path = villager / "personalHeader.dat"
    header = header_path.read_bytes()
    original_encrypted = data_path.read_bytes()
    original = decrypt(header, original_encrypted)
    revision = struct.unpack_from("<H", header, 0x0E)[0]
    if revision != 34:
        raise ValueError(f"expected ACNH 3.0.3 save revision 34, found {revision}")
    updated, before = reset(original)
    encrypted, new_header = encrypt(updated, header, seed)
    if decrypt(new_header, encrypted) != updated:
        raise RuntimeError("encryption self-check failed")
    expected_hash = struct.unpack_from("<I", updated, PERSONAL_HASH_OFFSET)[0]
    actual_hash = murmur3(updated[PERSONAL_HASH_OFFSET + 4 : PERSONAL_HASH_OFFSET + 4 + PERSONAL_HASH_LENGTH])
    if expected_hash != actual_hash:
        raise RuntimeError("personal hash self-check failed")
    print(f"{villager}: flags first-access/upload-once {before} -> (0, 0); hash verified")
    if apply:
        data_path.write_bytes(encrypted)
        header_path.write_bytes(new_header)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("save_slot", type=Path, help="slot containing Villager0")
    parser.add_argument("--apply", action="store_true", help="write the verified encrypted pair")
    parser.add_argument("--seed", type=lambda value: int(value, 0), default=None)
    args = parser.parse_args()
    seed = secrets.randbits(32) if args.seed is None else args.seed
    process(args.save_slot / "Villager0", args.apply, seed)
    print("dry run only" if not args.apply else "updated encrypted save pair")


if __name__ == "__main__":
    main()
