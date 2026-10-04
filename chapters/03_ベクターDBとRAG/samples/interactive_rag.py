"""自分の質問でRAGを試す対話型サンプル（本リポジトリ限定の追加教材）。

このスクリプトは書籍本文には登場しない。3-3（取り込み→検索）と
3-4（プロンプト組み立て→生成）の実装を土台に、読者が自分で考えた質問で
RAG の挙動を確かめられるようにしたもの。

2段構えで動く。

1. 検索のみ（既定・APIキー不要）:
   埋め込みはローカルモデル（sentence-transformers）なので、質問に近い
   チャンクの取り出しと、組み上がるプロンプトの確認までキーなしで試せる。

2. 生成あり（--generate 指定時のみ）:
   環境変数 ANTHROPIC_API_KEY と anthropic パッケージ（pip install anthropic）が
   必要。組み立てたプロンプトを実際に Claude へ送り、回答を表示する。
   従量課金が発生し、質問と参考文書が Anthropic の API に送信される点に注意。
   ANTHROPIC_API_KEY の代わりに USE_BEDROCK=1 を設定すると、AWS の認証情報を使って
   Amazon Bedrock 経由で Claude を呼ぶ（導入: pip install "anthropic[bedrock]"）。

実行:
    python interactive_rag.py                      # 対話モード（検索のみ）
    python interactive_rag.py --question "..."     # 1回だけ実行
    python interactive_rag.py --generate           # 生成あり（要APIキー）
    USE_BEDROCK=1 AWS_PROFILE=... python interactive_rag.py --generate   # 生成あり（Bedrock）

対話モードは空行の入力、または Ctrl+C / Ctrl+D で終了できる。
初回実行時は埋め込みモデル（paraphrase-multilingual-MiniLM-L12-v2、約 0.5GB）の
ダウンロードが走る。
"""

import argparse
import os
import sys

from importlib import import_module

# 取り込み・検索は 3-3、プロンプト組み立ては 3-4 のものを再利用する
rag_minimal = import_module("3-3_rag_minimal")
rag_generation = import_module("3-4_rag_with_generation")

MODEL = rag_generation.MODEL  # モデル名の更新は 3-4 側の定数を書き換える


def check_generate_ready() -> None:
    """--generate に必要な前提を起動時に確認する。足りなければ1行で案内して終了。"""
    if not (os.environ.get("ANTHROPIC_API_KEY") or rag_generation.use_bedrock()):
        sys.exit("環境変数 ANTHROPIC_API_KEY（または USE_BEDROCK=1）が未設定です。"
                 "--generate なしなら検索のみ試せます。")
    try:
        import anthropic  # noqa: F401  # 存在確認のみ
    except ImportError:
        sys.exit("anthropic パッケージが未導入です。pip install anthropic を実行してください。")


def generate_with_claude(prompt: str) -> str:
    """組み立てたプロンプトを Claude に送り、回答テキストを返す。"""
    client, model = rag_generation.make_client()  # 接続先の切り替えは 3-4 と共通
    message = client.messages.create(
        model=model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def answer_question(collection, question: str, n_results: int, generate: bool) -> None:
    """1つの質問について、検索結果（と任意で生成結果）を表示する。"""
    # 検索：質問に近いチャンクを取り出す。距離（小さいほど近い）も表示する
    result = collection.query(query_texts=[question], n_results=n_results)
    chunks = result["documents"][0]
    distances = result["distances"][0]

    print("=== 検索結果（距離が小さいほど質問に近い） ===")
    for rank, (chunk, distance) in enumerate(zip(chunks, distances), start=1):
        print(f"{rank}. (distance={distance:.4f}) {chunk}")

    # 3-4 と同じテンプレートでプロンプトを組み立てる
    prompt = rag_generation.build_prompt(question, chunks)
    print("\n=== 組み立てたプロンプト ===")
    print(prompt)

    if generate:
        print("=== Claude の回答 ===")
        print(generate_with_claude(prompt))
    else:
        print("[メモ] 生成まで試すには --generate を付けて実行（要 ANTHROPIC_API_KEY か USE_BEDROCK=1）。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="自分の質問でRAG検索（と任意で生成）を試す対話型サンプル"
    )
    parser.add_argument(
        "--question",
        help="質問を1回だけ実行する。省略時は対話モード（空行 / Ctrl+C で終了）",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="検索結果をもとに Claude で回答を生成する（要 ANTHROPIC_API_KEY か USE_BEDROCK=1）",
    )
    parser.add_argument(
        "--n-results",
        type=int,
        default=2,
        help="検索で取り出すチャンク数（既定: 2。登録文書は3件）",
    )
    args = parser.parse_args()

    if args.generate:
        check_generate_ready()

    # 3-3 と同じ手順で、社内規程を模した3文書を取り込んだコレクションを作る
    print("[準備] 文書を取り込んでいます（初回は埋め込みモデルのダウンロードあり）...")
    collection = rag_minimal.build_collection()

    if args.question:
        answer_question(collection, args.question, args.n_results, args.generate)
        return

    # 対話モード：空行または Ctrl+C / Ctrl+D で終了
    print("質問を入力してください（空行 / Ctrl+C で終了）。")
    print("例: パスワードは何日ごとに変えればいい？ / 出張のお金って戻ってくる？")
    while True:
        try:
            question = input("\n質問> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n終了します。")
            break
        if not question:
            print("終了します。")
            break
        answer_question(collection, question, args.n_results, args.generate)


if __name__ == "__main__":
    main()
