# NicoLive Studio — ニコ生ビューア

Windows向けの非公式ニコニコ生放送ビューアです。mpvで動画とコメントを表示し、番組の検索・再生・録画・予約をまとめて操作できます。

操作方法は [USAGE.md](USAGE.md)、実装と確認の範囲は [FEATURE_STATUS.md](FEATURE_STATUS.md)、変更点は [RELEASE_NOTES.md](RELEASE_NOTES.md) を参照してください。

## 主な機能

- ブラウザログイン・二段階認証と、Windowsで暗号化したログイン状態の保存
- 複数番組の再生、色コメント・匿名184でのコメント投稿、番組ごとの音量・消音
- mpvのサイズ変更・全画面表示に追従するコメント、フォント・サイズ・コメントFPS（1～240）の設定
- mpvを開かない録画専用モード、再生＋録画、動画とコメントの別ファイル保存・同時再生
- 番組・ユーザー・チャンネルの予約、公式キーワード・タグによる番組予約の自動追加
- 録画一覧、視聴履歴、タイムシフトと保存動画のシーク
- 日本語／English、ダーク／ライト、画面の拡大率・位置・サイズの保存
- 設定の書き出し・読込・バックアップ、アプリ内NG、通知音と静かな時間帯
- 選択制のWindows自動起動、通知領域への常駐、自動スリープの抑制

再生・録画の同時処理数は共通の設定で最大16です。実際に処理できる数はPC・回線・配信方式によります。

## 起動

配布版はフォルダ全体を展開し、`NicoLiveViewer.exe` を開きます。`_internal` もEXEと同じ場所に必要です。

`build-public.ps1` で作るパッケージにはmpv・FFmpeg・SVPを含めません。再生用の [mpv](https://mpv.io/installation/) と、配信方式に応じて必要な [FFmpeg](https://ffmpeg.org/download.html) を別途用意し、設定画面で指定してください。SVPを使う場合はSVP本体と対応したmpv環境も必要です。

ログイン画面から専用Edgeを開き、ログインと二段階認証を完了してからウィンドウを閉じます。設定・予約・履歴は `%APPDATA%/NicoLiveViewer` に保存します。保存したログイン状態は同じWindowsユーザーで利用でき、OS再インストール後は再ログインが必要です。

## 自動予約について

番組・ユーザーの放送状態を定期照会せず、放送開始のプッシュ通知で再生・録画を開始します。予約対象のユーザー・チャンネルはニコニコアカウントでもフォローします。予約解除時にサイト側のフォローは解除しません。

公式キーワード・タグの検索は日本時間の毎日午前3時、取得失敗時は1時間おきです。検索結果から作った予約も、開始にはプッシュ通知が必要です。通知の未配信・無効化・期限切れや、PCのスリープ・アプリ終了中は自動実行できません。公式・チャンネル番組の実際の開始通知による自動実行は、引き続き実環境での確認が必要です。

## ソースから実行

Windows用のPythonとTkinterが必要です。依存パッケージは [requirements.txt](requirements.txt) に固定しています。対応Pythonバージョンの範囲は未確定のため、使用する環境で依存関係のインストールと動作を確認してください。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe launch.py
```

テスト：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

EXEの作成は `powershell -ExecutionPolicy Bypass -File .\build-public.ps1`。このスクリプトだけでは第三者ライセンス通知・対応ソースの配布準備は完了しません。アプリのソースは [MIT License](LICENSE)、依存物と再配布時の注意は [THIRD_PARTY.md](THIRD_PARTY.md) を確認してください。

本アプリはニコニコの公式製品ではありません。視聴権限のある番組を対象に利用してください。Web版NGとの自動同期と公式ギフト演出は含まれません。

---

# NicoLive Studio — NicoLive Viewer (English)

An unofficial Nico Nico Live viewer for Windows. It uses mpv to display video and comments, and provides a single interface for searching, watching, recording, and scheduling broadcasts.

For usage instructions, see [USAGE.md](USAGE.md) (Japanese). For implementation and verification coverage, see [FEATURE_STATUS.md](FEATURE_STATUS.md) (Japanese), and for changes, see [RELEASE_NOTES.md](RELEASE_NOTES.md) (Japanese).

## Features

- Browser-based login and two-step verification, with login state encrypted and stored on Windows
- Watch multiple broadcasts, post colored comments or anonymous 184 comments, and control volume or mute for each broadcast
- Comments that adapt to mpv resizing and fullscreen mode; configurable font, size, and comment FPS (1–240)
- Recording-only mode without opening mpv; watch and record at the same time; save video and comments separately and play them together
- Schedule broadcasts, users, and channels; automatically add broadcast schedules using official keywords and tags
- Recording list, viewing history, and seeking in time-shift broadcasts and saved videos
- Japanese and English; dark and light themes; saved display scaling, position, and window size
- Export, import, and back up settings; in-app comment filtering; notification sounds and quiet hours
- Optional Windows startup, minimize to the notification area, and prevent automatic sleep

A shared setting allows up to 16 concurrent playback and recording tasks. The actual number depends on your PC, network connection, and streaming format.

## Getting Started

For the packaged version, extract the entire folder and run `NicoLiveViewer.exe`. The `_internal` folder must be next to the executable.

Packages created with `build-public.ps1` do not include mpv, FFmpeg, or SVP. Install [mpv](https://mpv.io/installation/) for playback and [FFmpeg](https://ffmpeg.org/download.html) if required by the streaming format, then set their paths in the app's settings. To use SVP, you also need SVP itself and a compatible mpv setup.

Open the dedicated Edge browser from the login screen, complete login and two-step verification, and then close the browser window. Settings, schedules, and history are stored in `%APPDATA%/NicoLiveViewer`. The saved login state is available to the same Windows user; you will need to log in again after reinstalling the OS.

## Automatic Scheduling

The app does not periodically poll broadcast or user status. Playback and recording start when a push notification announces that a broadcast has begun. Users and channels added to schedules are also followed through your Nico Nico account. Removing a schedule does not unfollow them on the site.

Official keyword and tag searches run daily at 3:00 a.m. Japan time, or every hour if a search fails. Schedules created from search results also require a push notification to start. Automatic actions are unavailable if notifications are not delivered, are disabled or expired, or while the PC is asleep or the app is closed. Automatic execution based on actual start notifications for official and channel broadcasts still needs verification in a live environment.

## Run from Source

You need Python for Windows and Tkinter. Dependencies are pinned in [requirements.txt](requirements.txt). The supported Python version range has not yet been determined, so verify dependency installation and operation in your environment.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe launch.py
```

Run tests:

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Build the executable with `powershell -ExecutionPolicy Bypass -File .\build-public.ps1`. This script alone does not complete the preparation of third-party license notices or corresponding source code for distribution. See the [MIT License](LICENSE) for the application source, and [THIRD_PARTY.md](THIRD_PARTY.md) for dependencies and redistribution notes.

This app is not an official Nico Nico product. Use it only with broadcasts you are authorized to watch. Automatic synchronization with the web version's comment filters and official gift effects are not included.
