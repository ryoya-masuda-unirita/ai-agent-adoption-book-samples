"""4-2 Anthropic SDKでFunction Callingを直接書く（tool_use → tool_result の往復）。

本書 4-2 / 4-3 節の掲載コードを、そのまま実行できる形にまとめたもの。
1つのツール（架空の在庫照会 get_stock）をモデルに渡し、
「tool_use を受け取る → アプリ側で実行する → tool_result を返す」という
Function Calling の一往復（必要なら複数往復）を動かす。

このサンプルは2モードで動く。

1. 既定（APIキー不要）:
   実際の API は呼ばず、ツール定義とダミー実行の流れ・期待される往復の構造を
   コンソールに表示する。本文の「モデルは要求を出すだけ・実行はアプリ」という
   分業を、API なしでも追えるようにしたもの。

2. 本番（任意）:
   環境変数 ANTHROPIC_API_KEY が設定され、anthropic パッケージが入っていれば、
   実際に Claude へ問い合わせ、tool_use を取り出して get_stock を実行し、
   tool_result を返して最終回答までループする。
   （anthropic の導入: pip install anthropic）
   ANTHROPIC_API_KEY の代わりに USE_BEDROCK=1 を設定すると、AWS の認証情報を使って
   Amazon Bedrock 経由で Claude を呼ぶ（導入: pip install "anthropic[bedrock]"）。

実行:
    python 4-2_function_calling_minimal.py
    ANTHROPIC_API_KEY=sk-... python 4-2_function_calling_minimal.py   # 本番
    USE_BEDROCK=1 AWS_PROFILE=... python 4-2_function_calling_minimal.py   # 本番（Bedrock）

注意:
    モデル名（MODEL）は新しい世代が出るたびに更新される。実行時は
    Anthropic 公式ドキュメントで現行のモデル名を確認して置き換えること。
    確かめたいのはモデルの賢さではなく、tools / tool_use / tool_result の
    やり取りの「型」であり、この型はモデルが新しくなっても変わらない。
"""

import os

# 実行時は公式ドキュメントで最新のモデル名を確認して置き換える
MODEL = "claude-sonnet-4-6"
# Bedrock では推論プロファイルのIDで指定する（jp. は日本国内で処理するプロファイル）
BEDROCK_MODEL = "jp.anthropic.claude-sonnet-4-6"
BEDROCK_REGION = "ap-northeast-1"


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

# --- ツールの「定義」：モデルに渡す仕様の宣言（実装ではない） ---
TOOLS = [
    {
        "name": "get_stock",
        "description": (
            "指定した商品の現在の在庫数を返す。商品コード（例：A-100）を渡すと、"
            "その商品の在庫数を整数で返す。在庫照会の問い合わせに使い、"
            "発注や入出庫の処理には使わない。商品コードが存在しない場合は0を返す。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "product_code": {
                    "type": "string",
                    "description": "在庫を調べたい商品の商品コード（例：A-100）",
                }
            },
            "required": ["product_code"],
        },
    }
]


# --- ツールの「実装」：実際にアプリ側で動く処理。モデルはこの中身を知らない ---
def get_stock(product_code: str) -> str:
    """在庫DBへの問い合わせを模したダミー実装。実数の代わりに固定値を返す。"""
    dummy_stock = {"A-100": 42, "B-200": 0}
    count = dummy_stock.get(product_code, 0)
    return str(count)


# tool 名 → 実装のディスパッチ表（ツールが増えたらここに足す）
TOOL_IMPLEMENTATIONS = {
    "get_stock": get_stock,
}


def run_tool(name: str, tool_input: dict) -> str:
    """tool_use の name / input から、対応する実装を呼び出す。

    実務では、ここで入力検証・権限確認・（取り消せない操作なら）人間の承認を挟む。
    詳細は本書 4-5 / 第15章を参照。
    """
    impl = TOOL_IMPLEMENTATIONS.get(name)
    if impl is None:
        # 未知のツール名はエラーとして返す（モデルに失敗を伝える）
        raise ValueError(f"未知のツール: {name}")
    return impl(**tool_input)


def run_with_api(question: str) -> None:
    """ANTHROPIC_API_KEY か USE_BEDROCK=1 があれば、実際に Function Calling の往復を回す。"""
    client, model = make_client()
    messages = [{"role": "user", "content": question}]

    while True:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            tools=TOOLS,
            messages=messages,
        )
        print(f"[stop_reason] {response.stop_reason}")

        if response.stop_reason != "tool_use":
            # ツール要求がなければ、テキストの最終回答が来ている
            for block in response.content:
                if block.type == "text":
                    print("=== 最終回答 ===")
                    print(block.text)
            return

        # モデルの応答（tool_use を含む）を会話履歴に積む
        messages.append({"role": "assistant", "content": response.content})

        # 要求された全ての tool_use を実行し、tool_result をまとめて返す
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"[tool_use] {block.name}({block.input})")
            try:
                result = run_tool(block.name, block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,  # 要求と結果を id で対応づける
                        "content": result,
                    }
                )
            except Exception as exc:  # 失敗は is_error でモデルに伝える
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(exc),
                        "is_error": True,
                    }
                )
        messages.append({"role": "user", "content": tool_results})


def run_dry(question: str) -> None:
    """APIキーなしのドライラン。往復の構造とダミー実行だけを示す。"""
    print("[メモ] ANTHROPIC_API_KEY / USE_BEDROCK 未設定のため、API は呼ばずに流れだけを表示します。")
    print(f"質問: {question}\n")

    print("① アプリ → モデル：tools とユーザーの質問を送る")
    print(f"   tools = {[t['name'] for t in TOOLS]}\n")

    print("② モデル → アプリ：tool_use を返す（実際にはモデルが判断する部分）")
    simulated_tool_use = {"name": "get_stock", "input": {"product_code": "A-100"}}
    print(f"   stop_reason = 'tool_use'")
    print(f"   tool_use = {simulated_tool_use}\n")

    print("③ アプリ：ツールを実行する")
    result = run_tool(simulated_tool_use["name"], simulated_tool_use["input"])
    print(f"   get_stock(product_code='A-100') -> {result!r}\n")

    print("④ アプリ → モデル：tool_result を返す（tool_use_id で対応づけ）")
    print(f"   tool_result.content = {result!r}\n")

    print("⑤ モデル：結果を踏まえて最終回答（例：『商品A-100の在庫は42個です』）")
    print("\n[メモ] 実際にモデルに判断・回答させるには ANTHROPIC_API_KEY か USE_BEDROCK=1 を設定して再実行。")


def main() -> None:
    question = "商品A-100の在庫はいくつ？"
    if os.environ.get("ANTHROPIC_API_KEY") or use_bedrock():
        try:
            run_with_api(question)
            return
        except ImportError:
            print("[メモ] anthropic 未導入のためドライランに切り替え（pip install anthropic）\n")
    run_dry(question)


if __name__ == "__main__":
    main()
