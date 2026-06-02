#!/usr/bin/env python3
"""
NT Flash Tools — Build Script (Cross-platform)
================================================
Đóng gói ứng dụng thành file thực thi cho Windows, Linux, macOS.

Cách dùng:
    python build.py              # Build mặc định (one-folder)
    python build.py --onefile    # Build thành 1 file duy nhất
    python build.py --clean      # Xóa thư mục build cũ trước khi build

Yêu cầu:
    pip install pyinstaller
"""

import os
import sys
import shutil
import subprocess
import platform


# ── Cấu hình ──
APP_NAME = "NTFlashTools"
ENTRY = "app.py"
ICON_WIN = None     # Đặt path icon .ico nếu có, vd: "assets/icon.ico"
ICON_MAC = None     # Đặt path icon .icns nếu có
VERSION = "1.0.0"

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(ROOT, "dist")
BUILD_DIR = os.path.join(ROOT, "build")


def get_os():
    s = sys.platform
    if s == 'win32':
        return 'windows'
    elif s == 'darwin':
        return 'macos'
    else:
        return 'linux'


def find_package_path(package_name):
    """Tìm đường dẫn package đã cài trong site-packages."""
    try:
        mod = __import__(package_name)
        pkg_dir = os.path.dirname(mod.__file__)
        return pkg_dir
    except ImportError:
        return None


def build_command(onefile=False):
    """Tạo câu lệnh PyInstaller."""
    os_name = get_os()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", APP_NAME,
        "--distpath", DIST_DIR,
        "--workpath", BUILD_DIR,
    ]

    # ── Mode ──
    if onefile:
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    # ── Ẩn console ──
    if os_name == 'windows':
        cmd.append("--noconsole")  # Ẩn CMD trên Windows
    elif os_name == 'macos':
        cmd.append("--windowed")   # .app bundle trên macOS

    # ── Icon ──
    if os_name == 'windows' and ICON_WIN and os.path.isfile(ICON_WIN):
        cmd += ["--icon", ICON_WIN]
    elif os_name == 'macos' and ICON_MAC and os.path.isfile(ICON_MAC):
        cmd += ["--icon", ICON_MAC]

    # ── Thu thập data: qfluentwidgets resources ──
    qfw_path = find_package_path("qfluentwidgets")
    if qfw_path:
        sep = ";" if os_name == 'windows' else ":"
        cmd += ["--add-data", f"{qfw_path}{sep}qfluentwidgets"]

    # ── Thu thập darkdetect ──
    dd_path = find_package_path("darkdetect")
    if dd_path:
        sep = ";" if os_name == 'windows' else ":"
        cmd += ["--add-data", f"{dd_path}{sep}darkdetect"]

    # ── Thu thập edlclient (tích hợp EDL tool) ──
    edl_path = find_package_path("edlclient")
    if edl_path:
        sep = ";" if os_name == 'windows' else ":"
        cmd += ["--add-data", f"{edl_path}{sep}edlclient"]

    # ── Hidden imports (PyQt6 plugins thường bị miss) ──
    hidden = [
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PyQt6.QtSvg",
        "PyQt6.QtSvgWidgets",
        "qfluentwidgets",
        "darkdetect",
        "edlclient",
        "edlclient.Library.sahara",
        "edlclient.Library.firehose",
        "edlclient.Library.Connection.usblib",
        "usb",
        "usb.core",
        "usb.backend",
        "serial",
    ]
    for h in hidden:
        cmd += ["--hidden-import", h]

    # ── Exclude không cần thiết (giảm kích thước) ──
    excludes = [
        "tkinter", "unittest", "pydoc", "doctest", "lib2to3",
        "matplotlib", "numpy", "pandas", "scipy",
    ]
    for e in excludes:
        cmd += ["--exclude-module", e]

    # ── Entry point ──
    cmd.append(ENTRY)

    return cmd


def clean():
    """Xóa thư mục build/dist cũ."""
    for d in [BUILD_DIR, DIST_DIR]:
        if os.path.exists(d):
            print(f"🗑  Xóa {d}")
            shutil.rmtree(d)
    spec = os.path.join(ROOT, f"{APP_NAME}.spec")
    if os.path.exists(spec):
        os.remove(spec)


def copy_extras():
    """Copy các file cần thiết vào thư mục dist sau khi build."""
    os_name = get_os()
    output_dir = os.path.join(DIST_DIR, APP_NAME)

    if not os.path.isdir(output_dir):
        # Onefile mode — dist chứa file trực tiếp
        output_dir = DIST_DIR

    # Copy config.json mẫu
    config_src = os.path.join(ROOT, "config.json")
    if os.path.isfile(config_src):
        shutil.copy2(config_src, os.path.join(output_dir, "config.json"))
        print(f"📄  Copied config.json")

    # Copy platform-tools nếu có
    pt_src = os.path.join(ROOT, "platform-tools")
    pt_dst = os.path.join(output_dir, "platform-tools")
    if os.path.isdir(pt_src) and not os.path.isdir(pt_dst):
        shutil.copytree(pt_src, pt_dst)
        print(f"📁  Copied platform-tools/")

    # Copy scrcpy nếu có
    sc_src = os.path.join(ROOT, "scrcpy")
    sc_dst = os.path.join(output_dir, "scrcpy")
    if os.path.isdir(sc_src) and not os.path.isdir(sc_dst):
        shutil.copytree(sc_src, sc_dst)
        print(f"📁  Copied scrcpy/")

    # Copy edl tool nếu có (fallback local)
    edl_src = os.path.join(ROOT, "edl")
    edl_dst = os.path.join(output_dir, "edl")
    if os.path.isdir(edl_src) and not os.path.isdir(edl_dst):
        shutil.copytree(edl_src, edl_dst)
        print(f"📁  Copied edl/")


def main():
    args = sys.argv[1:]
    onefile = "--onefile" in args
    do_clean = "--clean" in args

    os_name = get_os()
    mode = "one-file" if onefile else "one-folder"

    print(f"╔══════════════════════════════════════════╗")
    print(f"║  NT Flash Tools — Build ({os_name})      ║")
    print(f"║  Mode: {mode:<33}║")
    print(f"╚══════════════════════════════════════════╝")
    print()

    # Kiểm tra PyInstaller
    try:
        import PyInstaller
        print(f"✔  PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("✖  PyInstaller chưa cài. Chạy: pip install pyinstaller")
        sys.exit(1)

    if do_clean:
        clean()

    cmd = build_command(onefile)
    print(f"\n🔨  Lệnh build:\n   {' '.join(cmd)}\n")

    rc = subprocess.call(cmd, cwd=ROOT)
    if rc != 0:
        print(f"\n✖  Build thất bại (mã {rc})")
        sys.exit(rc)

    print(f"\n✔  Build thành công!")

    if not onefile:
        copy_extras()

    # Kết quả
    output_dir = os.path.join(DIST_DIR, APP_NAME)
    if onefile:
        ext = ".exe" if os_name == 'windows' else ""
        output_file = os.path.join(DIST_DIR, f"{APP_NAME}{ext}")
        print(f"\n📦  File đầu ra: {output_file}")
    else:
        print(f"\n📦  Thư mục đầu ra: {output_dir}")

    print(f"\n{'='*50}")
    print(f"  Hoàn tất! Chạy ứng dụng:")
    if onefile:
        if os_name == 'windows':
            print(f"    dist\\{APP_NAME}.exe")
        else:
            print(f"    ./dist/{APP_NAME}")
    else:
        if os_name == 'windows':
            print(f"    dist\\{APP_NAME}\\{APP_NAME}.exe")
        elif os_name == 'macos':
            print(f"    open dist/{APP_NAME}.app")
        else:
            print(f"    ./dist/{APP_NAME}/{APP_NAME}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
