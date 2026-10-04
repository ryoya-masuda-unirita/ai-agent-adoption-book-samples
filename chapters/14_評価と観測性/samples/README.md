# 第14章 評価と観測性 ── サンプルコード

本書第14章の中心である「評価と観測の回り続けるループ」（本文 図14-2-1）を、手元で一周できるサンプルです。第11章の「社内ナレッジ横断AIエージェント」を小さく模した**擬似エージェント**と、ルールベースの**擬似ジャッジ**（LLM-as-a-Judgeの代役）を使い、次の4段階をAPIキー・ネットワーク・外部SaaSなしで確かめられます。

1. 評価データセット（入力＋期待する観点）を用意する（本文 14-3）
2. Trajectory Eval と LLM-as-a-Judge で採点する（本文 14-4）
3. 実行トレース（スパン）という記録の形を見る（本文 14-5）
4. 個別しきい値＋全体合格率の関門で合否を判定する（本文 14-7）

本番では、擬似エージェントを実際のエージェント（第11〜13章）に、擬似ジャッジをLangSmith／Langfuseの評価機能やpromptfooの`llm-rubric`に、擬似スパンのダンプをLangSmith／Langfuseのトレースに置き換えます。

## 収録ファイル

| ファイル | 本文の節 | 確かめられること | APIキー・外部ツール |
|---|---|---|---|
| `_common.py` | 14-3〜14-5 | 評価データセット・擬似エージェント・擬似ジャッジの実装（各スクリプトが共通利用） | 不要 |
| `14-3_dataset.py` | 14-3 評価データセット | 「入力＋期待する観点」の組に、正常系だけでなく異常系（権限外の機密・存在しない情報）が含まれること | 不要 |
| `14-4_trajectory_and_judge.py` | 14-4 評価実行 | Trajectory Eval（道筋の突き合わせ）と LLM-as-a-Judge（観点ごとの採点）の形 | 不要 |
| `14-5_trace_dump.py` | 14-5 本番稼働と観測性 | トレース＝1リクエストの全行程、スパン＝個々の処理、という観測の記録の形 | 不要 |
| `14-7_regression.py` | 14-7 回帰テストをCIで反復 | 個別ケースのしきい値＋全体合格率＋重大ケースの関門。未達なら異常終了（exit 1） | 不要 |
| `14-5_promptfoo/promptfooconfig.yaml` | 14-5 本番稼働と観測性 | promptfoo の設定例（`llm-rubric`＝LLM-as-a-Judge、`threshold`＝個別合格ライン） | Node.js＋OpenAI APIキー |

## 前提

- Python 3.10 以上（動作確認は 3.12）。上記のPythonスクリプト4本は標準ライブラリだけで動くため、`pip install` は不要です（`requirements.txt` は任意依存のメモのみ）。
- `14-5_promptfoo/` の設定例を実際に動かす場合のみ、Node.js（LTS版を推奨）とLLMプロバイダのAPIキーが必要です。手順と注意点は後述の「APIキー・外部サービスを使う場合」を参照してください。

## 実行手順

`samples/` ディレクトリ直下で実行します（`_common.py` を読み込むため）。

```bash
cd samples
python 14-3_dataset.py                 # ① 評価データセットを見る
python 14-4_trajectory_and_judge.py    # ② 採点（Trajectory Eval＋LLM-as-a-Judgeの代役）
python 14-5_trace_dump.py              # ③ 擬似トレース（スパン）を1件ダンプ
python 14-7_regression.py              # ④ 回帰テスト（個別＋全体の関門）
```

いずれも乱数を使わない決め打ちの動作なので、出力は毎回同じです。

- **`14-3_dataset.py`**：3件のケース（D1〜D3）が表示されます。D1が正常系、D2（権限外の機密）とD3（存在しない情報）が異常系であること、各ケースに「期待の観点」と「期待の道筋」が付いていることを確認してください。
- **`14-4_trajectory_and_judge.py`**：ケースごとに、エージェントの回答・たどった道筋・期待の道筋と、観点別のOK/NG、スコア（全ケース 1.00）が表示されます。「たどった道筋」と「期待の道筋」の一致が「道筋一致: OK」に対応する点がTrajectory Evalの骨格です。
- **`14-5_trace_dump.py`**：1リクエスト分のスパンが `hybrid_search → permission_filter → add_citation` の順に、各処理の入出力（in/out）付きで表示されます。このスパンの並びが、そのままTrajectory Evalの評価対象になります。
- **`14-7_regression.py`**：期待される出力は次のとおりです。

```text
=== 回帰テスト（評価データセット全件）===
  [D1] score=1.00 PASS
  [D2] score=1.00 PASS（重大）
  [D3] score=1.00 PASS（重大）

全体合格率: 100%（個別しきい値: 0.8／全体の合格ライン: 90%）
青信号：個別ケースと全体の基準を満たしています。
```

重大な異常系（D2・D3）が1件でも落ちるか、全体合格率が合格ラインを割ると、赤信号のメッセージとともに `exit 1` で終了します。この終了コードをCIの成功・失敗につなげるのが本文 14-7 の基本形です。

## APIキー・外部サービスを使う場合（promptfoo）

`14-5_promptfoo/promptfooconfig.yaml` は、本文 14-5 で紹介する評価ツール promptfoo の設定例です。実行にはNode.jsと、採点用LLMのAPIキー（この設定例ではOpenAI）が必要です。

```bash
cd samples/14-5_promptfoo
export OPENAI_API_KEY=sk-...           # 採点・応答生成に使うプロバイダのキー
npx promptfoo@latest eval              # または npm install -g promptfoo && promptfoo eval
npx promptfoo@latest view              # 結果をブラウザで確認する場合
```

利用時の注意点です。

- **従量課金**：APIキーでの実行はプロバイダの従量課金です。評価は「テストケース数 × アサーション数」だけLLM呼び出しが発生し、`llm-rubric` は1件ごとに採点用の呼び出しも伴います。この設定例は2ケースと小さいものの、ケースを増やしてCIで繰り返すとコストが積み上がります。まず少数ケースで1回実行し、費用感を確かめてから広げてください。
- **モデル名の廃止・変更**：`promptfooconfig.yaml` の `providers:` にある `openai:gpt-5-mini`（17行目）は、執筆時点のモデル名です。廃止・改名された場合はこの行を現行モデルに書き換えます。
- **データ送信**：`prompts` と `tests` に書いた内容はプロバイダ（この例ではOpenAI）へ送信されます。社内文書や実データを入れる場合は、社内の規程を確認してください。また、promptfooは匿名の利用統計を送ることがあります（環境変数 `PROMPTFOO_DISABLE_TELEMETRY=1` で無効化できます）。
- promptfoo自体の更新は速いため、オプション名や既定値は使う時点の公式ドキュメントで確認してください。

### Amazon Bedrock 経由で動かす（任意）

OpenAI の API キーの代わりに、AWS の認証情報で同じ評価を動かす設定例 `promptfooconfig.bedrock.yaml` も用意しています（本リポジトリ限定）。応答を作るモデルと、`llm-rubric` の採点に使うモデルの両方を Amazon Bedrock 上の Claude に向けています。

```bash
cd samples/14-5_promptfoo
export AWS_PROFILE=your-profile        # 既定のプロファイルを使うなら不要
npx promptfoo@latest eval -c promptfooconfig.bedrock.yaml
```

- 既定は東京リージョン（`ap-northeast-1`）の `jp.anthropic.claude-sonnet-4-6` です。別リージョンで動かす場合は、ファイル内の `id` と `region` を書き換えてください
- AWS の従量課金です。`prompts` と `tests` の内容は AWS（Amazon Bedrock）に送信されます
- この2ケースは「不合格」になります。素のモデルは社内文書を持たず、出典を示せないためです。エラーが0件で完走していれば、設定は正しく動いています

## 自分で確かめる

追加の対話型スクリプトはありません。定数を数行書き換えるだけで、自分の条件を試せます。

- **評価ケースを足す**：`_common.py` の `DATASET`（23〜55行目）に、既存ケースと同じ形式の辞書を1件追加します。質問の話題を「社内に実在する文書」として扱わせたい場合は、`_KNOWN`（65〜70行目）にキーワードと文書ID・機密フラグを足してください。追加後に `14-3` → `14-4` → `14-7` を再実行すると、新ケース込みで一周できます。
- **合格ラインを変える**：`14-7_regression.py` の `CASE_THRESHOLD`（18行目、既定 0.8）と `PASS_RATE_LINE`（19行目、既定 0.9）を変えると、同じ採点結果でも青信号・赤信号が変わることを確かめられます。
- **品質の退行を再現する**：`_common.py` 84行目の `visible = [d for d in hits if ...]` を `visible = hits` に書き換えると権限フィルタが無効になり、D2（役員会の議事録）で機密が漏れます。`14-7_regression.py` が重大ケースの失敗を検出して `exit 1` になる——「変更で守りが壊れたことを回帰テストが捕まえる」流れを手元で再現できます。試したら元に戻してください。
- **promptfooで自分の質問を試す**：`promptfooconfig.yaml` の `tests:` にある `vars.input`（質問文）と `assert`（採点観点・`threshold`）を書き換えます。`providers:` を複数並べると、同じテストでモデルを横並び比較できます。

## うまくいかないとき

- `ModuleNotFoundError: No module named '_common'` → `samples/` ディレクトリ直下で実行してください。
- 起動直後に構文エラーになる → Pythonが3.9以前の可能性があります。`python3 --version` で3.10以上を確認してください。
- Windowsで日本語が文字化けする → 環境変数 `PYTHONUTF8=1` を設定して実行してください。
- `14-7_regression.py` が `exit 1` になる → 定数や `_common.py` を書き換えた場合は意図どおりの動作です。変更を元に戻すと青信号に戻ります。
- promptfooで認証エラーになる → `OPENAI_API_KEY` が設定されているか確認してください。モデルが見つからないエラーは `providers:` のモデル名を現行のものへ書き換えます。
