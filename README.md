# 洛友会サーバ復旧

## 全体の流れ
以下は手元のマシンで実行
1. Internet Archive からアーカイブを取得
2. 新サーバに静的ファイルを `rsync` で転送
3. WordPress が生成した HTML ファイル群から WXR ファイルを生成
4. 新サーバの WordPress に WXR ファイルをインポート

## Internet Archive からアーカイブを取得

[pywaybackup](https://pypi.org/project/pywaybackup/) を使う

`poetry` をインストールしたうえで
```sh
poetry install
```

`waybackup` コマンドを実行

```sh
poetry run waybackup --last --url http://www.rakuyukai.org/
```

`waybackup_snapshots` ディレクトリ以下に Internet Archive から取得したファイルが保存される

以下のスクリプトでは `wp-login.php` の存在を WordPress サイトの判定に使っているが、`chugoku/wp-login.php` は Internet Archive に保存されていないので、`touch` でしのぐ。

```sh
touch waybackup_snapshots/www.rakuyukai.org/chugoku/wp-login.php
```

## WordPress が生成した HTML ファイル群から WXR ファイルを生成

```sh
poetry shell
cd waybackup_snapshots
python wordpress_html_to_wxr.py
```

出力:
- `wordpress_export_*.xml`: WXR ファイル (ブログごと)
- `media_files/` 以下に画像等のメディアファイル

`media_files` を `rakuyukai:~/www/old_uploads` に転送する (WXR ファイル内でメディアファイルをここに置くと指定してある)。`old_uploads` は WXR ファイルのインポート後は削除してよい。


## 新サーバの WordPress 設定

`~www/raku/index.php`, `~www/raku/.htaccess` を `~/www` 以下にコピー

`~www/raku/wp-config.php` に以下を追加
```php
/* added by murawaki */
define('WP_HOME', 'https://www.rakuyukai.org');
define('WP_SITEURL', 'https://www.rakuyukai.org/raku');
```

WordPress の設定 -> 一般 で サイトアドレス (URL) を `https://www.rakuyukai.org/raku` から `https://www.rakuyukai.org` に変更

設定 -> パーマリンク でパーマリンク構造をカスタム構造の `/blog/%year%/%monthnum%/%day%/%postname%/` に変更

ツール -> インポート で WordPress を「今すぐインストール」し有効化して「インポーターの実行」をクリック

手元のマシンから `wordpress_export_.xml` をアップロード

「添付ファイルをダウンロードしてインポートする」を選択して続行
