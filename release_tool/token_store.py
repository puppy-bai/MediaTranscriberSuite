"""Optional Windows-user-bound token storage via DPAPI; no plaintext fallback."""

import ctypes
import os
import sys
from ctypes import wintypes
from pathlib import Path

TOKEN_FILE_NAME = "token.dpapi"
_MAX_BLOB = 8192


class TokenStoreError(RuntimeError):
    """Encrypted token could not be saved or restored."""


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, *, decrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise TokenStoreError("记住 Token 只支持 Windows；此平台不会保存明文。")
    if not data or len(data) > _MAX_BLOB:
        raise TokenStoreError("Token 数据无效或过长。")
    source = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    in_blob = _DataBlob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    out_blob = _DataBlob()
    # CryptProtectData without CRYPTPROTECT_LOCAL_MACHINE binds the ciphertext
    # to the signed-in Windows user. Suppress UI so this never blocks Tk.
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(_DataBlob), wintypes.LPCWSTR,
                                         ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.c_void_p, wintypes.DWORD,
                                         ctypes.POINTER(_DataBlob)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(_DataBlob), ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_void_p, wintypes.DWORD,
                                           ctypes.POINTER(_DataBlob)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    try:
        if decrypt:
            ok = crypt32.CryptUnprotectData(ctypes.byref(in_blob), None, None, None,
                                            None, flags, ctypes.byref(out_blob))
        else:
            ok = crypt32.CryptProtectData(ctypes.byref(in_blob), "Release Publisher token",
                                          None, None, None, flags, ctypes.byref(out_blob))
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    except OSError as e:
        raise TokenStoreError(f"Windows 凭据保护失败：{e}") from e
    finally:
        if out_blob.pbData:
            ctypes.windll.kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def token_file(config_path: Path) -> Path:
    return config_path.parent / TOKEN_FILE_NAME


def store_token(config_path: Path, token: str) -> None:
    """Replace the encrypted token atomically; never serialize plaintext."""
    if not token.strip():
        raise TokenStoreError("Token 为空，无法保存。")
    fp = token_file(config_path)
    ciphertext = _crypt(token.strip().encode("utf-8"), decrypt=False)
    fp.parent.mkdir(parents=True, exist_ok=True)
    tmp = fp.with_suffix(".tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(ciphertext)
        os.replace(tmp, fp)
    finally:
        tmp.unlink(missing_ok=True)


def load_token(config_path: Path) -> str | None:
    fp = token_file(config_path)
    if not fp.is_file():
        return None
    ciphertext = fp.read_bytes()
    try:
        return _crypt(ciphertext, decrypt=True).decode("utf-8")
    except UnicodeError as e:
        raise TokenStoreError("已保存的 Token 无法解码，请取消记住后重新保存。") from e


def forget_token(config_path: Path) -> None:
    token_file(config_path).unlink(missing_ok=True)
