"""cache_limits: helpers moved out of keys.py (P2b cycle break)."""

import logging
import os
import platform
import subprocess
import sys
from typing import Any

import psutil

logger = logging.getLogger(__name__)


_psutil_vm_fallback_logged = False


_darwin_total_ram_cached: int | None = None


def _is_libraw_native_name(name: str) -> bool:
    lower = name.lower()
    return lower == "raw_r.dll" or (lower.startswith("libraw_r") and lower.endswith((".dylib", ".so", ".dll")))


def _libraw_native_search_dirs() -> list[str]:
    """Directories that may hold the packaged LibRaw shared library.

    Dev installs keep ``raw_r.dll`` beside the rawpy package. Frozen / installer
    layouts often put it next to the exe or under ``_internal/`` (PyInstaller
    ``sys._MEIPASS``). Searching only rawpy's folder made the stamp report
    ``nolib`` after packaging, so a later pin swap would not invalidate caches.
    """
    dirs: list[str] = []
    try:
        import rawpy

        rawpy_dir = os.path.dirname(os.path.abspath(rawpy.__file__))
        dirs.append(rawpy_dir)
        parent = os.path.dirname(rawpy_dir)
        if parent:
            dirs.append(parent)
    except Exception:
        pass
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        dirs.append(exe_dir)
        dirs.append(os.path.join(exe_dir, "_internal"))
        dirs.append(os.path.join(exe_dir, "_internal", "rawpy"))
    except Exception:
        pass
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(meipass)
        dirs.append(os.path.join(meipass, "rawpy"))
    try:
        if getattr(sys, "frozen", False) and sys.argv:
            root = os.path.dirname(os.path.abspath(sys.argv[0]))
            dirs.append(root)
            dirs.append(os.path.join(root, "_internal"))
            dirs.append(os.path.join(root, "_internal", "rawpy"))
    except Exception:
        pass
    out: list[str] = []
    seen: set[str] = set()
    for d in dirs:
        if not d:
            continue
        key = os.path.normcase(os.path.abspath(d))
        if key in seen:
            continue
        if os.path.isdir(d):
            seen.add(key)
            out.append(d)
    return out


def _libraw_native_fingerprint() -> str:
    candidates: list[str] = []
    for directory in _libraw_native_search_dirs():
        try:
            for name in os.listdir(directory):
                if _is_libraw_native_name(name):
                    candidates.append(os.path.join(directory, name))
        except Exception:
            continue
    if not candidates:
        return "nolib"
    primary = None
    for path in candidates:
        base = os.path.basename(path).lower()
        if base == "raw_r.dll" or base.startswith("libraw_r.") or base == "libraw_r.dylib":
            primary = path
            break
    path = primary or sorted(candidates)[0]
    st = os.stat(path)
    return f"{os.path.basename(path)}:{st.st_size}:{int(st.st_mtime)}"


def libraw_runtime_stamp() -> str:
    """Fingerprint the LibRaw binary backing rawpy (version + native lib file).

    Packaging may swap ``raw_r.dll`` / ``libraw_r*.dylib`` while leaving
    ``rawpy.libraw_version`` unchanged (master still reports 0.22.0). Size and
    mtime of the bundled native library catch that swap so demosaic caches
    invalidate. Returns "" if rawpy is unavailable.
    """
    try:
        import rawpy
    except Exception:
        return ""
    try:
        ver = getattr(rawpy, "libraw_version", None) or ()
        ver_s = ".".join(str(int(x)) for x in ver[:3]) if ver else "unknown"
    except Exception:
        ver_s = "unknown"
    try:
        lib_s = _libraw_native_fingerprint()
    except Exception:
        lib_s = "nolib"
    return f"{ver_s}|{lib_s}"


def _darwin_os_proc_available_memory() -> int | None:
    """Jetsam headroom via os_proc_available_memory (no subprocess)."""
    if platform.system() != "Darwin":
        return None
    try:
        import ctypes

        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        lib.os_proc_available_memory.restype = ctypes.c_uint64
        lib.os_proc_available_memory.argtypes = []
        n = int(lib.os_proc_available_memory())
        return n if n > 0 else None
    except Exception:
        return None


def _sysctl_hw_memsize_bytes() -> int | None:
    """Physical RAM via sysctlbyname (cached); subprocess only if ctypes fails."""
    global _darwin_total_ram_cached
    if platform.system() != "Darwin":
        return None
    if _darwin_total_ram_cached:
        return _darwin_total_ram_cached
    try:
        import ctypes

        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        val = ctypes.c_uint64(0)
        size = ctypes.c_size_t(ctypes.sizeof(val))
        lib.sysctlbyname.argtypes = [
            ctypes.c_char_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t),
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        lib.sysctlbyname.restype = ctypes.c_int
        rc = lib.sysctlbyname(b"hw.memsize", ctypes.byref(val), ctypes.byref(size), None, 0)
        if rc == 0 and int(val.value) > 0:
            _darwin_total_ram_cached = int(val.value)
            return _darwin_total_ram_cached
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", "hw.memsize"],
            text=True,
            timeout=2,
        ).strip()
        n = int(out)
        if n > 0:
            _darwin_total_ram_cached = n
            return n
    except Exception:
        return None
    return None


def _darwin_page_size_bytes() -> int:
    if platform.system() != "Darwin":
        return 4096
    try:
        out = subprocess.check_output(["pagesize"], text=True, timeout=2).strip()
        return max(4096, int(out))
    except Exception:
        return 4096


def _parse_vm_stat_page_count(vm_stat_output: str, label: str) -> int:
    prefix = f"{label}:"
    for line in vm_stat_output.splitlines():
        if not line.startswith(prefix):
            continue
        raw = line.split(":", 1)[1].strip().rstrip(".")
        try:
            return int(raw.replace(",", ""))
        except ValueError:
            return 0
    return 0


def _darwin_memory_stats_from_vm_stat() -> tuple[int, int, float] | None:
    """Installed RAM + available bytes. Prefer os_proc_available_memory (jetsam).

    Never shells out to the ``memory_pressure`` CLI. ``vm_stat`` is a last
    resort when jetsam headroom is unavailable.
    """
    if platform.system() != "Darwin":
        return None
    total = _sysctl_hw_memsize_bytes()
    if not total:
        return None
    jetsam = _darwin_os_proc_available_memory()
    if jetsam is not None:
        available = min(int(total), int(jetsam))
        used = max(0, int(total) - available)
        percent = min(100.0, max(0.0, (used / float(total)) * 100.0))
        return int(total), available, percent
    try:
        vm_out = subprocess.check_output(["vm_stat"], text=True, timeout=2)
    except Exception:
        return None

    page_size = _darwin_page_size_bytes()
    free_pages = _parse_vm_stat_page_count(vm_out, "Pages free")
    inactive_pages = _parse_vm_stat_page_count(vm_out, "Pages inactive")
    speculative_pages = _parse_vm_stat_page_count(vm_out, "Pages speculative")
    available_pages = free_pages + inactive_pages + speculative_pages
    available = min(total, available_pages * page_size)
    used = max(0, total - available)
    percent = min(100.0, max(0.0, (used / total) * 100.0))
    return total, available, percent


def system_memory_total_bytes() -> int | None:
    """Total installed RAM in bytes (sysctl on macOS, then psutil)."""
    if platform.system() == "Darwin":
        total = _sysctl_hw_memsize_bytes()
        if total:
            return int(total)
    try:
        return int(psutil.virtual_memory().total)
    except Exception:
        return _sysctl_hw_memsize_bytes()


def _safe_virtual_memory() -> Any:
    """Memory stats with macOS sysctl/vm_stat first (avoids psutil HOST_VM_INFO64 issues)."""
    global _psutil_vm_fallback_logged
    if platform.system() == "Darwin":
        fallback = _darwin_memory_stats_from_vm_stat()
        if fallback is not None:
            from types import SimpleNamespace

            total, available, percent = fallback
            return SimpleNamespace(total=total, available=available, percent=percent)

    try:
        return psutil.virtual_memory()
    except Exception as exc:
        if not _psutil_vm_fallback_logged:
            logger.debug(
                "psutil.virtual_memory() unavailable (%s); using fallback memory stats",
                exc,
            )
            _psutil_vm_fallback_logged = True
        fallback = _darwin_memory_stats_from_vm_stat()
        from types import SimpleNamespace

        if fallback is not None:
            total, available, percent = fallback
            return SimpleNamespace(total=total, available=available, percent=percent)

        total = _sysctl_hw_memsize_bytes() or (8 * 1024**3)
        available = total // 4
        return SimpleNamespace(
            total=total,
            available=available,
            percent=min(100.0, max(0.0, ((total - available) / total) * 100.0)),
        )


def disk_preview_max_edge() -> int:
    """Max long edge for JPEG files under ~/.rawviewer_cache/previews (grid tier is separate)."""
    raw = os.environ.get("RAWVIEWER_DISK_PREVIEW_MAX", "512").strip()
    try:
        v = int(raw)
    except Exception:
        v = 512
    return max(256, min(v, 1024))


def memory_preview_max_edge() -> int:
    """Max in-memory preview for progressive single-image RAW display (not disk size)."""
    raw = os.environ.get("RAWVIEWER_MEMORY_PREVIEW_MAX", "1920").strip()
    try:
        v = int(raw)
    except Exception:
        v = 1920
    return max(512, min(v, 4096))


def _jpeg_bytes_max_edge(jpeg_data: bytes, max_edge: int) -> bytes:
    """Downscale JPEG bytes so disk cache entries stay thumbnail-sized."""
    if not jpeg_data or max_edge <= 0:
        return jpeg_data
    try:
        import io

        from PIL import Image, ImageOps

        pil_image = Image.open(io.BytesIO(jpeg_data))
        pil_image = ImageOps.exif_transpose(pil_image)
        w, h = pil_image.size
        if max(w, h) <= max_edge:
            # We must re-save to strip EXIF orientation and ensure it stays rotated.
            out = io.BytesIO()
            pil_image.save(out, format="JPEG", quality=85)
            return out.getvalue()
        scale = max_edge / float(max(w, h))
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        resized = pil_image.resize((new_w, new_h), Image.Resampling.HAMMING)
        if resized.mode != "RGB":
            resized = resized.convert("RGB")
        out = io.BytesIO()
        resized.save(out, format="JPEG", quality=85)
        return out.getvalue()
    except Exception:
        return jpeg_data


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            return max(minimum, min(int(raw), maximum))
        except ValueError:
            pass
    return max(minimum, min(default, maximum))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            return max(minimum, min(float(raw), maximum))
        except ValueError:
            pass
    return max(minimum, min(default, maximum))


def _preview_cache_adaptive_enabled() -> bool:
    raw = os.environ.get("RAWVIEWER_PREVIEW_CACHE_ADAPTIVE", "1").strip().lower()
    return raw in ("1", "true", "yes", "on")
