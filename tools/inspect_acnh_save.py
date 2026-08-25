"""Read-only inspector for Animal Crossing: New Horizons encrypted save pairs.

This mirrors NHSE's AES-CTR decryption solely to identify portal-related data;
it never writes to the save directory.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


MASK32 = 0xFFFFFFFF
MERSENNE = 0x6C078965


class XorShift128:
    def __init__(self, seed: int) -> None:
        self.a = (MERSENNE * (seed ^ (seed >> 30)) + 1) & MASK32
        self.b = (MERSENNE * (self.a ^ (self.a >> 30)) + 2) & MASK32
        self.c = (MERSENNE * (self.b ^ (self.b >> 30)) + 3) & MASK32
        self.d = (MERSENNE * (self.c ^ (self.c >> 30)) + 4) & MASK32

    def next(self) -> int:
        value = self.a
        self.a, self.b, self.c = self.b, self.c, self.d
        value ^= (value << 11) & MASK32
        value ^= value >> 8
        self.d = (value ^ self.d ^ (self.d >> 19)) & MASK32
        return self.d

    def next64(self) -> int:
        return (self.next() << 32) | self.next()


def params(words: tuple[int, ...], index: int, length: int = 16) -> bytes:
    rng = XorShift128(words[words[index] & 0x7F])
    rolls = (words[words[index + 1] & 0x7F] & 0x7F & 0xF) + 1
    for _ in range(rolls):
        rng.next64()
    return bytes(rng.next() >> 24 for _ in range(length))


def decrypt(header: bytes, encrypted: bytes) -> bytes:
    if len(header) < 0x300:
        raise ValueError("header is too short")
    words = struct.unpack("<128I", header[0x100:0x300])
    key = params(words, 0)
    counter = bytearray(params(words, 2))
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    output = bytearray(encrypted)
    for offset in range(0, len(output), 16):
        stream = encryptor.update(bytes(counter))
        block_length = min(16, len(output) - offset)
        for index in range(block_length):
            output[offset + index] ^= stream[index]
        for index in range(15, -1, -1):
            counter[index] = (counter[index] + 1) & 0xFF
            if counter[index]:
                break
    return bytes(output)


def utf16_name(data: bytes, offset: int, limit: int = 10) -> str:
    return data[offset : offset + limit * 2].decode("utf-16le", "ignore").split("\0", 1)[0]


def print_flag(data: bytes, index: int, label: str) -> None:
    # PersonalOffsets30: Player 0x110 + EventFlagsPlayer 0xc170.
    offset = 0x110 + 0xC170 + index * 2
    value = struct.unpack_from("<h", data, offset)[0]
    print(f"{label}: {value} (offset 0x{offset:X})")


def inspect_pair(directory: Path, name: str) -> bytes:
    header = (directory / f"{name}Header.dat").read_bytes()
    encrypted = (directory / f"{name}.dat").read_bytes()
    data = decrypt(header, encrypted)
    major, minor, unk1, header_revision, unk2, save_revision = struct.unpack_from("<IIHHHH", header)
    print(f"{directory.name}/{name}: revision {save_revision} (major=0x{major:X}, minor=0x{minor:X}), {len(data):,} bytes")
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("save_root", type=Path)
    args = parser.parse_args()
    root: Path = args.save_root

    for villager in sorted(root.glob("Villager*")):
        personal = inspect_pair(villager, "personal")
        personal_id_offset = 0x110 + 0xC138
        town_id, player_id = struct.unpack_from("<I20xI", personal, personal_id_offset)
        print(f"  town={utf16_name(personal, personal_id_offset + 4)!r} ({town_id}), player={utf16_name(personal, personal_id_offset + 0x20)!r} ({player_id})")
        print_flag(personal, 0x34D, "  MyDesignExchangeFirstAccess")
        print_flag(personal, 0x34E, "  MyDesignExchangeUploadOnce")
        print_flag(personal, 0x41A, "  MyDesignExchangeDiscloseAuthorID")
        inspect_pair(villager, "profile")


if __name__ == "__main__":
    main()
