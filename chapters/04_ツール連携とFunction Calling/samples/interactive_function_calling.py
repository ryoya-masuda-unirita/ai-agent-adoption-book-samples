"""自分の質問でFunction Callingの発火を観察する対話型サンプル（本リポジトリ限定の追加教材）。

このスクリプトは書籍本文には登場しない。4-2 の実装（ツール定義 TOOLS・
実装 get_stock・ディスパッチ run_tool・モデル名 MODEL）を土台に、読者が
自分の質問を入力して「どのツールが・どの引数で呼ばれたか」を観察できる
ようにしたもの。

モデルの判断（tool_use が返るかどうか）を観察するには、環境変数
ANTHROPIC_API_KEY と anthropic パッケージ（pip install anthropic）が必要。
従量課金が発生し、質問文とツール定義が Anthropic の API に送信される点に注意。
ANTHROPIC_API_KEY の代わりに USE_BEDROCK=1 を設定すると、4-2 と同じく
Amazon Bedrock 経由で呼ぶ（導入: pip install "anthropic[bedrock]"）。

キー未設定のときは、4-2 のドライランに倣い、①「アプリ→モデルに何が
送られるか」だけを表示する構造確認モードで動く（モデルの判断は再現しない）。

実行:
    python interactive_function_calling.py                     # 対話モード
    python interactive_function_calling.py --question "..."    # 1回だけ実行

対話モードは空行の入力、または Ctrl+C / Ctrl+D で終了できる。
"""

import argparse
import os
import sys

from importlib import import_module

# ツール定義・実装・モデル名は 4-2 のものを再利用する
fc_minimal = import_module("4-2_function_calling_minimal")

TOOLS = fc_minimal.TOOLS
MODEL = fc_minimal.MODEL  # モデル名の更新は 4-2 側の定数を書き換える
run_tool = fc_minimal.run_tool


def check_api_ready() -> bool:
    """APIで動かせるかを起動時に確認し、案内を表示する。"""
    if not (os.environ.get("ANTHROPIC_API_KEY") or fc_minimal.use_bedrock()):
        print("[案内] 環境変数 ANTHROPIC_API_KEY が未設定です。")
        print("       ツールが発火するかどうかはモデルが判断するため、観察にはAPIキーが必要です。")
        print("       export ANTHROPIC_API_KEY=sk-ant-... を設定して再実行してください。")
        print("       （Amazon Bedrock 経由なら export USE_BEDROCK=1）")
        print("       このまま続けると、①（アプリ→モデルに何が送られるか）だけを表示します。\n")
        return False
    try:
        import anthropic  # noqa: F401  # 存在確認のみ
    except ImportError:
        sys.exit("anthropic パッケージが未導入です。pip install anthropic を実行してください。")
    return True


def observe_with_api(question: str) -> None:
    """実際にAPIを呼び、どのツールがどの引数で呼ばれたかを表示する。"""
    client, model = fc_minimal.make_client()  # 接続先の切り替えは 4-2 と共通
    messages = [{"role": "user", "content": question}]
    round_count = 0

    while True:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            tools=TOOLS,
            messages=messages,
        )
        round_count += 1
        print(f"[往復{round_count}] stop_reason = {response.stop_reason!r}")

        if response.stop_reason != "tool_use":
            # ツール要求がなければ、テキストの最終回答が来ている
            if round_count == 1:
                print("  -> ツールは発火しませんでした（モデルはツール不要と判断）")
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
            print(f"  [発火] {block.name}(引数={block.input})")
            try:
                result = run_tool(block.name, block.input)
                print(f"  [実行結果] {result!r}")
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,  # 要求と結果を id で対応づける
                        "content": result,
                    }
                )
            except Exception as exc:  # 失敗は is_error でモデルに伝える
                print(f"  [実行エラー] {exc}")
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(exc),
                        "is_error": True,
                    }
                )
        messages.append({"role": "user", "content": tool_results})


def observe_dry(question: str) -> None:
    """キーなしの構造確認モード。①（アプリ→モデル）に何が送られるかだけを示す。"""
    print("① アプリ → モデル：tools とユーザーの質問を送る")
    print(f"   tools = {[t['name'] for t in TOOLS]}")
    print(f"   messages = [{{'role': 'user', 'content': {question!r}}}]")
    print("② 以降（tool_use が返るかどうか）はモデルの判断のため、APIキーなしでは再現できません。")
    print("   固定の流れのドライランは python 4-2_function_calling_minimal.py で確認できます。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="自分の質問でFunction Callingの発火（どのツールがどの引数で呼ばれたか）を観察する"
    )
    parser.add_argument(
        "--question",
        help="質問を1回だけ実行する。省略時は対話モード（空行 / Ctrl+C で終了）",
    )
    args = parser.parse_args()

    api_ready = check_api_ready()
    observe = observe_with_api if api_ready else observe_dry

    if args.question:
        observe(args.question)
        return

    # 対話モード：空行または Ctrl+C / Ctrl+D で終了
    print("質問を入力してください（空行 / Ctrl+C で終了）。")
    print("例: 商品A-100の在庫はいくつ？ / A-100とB-200、在庫が多いのはどっち？")
    while True:
        try:
            question = input("\n質問> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n終了します。")
            break
        if not question:
            print("終了します。")
            break
        observe(question)


if __name__ == "__main__":
    main()
