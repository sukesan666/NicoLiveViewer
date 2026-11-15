# NicoLive Studio — ニコ生ビューア

**[日本語](#日本語) | [English](#english)**

---

<a id="日本語"></a>

## 日本語

Windows・Linux・macOS に対応した非公式ニコニコ生放送ビューアです。  
**mpv** による高画質再生（デバンド・高品質アップスケール対応）と滑らかなコメント描画を備え、番組の検索・再生・録画・自動予約をひとつの画面でまとめて操作できます。

詳しい操作方法は [USAGE.md](USAGE.md)、実装と確認の範囲は [FEATURE_STATUS.md](FEATURE_STATUS.md)、変更履歴は [RELEASE_NOTES.md](RELEASE_NOTES.md) を参照してください。

### 主な機能

- **マルチプラットフォーム対応**：Windows・Linux・macOS（Intel / Apple Silicon 両対応の Universal 2 ビルド対応）で動作
- **安全・簡単なブラウザログイン**：専用プロファイルのブラウザ（Microsoft Edge / Google Chrome / Chromium）でログイン・二段階認証を行い、ログイン状態を OS ごとに暗号化して保存（Windows は DPAPI、Linux / macOS はユーザー専用鍵による AES-256-GCM）
- **高画質映像＆高フレームレートコメント**：
  - mpv のデバンド（`--deband=yes`）と高品質スケーラ（`ewa_lanczossharp` / `mitchell`）を標準適用
  - ウィンドウのサイズ変更・全画面表示に追従するコメント描画、フォント・文字サイズ・コメント FPS（1～240、既定 120fps）の設定
  - Windows 環境での SVP（SmoothVideo Project）フレーム補間連携に対応
- **複数番組の同時視聴・コメント投稿**：色コメント・匿名（184）でのコメント投稿、番組ごとの音量調整・消音
- **録画・再生の柔軟なモード**：mpv を開かない録画専用モード、再生＋録画、映像（`.ts`）とコメント（`.comments.jsonl`）の別ファイル保存・同時再生
- **自動予約・プッシュ通知連動**：番組・ユーザー・チャンネルの予約、公式キーワード・タグによる番組予約の自動追加
- **ライブラリ＆履歴**：録画一覧、視聴履歴、タイムシフトと保存動画のシーク（コメント連動）
- **カスタマイズ＆快適機能**：日本語／English 切替、ダーク／ライトテーマ、画面の拡大率（100～250％）・位置・サイズの保存、設定の書き出し・読込・バックアップ、アプリ内 NG 設定、通知音と静かな時間帯、自動起動・自動スリープ抑制

> 再生・録画の同時処理数は共通の設定で最大 16 です。実際に処理できる数は PC・回線・配信方式によります。

---

### 必要な外部ツール

配布パッケージやソースコードには **mpv**・**FFmpeg**・**SVP** を同梱していません。お使いの OS に合わせて導入してください（標準パスに入っていれば自動検出し、設定画面から手動指定もできます）。

- **[mpv](https://mpv.io/installation/)**（再生・コメント表示に必須）
- **[FFmpeg](https://ffmpeg.org/download.html)**（配信方式やサムネイル生成に応じて必要）
- **Microsoft Edge / Google Chrome / Chromium**（ブラウザログイン時に使用）
- **SVP 4**（任意・Windows のみ。SVP 本体と SVP 付属の mpv 環境が必要）

| OS | 導入コマンドの例 | 設定・データの保存先 |
| :--- | :--- | :--- |
| **Windows** | 公式インストーラー等から `mpv.exe` / `ffmpeg.exe` を配置 | `%APPDATA%\NicoLiveViewer` |
| **Linux (Ubuntu/Debian)** | `sudo apt install python3-venv python3-tk mpv ffmpeg` | `~/.config/NicoLiveViewer` |
| **macOS (Homebrew)** | `brew install mpv ffmpeg` | `~/Library/Application Support/NicoLiveViewer` |

---

### 起動方法（配布パッケージ版）

- **Windows**：フォルダ全体を展開し、`NicoLiveViewer.exe` を開きます（`_internal` フォルダも同じ場所に必要です）。
- **Linux**：展開したフォルダ内の `./NicoLiveViewer` を実行します。
- **macOS**：`NicoLiveViewer.app` を開きます。

初回はログイン画面から「ブラウザでログイン」を押し、開いた専用ブラウザでログインと二段階認証を完了してからブラウザのウィンドウを閉じてください。

---

### ソースコードから実行

Python 3 と Tkinter が必要です。依存パッケージは [requirements.txt](requirements.txt) に記載しています。

#### Windows (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe launch.py
```

#### Linux / macOS (Bash / Zsh)
```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python launch.py
```

#### テストの実行
```powershell
# Windows (PowerShell)
$env:PYTHONPATH = 'src;tests'
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
```bash
# Linux / macOS
PYTHONPATH=src:tests ./.venv/bin/python -m unittest discover -s tests
```

---

### パッケージ（実行ファイル）のビルド

- **Windows (`dist/NicoLiveViewer/NicoLiveViewer.exe`)**
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\build-public.ps1
  ```
- **Linux (`dist/NicoLiveViewer/NicoLiveViewer`)**
  ```bash
  bash ./build-linux.sh
  ```
- **macOS Universal 2 (`dist/NicoLiveViewer.app` — Intel & Apple Silicon 両対応)**
  ```bash
  # python.org 公式の macOS Universal2 インストーラーの Python で .venv を作成した場合、
  # 1つの .app に arm64 と x86_64 の両方がまとまります（単一アーキテクチャ環境では自動でネイティブビルドになります）。
  bash ./build-macos.sh
  ```

> ビルドスクリプト単体では第三者ライセンス通知・対応ソースの配布準備は完了しません。アプリのソースは [MIT License](LICENSE)、依存物と再配布時の注意は [THIRD_PARTY.md](THIRD_PARTY.md) を確認してください。

---

### 自動予約・免責事項

- 番組・ユーザーの放送状態を定期照会せず、**放送開始のプッシュ通知**で再生・録画を開始します。予約対象のユーザー・チャンネルはニコニコアカウントでもフォローします（予約解除時にサイト側のフォローは解除しません）。
- 公式キーワード・タグの検索は日本時間の毎日午前3時（取得失敗時は1時間おきに再試行）です。通知の未配信・無効化・期限切れや、PC のスリープ・アプリ終了中は自動実行できません。
- 本アプリはニコニコの公式製品ではありません。視聴権限のある番組を対象に利用してください。Web 版 NG との自動同期と公式ギフト演出は含まれません。

---

<a id="english"></a>

## English

An unofficial Nico Nico Live viewer for **Windows, Linux, and macOS**.  
It uses **mpv** for high-quality video playback (with built-in debanding and high-quality upscaling) and smooth scrolling comments, providing a unified interface for searching, watching, recording, and scheduling broadcasts.

For detailed usage instructions, see [USAGE.md](USAGE.md) (Japanese). For implementation and verification coverage, see [FEATURE_STATUS.md](FEATURE_STATUS.md) (Japanese), and for changes, see [RELEASE_NOTES.md](RELEASE_NOTES.md) (Japanese).

### Key Features

- **Cross-Platform Support**: Runs on Windows, Linux, and macOS (including single-package Universal 2 builds for both Intel and Apple Silicon Macs).
- **Secure Browser Login**: Complete login and two-step verification in an isolated browser profile (Microsoft Edge, Google Chrome, or Chromium). Session state is encrypted per user (DPAPI on Windows; AES-256-GCM with a user-only key on Linux and macOS).
- **High-Quality Video & High-FPS Comments**:
  - Built-in mpv debanding (`--deband=yes`) and high-quality scaling (`ewa_lanczossharp` / `mitchell`) enabled out of the box
  - Comments automatically scale with mpv resizing and fullscreen mode; configurable font, size, and comment FPS (1–240, default 120 FPS)
  - Optional SVP (SmoothVideo Project) frame interpolation support on Windows
- **Multi-Stream Playback & Comment Posting**: Watch multiple broadcasts, post colored or anonymous (184) comments, and control volume or mute per broadcast.
- **Flexible Recording & Playback**: Recording-only mode without opening mpv, watch and record simultaneously, and save video (`.ts`) and comments (`.comments.jsonl`) separately to replay together with full seek sync.
- **Automated Push Scheduling**: Schedule broadcasts, users, and channels, or automatically add official broadcast schedules by keyword or tag.
- **Library & History**: Recording library, viewing history, and time-shift / local recording seeking.
- **Customization**: Japanese and English UI, dark and light themes, UI scaling (100–250%), settings export/import/backup, in-app comment filtering (blocked words/users), notification sounds, quiet hours, autostart, and sleep prevention.

> A shared setting allows up to 16 concurrent playback and recording tasks. The actual number depends on your computer, network connection, and streaming format.

---

### External Requirements

Prebuilt packages and this repository do not bundle **mpv**, **FFmpeg**, or **SVP**. Install them for your OS (they are auto-detected from standard paths or can be selected in Settings):

- **[mpv](https://mpv.io/installation/)** (required for playback and comment display)
- **[FFmpeg](https://ffmpeg.org/download.html)** (required depending on streaming format and for thumbnail generation)
- **Microsoft Edge / Google Chrome / Chromium** (used for interactive browser login)
- **SVP 4** (optional, Windows only; requires SVP and its compatible mpv runtime)

| OS | Example Installation | Settings & Data Directory |
| :--- | :--- | :--- |
| **Windows** | Install `mpv.exe` and `ffmpeg.exe` | `%APPDATA%\NicoLiveViewer` |
| **Linux (Ubuntu/Debian)** | `sudo apt install python3-venv python3-tk mpv ffmpeg` | `~/.config/NicoLiveViewer` |
| **macOS (Homebrew)** | `brew install mpv ffmpeg` | `~/Library/Application Support/NicoLiveViewer` |

---

### Getting Started (Prebuilt Package)

- **Windows**: Extract the entire folder and run `NicoLiveViewer.exe` (keep the `_internal` folder next to the executable).
- **Linux**: Run `./NicoLiveViewer` from the extracted folder.
- **macOS**: Open `NicoLiveViewer.app`.

Open the login window, click **Log in with browser**, complete login and two-step verification in the dedicated browser window, and then close that browser window to save your encrypted session.

---

### Run from Source

Requires Python 3 and Tkinter. Dependencies are pinned in [requirements.txt](requirements.txt).

#### Windows (PowerShell)
```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe launch.py
```

#### Linux / macOS (Bash / Zsh)
```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python launch.py
```

#### Run Tests
```powershell
# Windows (PowerShell)
$env:PYTHONPATH = 'src;tests'
.\.venv\Scripts\python.exe -m unittest discover -s tests
```
```bash
# Linux / macOS
PYTHONPATH=src:tests ./.venv/bin/python -m unittest discover -s tests
```

---

### Build Executables

- **Windows (`dist/NicoLiveViewer/NicoLiveViewer.exe`)**
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\build-public.ps1
  ```
- **Linux (`dist/NicoLiveViewer/NicoLiveViewer`)**
  ```bash
  bash ./build-linux.sh
  ```
- **macOS Universal 2 (`dist/NicoLiveViewer.app` — Intel & Apple Silicon in one bundle)**
  ```bash
  # Using the official python.org macOS universal2 Python installer produces a single
  # universal2 .app bundle (automatically falls back to native arch on single-arch Python).
  bash ./build-macos.sh
  ```

> These build scripts alone do not complete the preparation of third-party license notices or corresponding source code for redistribution. See the [MIT License](LICENSE) for the application source, and [THIRD_PARTY.md](THIRD_PARTY.md) for dependencies and redistribution notes.

---

### Scheduling & Disclaimer

- The app does not periodically poll broadcast or user status; playback and recording start when a **live-start push notification** arrives. Users and channels added to schedules are followed on your Nico Nico account (removing a schedule does not unfollow them on the site).
- Official keyword and tag searches run daily at 03:00 Japan time (or retry hourly on failure). Scheduled jobs cannot run if notifications are disabled/expired or while the computer is asleep or the app is closed.
- This app is not an official Nico Nico product. Use it only with broadcasts you are authorized to watch. Automatic synchronization with the web player's block lists and official gift effects are not included.
