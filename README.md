# NicoLive Studio — ニコ生ビューア

Windows向けの非公式ニコニコ生放送ビューアです。mpvで動画とコメントを表示します。

v1.0.0-beta.14 は録画・予約・番組検索を追加した版です。確認範囲は [FEATURE_STATUS.md](FEATURE_STATUS.md)、操作方法は [USAGE.md](USAGE.md) を参照してください。

- ブラウザログイン・二段階認証、Windowsで暗号化したログイン状態の保存
- 複数番組の再生、色コメント、フォント・サイズ・コメントFPSの数値指定（1～240）
- ダーク／ライト、メニューバー、番組ごとの音量・消音・コメント投稿小窓（匿名184）
- 番組検索、予約再生・録画、録画一覧、視聴履歴
- mpvを起動しない録画専用モード、動画とコメントの保存・同時再生
- タイムシフトのシーク、設定の書き出し・読込・バックアップ、通知音と静かな時間帯
- 選択制のWindows自動起動、通知領域への常駐、自動スリープの抑制

## 起動

公開ZIPを全体ごと展開して `NicoLiveViewer.exe` を開きます。`_internal` はEXEと同じ場所に必要です。
公開ZIPにmpv・FFmpeg・SVPは含まれません。別途用意し、設定画面で指定してください。

- mpv: <https://mpv.io/installation/>
- FFmpeg: <https://ffmpeg.org/download.html>

ログイン画面から専用Edgeを開いてログインし、Edgeを×で閉じるとログイン状態を保存します。
設定と予約・履歴は `%APPDATA%/NicoLiveViewer` に保存され、アプリを更新しても保持されます。
OS再インストール後はブラウザで再ログインしてください。

配布物の条件と参考元は [THIRD_PARTY.md](THIRD_PARTY.md) に記載しています。
Web版NGとの自動同期と公式ギフト演出は含まれません。アプリ内のNG設定は利用できます。

## ソースから実行

Python 3.12 x64を使用します。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe launch.py
```

テストは `$env:PYTHONPATH='src'` を設定して `.\.venv\Scripts\python.exe -m unittest discover -s tests`。
EXEは `build-public.ps1` で作成できます。再配布には外部ライブラリのライセンス・必要な対応ソースも含めてください。

本アプリはニコニコの公式製品ではありません。視聴権限のない番組は再生・録画できません。
