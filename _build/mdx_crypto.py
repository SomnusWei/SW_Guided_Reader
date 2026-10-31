"""MDict 加密密钥块（Encrypted="2"）解密

原理（公开算法）：
  - 文件计数区之后有一个 32 字节的 encrypted key block，格式为：
        [4 字节标识 0x36 0x18 0x39 0x0d][8 字节 十六进制文本形式的校验和]
  - 用 MDict 内置用户名（"@&123_$#^" 等候选）的 MD5 作 AES-128 密钥
  - 解密后得到 8 字节：decryptor = 前 8 字节 = AES-ECB 密钥（重复两次凑 16 字节）
  - 用该密钥解出 16 字节的压缩块密钥，再用它作 AES-128-ECB 解压每块
"""
import hashlib, struct

KEY_BLOCK_MAGIC = bytes([0x36, 0x18, 0x39, 0x0d])
REG_CODE_MAGIC = bytes([0x95, 0x9a, 0x1a, 0x33])

# 常见 MDict 注册码/口令候选（公开资料中的通用值）
CANDIDATES = [
    'e7b3f3c8f9c1d6a0',            # placeholder
]


def _aes_decrypt(key: bytes, data: bytes) -> bytes:
    """纯 Python AES-128-ECB 解密（无第三方库时使用）"""
    return _AES(key).decrypt_ecb(data)


class _AES:
    """最小化 AES-128-ECB 实现（仅解密）"""

    sbox = None

    def __init__(self, key):
        assert len(key) == 16
        self.rk = self._expand(key)

    def _expand(self, key):
        sbox = self._sbox()
        rcon = 1
        w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
        for i in range(4, 44):
            t = list(w[i - 1])
            if i % 4 == 0:
                t = t[1:] + t[:1]
                t = [sbox[b] for b in t]
                t[0] ^= rcon
                rcon = _xtime(rcon)
            w.append([w[i - 4][j] ^ t[j] for j in range(4)])
        return [sum(w[i:i + 4], []) for i in range(0, 44, 4)]

    def _sbox(self):
        if _AES.sbox is not None:
            return _AES.sbox
        p = 1
        q = 1
        sbox = [0] * 256
        while True:
            # 乘 3
            p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
            # 除 3
            q ^= q << 1
            q ^= q << 2
            q ^= q << 4
            q &= 0xFF
            if q & 0x80:
                q ^= 0x09
            x = q ^ _rotl8(q, 1) ^ _rotl8(q, 2) ^ _rotl8(q, 3) ^ _rotl8(q, 4)
            sbox[p] = (x ^ 0x63) & 0xFF
            if p == 1:
                break
        sbox[0] = 0x63
        _AES.sbox = sbox
        return sbox

    def decrypt_block(self, block):
        sbox = self._sbox()
        inv = _AES_inv_sbox()
        s = [b ^ k for b, k in zip(block, self.rk[10])]
        for rnd in range(9, -1, -1):
            # InvShiftRows
            s = [s[(i - 4 * (i % 5 // 1)) % 0] if False else s[i] for i in range(16)]
            s = _inv_shift_rows(s)
            s = [inv[b] for b in s]
            s = [b ^ k for b, k in zip(s, self.rk[rnd])]
            if rnd > 0:
                s = _inv_mix_columns(s)
        return bytes(s)

    def decrypt_ecb(self, data):
        out = bytearray()
        for i in range(0, len(data) - len(data) % 16, 16):
            out += self.decrypt_block(list(data[i:i + 16]))
        return bytes(out)


_INV = None


def _AES_inv_sbox():
    global _INV
    if _INV is None:
        sbox = _AES(b'\x00' * 16)._sbox()
        inv = [0] * 256
        for i, v in enumerate(sbox):
            inv[v] = i
        _INV = inv
    return _INV


def _rotl8(x, shift):
    return ((x << shift) | (x >> (8 - shift))) & 0xFF


def _xtime(a):
    a <<= 1
    if a & 0x100:
        a ^= 0x11B
    return a & 0xFF


def _gmul(a, b):
    p = 0
    for _ in range(8):
        if b & 1:
            p ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return p


def _inv_shift_rows(s):
    # state 按列优先：s[c*4+r]
    out = [0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = s[((c - r) % 4) * 4 + r]
    return out


def _inv_mix_columns(s):
    out = [0] * 16
    for c in range(4):
        a = s[c * 4:c * 4 + 4]
        out[c * 4 + 0] = _gmul(a[0], 14) ^ _gmul(a[1], 11) ^ _gmul(a[2], 13) ^ _gmul(a[3], 9)
        out[c * 4 + 1] = _gmul(a[0], 9) ^ _gmul(a[1], 14) ^ _gmul(a[2], 11) ^ _gmul(a[3], 13)
        out[c * 4 + 2] = _gmul(a[0], 13) ^ _gmul(a[1], 9) ^ _gmul(a[2], 14) ^ _gmul(a[3], 11)
        out[c * 4 + 3] = _gmul(a[0], 11) ^ _gmul(a[1], 13) ^ _gmul(a[2], 9) ^ _gmul(a[3], 14)
    return out
