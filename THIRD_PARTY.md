# Third-party components / 参考元

This public package does not include mpv, FFmpeg, SVP, or their runtime DLLs. Obtain those programs separately. The application is MIT-licensed; third-party components retain their own licenses.

Python 3.12.14, Tcl/Tk 8.6.12, OpenSSL 3.5.8, libffi and Microsoft runtime components are included. Runtime licenses and Python's third-party notices are in `licenses/`. Keep the Microsoft Distributable Code terms in Python-LICENSE.txt when redistributing.

PyInstaller's bootloader exception permits distribution of the bundled application; its GPL text and exception are included. No upstream runtime source was intentionally modified.

`licenses/python-packages` includes dependency notices and build-tool notices; inclusion of a notice does not mean every build tool is embedded in the executable.

| Package | Version | License metadata |
|---|---|---|
| PySocks | 1.7.1 | BSD |
| altgraph | 0.17.5 | MIT |
| attrs | 26.1.0 | MIT |
| certifi | 2026.7.22 | MPL-2.0 |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.1 | MIT |
| h11 | 0.16.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| isodate | 0.7.2 | Copyright (c) 2021, Hugo van Kemenade and contributors |
| lxml | 6.1.3 | BSD-3-Clause |
| outcome | 1.3.0.post0 | MIT OR Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pefile | 2024.8.26 | MIT |
| protobuf | 7.36.2 | 3-Clause BSD License |
| pycountry | 26.2.16 | LGPL-2.1-only |
| pycparser | 3.0 | BSD-3-Clause |
| pycryptodome | 3.23.0 | BSD, Public Domain |
| pyinstaller | 6.22.3 | GPLv2-or-later with a special exception which allows to use PyInstaller to build and distribute non-free programs (including commercial ones) |
| pyinstaller-hooks-contrib | 2026.7 |  |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause |
| requests | 2.34.2 | Apache-2.0 |
| setuptools | 84.0.0 | MIT |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| sortedcontainers | 2.4.0 | Apache 2.0 |
| streamlink | 8.6.1 | BSD-2-Clause |
| trio | 0.34.0 | MIT OR Apache-2.0 |
| trio-websocket | 0.12.2 |  |
| urllib3 | 2.8.0 | MIT |
| websocket-client | 1.9.2 | Apache-2.0 |
| wsproto | 1.3.2 | MIT |

## Corresponding source

pycountry (LGPL-2.1), certifi's certificate bundle (MPL-2.0), and lxml with its native libraries have corresponding source archives in `corresponding-source/`. lxml reports libxml2 2.11.9 / libxslt 1.1.45. The matching upstream Windows build release 2026.05.17 uses libiconv 1.17.1 and zlib 1.3.2; its build scripts and all four pinned submodule sources are included. See REBUILD.md for replacement/rebuild instructions. The LGPL libraries may be modified and replaced; reverse engineering for debugging such modifications is not prohibited by this application.

## Design and protocol references (not bundled)

- TVTComment: https://github.com/noriokun4649/TVTComment — compact posting UI and NicoLive posting protocol reference; no source copied.
- NicoJK: https://github.com/xtne6f/NicoJK — DwmFlush pacing design reference; no source copied. Application uses mpv display-resample.
- N Air protobuf schema: https://github.com/n-air-app/nicolive-comment-protobuf/tree/2e852e08df016888aefa602ec6c5be6a558eb3d9 — implemented required field subset without generated-code vendoring.
- NDGRClient: https://github.com/tsukumijima/NDGRClient — protocol research reference; no source bundled.
