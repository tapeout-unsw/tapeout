import struct
import unittest

from tapeout_host.protocol import MAX_PROGRAM_BYTES, ProgramError, ProgramImage


class ProtocolTests(unittest.TestCase):
    def test_boot_packet_has_little_endian_word_count_and_unchanged_image(self):
        payload = bytes.fromhex("1300000013000000")
        image = ProgramImage(payload, "two.bin")
        self.assertEqual(image.word_count, 2)
        self.assertEqual(image.boot_packet, struct.pack("<I", 2) + payload)

    def test_invalid_raw_binary_is_rejected(self):
        for payload in (
            b"",
            b"abc",
            b"x" * (MAX_PROGRAM_BYTES + 4),
            b"\x7fELF" + bytes(12),
        ):
            with self.subTest(length=len(payload)), self.assertRaises(ProgramError):
                ProgramImage(payload)


if __name__ == "__main__":
    unittest.main()
