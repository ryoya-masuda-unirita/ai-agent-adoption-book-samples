"""3-4 取り出した結果をLLMに渡す（取り込み→検索→生成のプロンプト組み立て）。

本書 3-4 節は「検索で取り出したチャンクを、質問と一緒にプロンプトへ詰めて
LLM に渡す」という生成段階を扱う。本文に API 呼び出しコードは載せていないため、
このサンプルでは次の2モードを用意する。

1. 既定（APIキー不要）:
   3-3 の検索結果をプロンプトに差し込み、組み上がったプロンプトを表示する。
   3-4 本文の「参考文書欄にチャンクを差し込んでモデルに送る」手前までを再現。

2. 生成あり（任意）:
   環境変数 ANTHROPIC_API_KEY が設定され、anthropic パッケージが入っていれば、
   組み立てたプロンプトを実際に Claude へ送って回答を表示する。
   （anthropic の導入: pip install anthropic）
   ANTHROPIC_API_KEY の代わりに USE_BEDROCK=1 を設定すると、AWS の認証情報を使って
   Amazon Bedrock 経由で Claude を呼ぶ（導入: pip install "anthropic[bedrock]"）。

実行:
    python 3-4_rag_with_generation.py
    ANTHROPIC_API_KEY=sk-... python 3-4_rag_with_generation.py   # 生成あり
    USE_BEDROCK=1 AWS_PROFILE=... python 3-4_rag_with_generation.py   # 生成あり（Bedrock）
"""

import os

from importlib import import_module

# 取り込み・検索は 3-3 のものを再利用する
rag_minimal = import_module("3-3_rag_minimal")

# モデル名は鮮度メモに従い実行時点の最新を確認すること
MODEL = "claude-sonnet-4-5"
# Bedrock では推論プロファイルのIDで指定する（jp. は日本国内で処理するプロファイル）
BEDROCK_MODEL = "jp.anthropic.claude-sonnet-4-5-20250929-v1:0"
BEDROCK_REGION = "ap-northeast-1"

PROMPT_TEMPLATE = """\
以下の社内文書を参考に、質問に答えてください。
文書に書かれていないことは「資料からは分かりません」と答えてください。

# 参考文書
{context}

# 質問
{question}
"""


def build_prompt(question: str, chunks: list[str]) -> str:
    context = "\n".join(f"- {c}" for c in chunks)
    return PROMPT_TEMPLATE.format(context=context, question=question)


def use_bedrock() -> bool:
    """USE_BEDROCK=1 なら、Anthropic の API ではなく Amazon Bedrock 経由で呼ぶ。"""
    return os.environ.get("USE_BEDROCK") == "1"


def make_client():
    """接続先に応じたクライアントとモデル名を返す。messages.create の書き方は共通。"""
    import anthropic

    if use_bedrock():
        # 認証は AWS の認証情報（AWS_PROFILE 等）から。APIキーは使わない
        region = os.environ.get("AWS_REGION", BEDROCK_REGION)
        return anthropic.AnthropicBedrock(aws_region=region), BEDROCK_MODEL
    return anthropic.Anthropic(), MODEL  # APIキーは環境変数 ANTHROPIC_API_KEY から


def generate_with_claude(prompt: str) -> str | None:
    """ANTHROPIC_API_KEY か USE_BEDROCK=1 があれば Claude で回答を生成する。なければ None。"""
    if not (os.environ.get("ANTHROPIC_API_KEY") or use_bedrock()):
        return None
    try:
        client, model = make_client()
    except ImportError:
        print("[メモ] anthropic 未導入のため生成はスキップ（pip install anthropic）")
        return None

    message = client.messages.create(
        model=model,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def main() -> None:
    collection = rag_minimal.build_collection()

    question = "休みを取りたいときの手続きを教えてください。"

    # 検索：質問に近いチャンクを取り出す（ここでは上位2件）
    result = collection.query(query_texts=[question], n_results=2)
    chunks = result["documents"][0]

    prompt = build_prompt(question, chunks)
    print("=== 組み立てたプロンプト ===")
    print(prompt)

    answer = generate_with_claude(prompt)
    if answer is not None:
        print("=== Claude の回答 ===")
        print(answer)
    else:
        print("[メモ] ANTHROPIC_API_KEY / USE_BEDROCK 未設定のため、生成は行わずプロンプト表示のみ。")


if __name__ == "__main__":
    main()
