# Third-party components / 参考元

## Application source

NicoLive Studioのアプリケーションソースは [MIT License](LICENSE) で公開します。依存ライブラリ、外部プログラム、参照したプロジェクトにはそれぞれのライセンスが適用されます。アプリのMITライセンスが、それらのライセンスに置き換わるものではありません。

Pythonの依存パッケージと固定バージョンは [requirements.txt](requirements.txt) を参照してください。このソースリポジトリには、バイナリ配布用の `licenses/`、`corresponding-source/`、`REBUILD.md` は含めていません。

## Building and redistributing binaries

`build-public.ps1` はPyInstallerでアプリと依存物をまとめ、公開用の説明文書をコピーします。第三者のライセンス通知や対応ソースを自動で収集するスクリプトではありません。

バイナリを再配布する場合は、実際に使用したPython・パッケージ・ネイティブライブラリ・ビルドツールのバージョンと同梱内容を確認し、それぞれのライセンスに必要な著作権表示、ライセンス本文、変更の記録、ソースの提供や差し替え・再ビルド方法を用意してください。Pythonに含まれるTcl/Tk、OpenSSL、libffiやWindowsランタイム等も、実際のビルドで含まれるものを確認する必要があります。

特に、依存物にはpycountry、certifi、lxmlとそのネイティブ依存物などがあり、ソース提供等の条件は配布する実物とそのライセンスに基づいて確認してください。PyInstallerのライセンスとbootloader exceptionも確認対象です。この文書だけで再配布に必要な通知・ソースの提供を完了したことにはなりません。

`build-public.ps1` はmpv・FFmpeg・SVPや、それらのランタイムDLLを同梱する設定ではありません。これらは別途入手して使用します。独自に同梱して配布する場合は、採用するビルドのライセンスと配布条件も確認してください。

## Design and protocol references

次のプロジェクトを設計・プロトコル調査の参考元として記載します。この一覧は、各プロジェクトを本アプリに丸ごと同梱していることを意味しません。

- [nicoLiveCheckTool](https://github.com/guest-nico/nicoLiveCheckTool) — プッシュ通知のアイコンからの配信者識別と、ユーザーフォローAPIの参考元。ソース内の `PushReceiver.getItem` / `FollowUser.join3` への参照に対応します。
- [N Air comment protobuf schema](https://github.com/n-air-app/nicolive-comment-protobuf/tree/2e852e08df016888aefa602ec6c5be6a558eb3d9) — コメント受信のNDGR protobufスキーマ。`comments.py` に参照リビジョンを記載しています。
- [TVTComment](https://github.com/noriokun4649/TVTComment) — コメント投稿UI・投稿プロトコルの参考元。
- [NicoJK](https://github.com/xtne6f/NicoJK) — コメント描画タイミングの設計参考元。
- [NDGRClient](https://github.com/tsukumijima/NDGRClient) — コメント受信プロトコルの調査参考元。
