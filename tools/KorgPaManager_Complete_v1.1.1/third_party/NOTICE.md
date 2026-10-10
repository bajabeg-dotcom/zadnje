# Third-party components bundled with KorgPaManager

KorgPaManager's own source is distributed under the MIT License in `../LICENSE`.
The complete offline release also carries the following unmodified or clearly
identified third-party components so the program can run without downloading a
Python package at first launch.

## Mido 1.3.3

- Purpose: Standard MIDI File parsing, editing and writing.
- Source: https://github.com/mido/mido
- Distribution: the `mido/` source package is bundled in this release.
- License: MIT; see `MIDO_LICENSE.txt`.
- Local compatibility change: `mido/version.py` exposes the fixed bundled
  version without importing the otherwise unused external `packaging` module.
  MIDI parsing/writing code is unchanged.

## CPython 3.12.10 Windows 64-bit offline installer

- Purpose: one-time private Python/Tkinter runtime setup when Windows has no
  usable Python installation.
- Official source:
  https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
- Bundled path: `../runtime_installer/python-3.12.10-amd64.exe`
- SHA-256:
  `67b5635e80ea51072b87941312d00ec8927c4db9ba18938f7ad2d27b328b95fb`
- MD5 published on the Python 3.12.10 release page:
  `5eddb0b6f12c852725de071ae681dde4`
- Publisher: Python Software Foundation.
- License: see `PYTHON_LICENSE.txt`.

The Windows launcher installs only the interpreter, standard library and
Tcl/Tk into `runtime/python` inside the extracted application folder. It does
not add Python to PATH, associate `.py` files, install a global launcher, or
need network access. The runtime installer itself is Authenticode-signed by the
Python Software Foundation; Windows users can verify this through file
Properties → Digital Signatures.
