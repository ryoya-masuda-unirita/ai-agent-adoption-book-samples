"""社内ナレッジ横断エージェントに自分の質問を入力する対話スクリプト

※ 本リポジトリ限定の追加教材。書籍本文には登場しません。

11-2_agent_pipeline.py のパイプライン（検索Subgraph → 権限チェック＋
Conditional Edge → 回答生成＋引用付与）をそのまま import して使い、
自分の質問を入力しながら、利用者の権限（部署・機密レベル）によって
同じ質問でも見える結果が変わることを確かめる。

2モードで動く。

1. 既定（APIキー不要）:
   回答生成は 11-2 と同じ擬似モデル。検索（ダミー社内コーパス）・権限フィルタ・
   引用対応表という「構造」は本番と同じに動くので、権限による見え方の違いは
   キーなしで確かめられる。

2. 本番（任意）:
   環境変数 ANTHROPIC_API_KEY が設定され、anthropic パッケージが入っていれば、
   回答生成ノードだけを実際の Claude 呼び出しに差し替える。検索対象は引き続き
   ダミーコーパス（_common.py）で、権限フィルタと引用対応表の仕組みは同一。
   （anthropic の導入: pip install anthropic）
   ANTHROPIC_API_KEY の代わりに USE_BEDROCK=1 を設定すると、AWS の認証情報を使って
   Amazon Bedrock 経由で Claude を呼ぶ（導入: pip install "anthropic[bedrock]"）。

実行:
    python interactive_knowledge_agent.py                  # 一般の開発者として
    python interactive_knowledge_agent.py --role hr        # 人事部員として
    python interactive_knowledge_agent.py --dept dev --clearance 5   # 権限を個別指定
    python interactive_knowledge_agent.py --question "給与テーブルはどこ？"  # 1回だけ実行

対話は空行または Ctrl+C で終了する。

注意:
    モデル名（MODEL）は新しい世代が出るたびに更新される。本番モードで
    not_found_error 等が出たら、Anthropic 公式ドキュメントで現行の
    モデル名を確認して置き換えること。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import re

from langgraph.graph import StateGraph, START, END

# 実行時は公式ドキュメントで最新のモデル名を確認して置き換える
MODEL = "claude-sonnet-4-6"
# Bedrock では推論プロファイルのIDで指定する（jp. は日本国内で処理するプロファイル）
BEDROCK_MODEL = "jp.anthropic.claude-sonnet-4-6"
BEDROCK_REGION = "ap-northeast-1"


def _use_bedrock() -> bool:
    """USE_BEDROCK=1 なら、Anthropic の API ではなく Amazon Bedrock 経由で呼ぶ。"""
    return os.environ.get("USE_BEDROCK") == "1"


def _model_name() -> str:
    return BEDROCK_MODEL if _use_bedrock() else MODEL

# ファイル名にハイフンを含むため、11-2 のモジュールはパス指定で読み込む（11-8 と同じ手法）
_here = pathlib.Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "agent_pipeline", _here / "11-2_agent_pipeline.py")
_pipeline = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_pipeline)


# 利用者プリセット（ダミーコーパスの権限メタデータと対応。--dept/--clearance で上書き可）
ROLES = {
    "dev": ("一般の開発者", {"dept": "dev", "clearance": 1}),
    "accounting": ("経理部員", {"dept": "accounting", "clearance": 2}),
    "hr": ("人事部員", {"dept": "hr", "clearance": 3}),
    "exec": ("役員", {"dept": "exec", "clearance": 5}),
}


def _make_live_answer(client):
    """回答生成ノードの本番版を作る。擬似モデルの代わりに実際の Claude を呼ぶ。

    引用の3段の仕組み（番号付与→番号で引用→対応表で引き直し）は
    11-2 の answer ノードと同一。対応表に無い番号は引用に採用しないので、
    モデルが文書IDをでっち上げても引用には入らない（本文11-7）。
    """
    def answer_live(state):
        numbered = {i + 1: doc for i, doc in enumerate(state["visible"])}
        refs = "\n".join(f"[{n}] {doc['text']}" for n, doc in numbered.items())
        prompt = (
            "あなたは社内ナレッジ検索アシスタントです。次の番号付き参考文書だけを"
            "根拠に、質問へ日本語で簡潔に答えてください。根拠に使った文書は、"
            "文中に [番号] の形で引用してください。参考文書に書かれていないことは"
            "推測で補わないでください。\n\n"
            f"# 質問\n{state['question']}\n\n# 参考文書\n{refs}"
        )
        resp = client.messages.create(
            model=_model_name(), max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        # 答えに現れた番号を対応表から実在のID・抜粋へ引き直す（11-2 と同じ）
        used = [int(n) for n in re.findall(r"\[(\d+)\]", text)]
        citations = [
            {"doc_id": numbered[n]["id"], "excerpt": numbered[n]["excerpt"]}
            for n in dict.fromkeys(used) if n in numbered
        ]
        return {"answer": text, "citations": citations}

    return answer_live


def build_graph(answer_fn):
    """11-2 と同じグラフを、回答生成ノードだけ差し替え可能にして組む。"""
    b = StateGraph(_pipeline.State)
    b.add_node("search", _pipeline.build_search_subgraph())  # 検索Subgraph（11-4/11-6）
    b.add_node("check_permission", _pipeline.check_permission)  # 権限フィルタ（11-3）
    b.add_node("answer", answer_fn)
    b.add_node("no_answer", _pipeline.no_answer)
    b.add_edge(START, "search")
    b.add_edge("search", "check_permission")
    b.add_conditional_edges("check_permission", _pipeline.route_after_permission)
    b.add_edge("answer", END)
    b.add_edge("no_answer", END)
    return b.compile()


def _setup_client():
    """本番モードが使えるなら Anthropic クライアントを返し、無理ならその理由を案内する。"""
    if not (os.environ.get("ANTHROPIC_API_KEY") or _use_bedrock()):
        print("[メモ] ANTHROPIC_API_KEY / USE_BEDROCK 未設定のため、擬似モデルで回答を生成します。")
        return None
    try:
        import anthropic
        if _use_bedrock():
            import boto3  # noqa: F401  # anthropic[bedrock] で入る。存在確認のみ
    except ImportError:
        print("[メモ] anthropic パッケージ未導入のため、擬似モデルで回答を生成します"
              "（本番モードは pip install anthropic、Bedrock は pip install 'anthropic[bedrock]'）。")
        return None
    if _use_bedrock():
        # 認証は AWS の認証情報（AWS_PROFILE 等）から。APIキーは使わない
        return anthropic.AnthropicBedrock(
            aws_region=os.environ.get("AWS_REGION", BEDROCK_REGION))
    return anthropic.Anthropic()


def ask(graph, question: str, user: dict) -> None:
    result = graph.invoke({"question": question, "user": user})
    retrieved = len(result.get("retrieved", []))
    visible = len(result.get("visible", []))
    print(f"  検索で取り出し {retrieved}件 → 権限フィルタ通過 {visible}件")
    print(f"  回答: {result['answer']}")
    print(f"  引用: {[c['doc_id'] for c in result.get('citations', [])] or 'なし'}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="社内ナレッジ横断エージェント（11-2）に自分の質問を入力する")
    parser.add_argument("--role", choices=ROLES, default="dev",
                        help="利用者プリセット（既定: dev＝一般の開発者）")
    parser.add_argument("--dept", help="部署を個別指定（例: dev / hr / exec / accounting）")
    parser.add_argument("--clearance", type=int,
                        help="機密レベルを個別指定（1〜5。コーパス側の必要レベルは1/3/5）")
    parser.add_argument("--question", help="質問を1回だけ実行して終了する")
    args = parser.parse_args()

    label, user = ROLES[args.role]
    user = dict(user)
    if args.dept:
        user["dept"] = args.dept
        label = f"dept={user['dept']}"
    if args.clearance is not None:
        user["clearance"] = args.clearance
        label = f"dept={user['dept']}"

    client = _setup_client()
    answer_fn = _make_live_answer(client) if client else _pipeline.answer
    graph = build_graph(answer_fn)

    print(f"利用者: {label}（dept={user['dept']} / clearance={user['clearance']}）")
    print("検索対象はダミー社内コーパス（_common.py の6文書）です。")
    print("『給与テーブルはどこ？』を --role dev と --role hr で聞き比べると、"
          "権限フィルタの効き方を確かめられます。")

    if args.question:
        print(f"\n質問: {args.question}")
        ask(graph, args.question, user)
        return

    while True:
        try:
            question = input("\n質問（空行で終了）> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            break
        ask(graph, question, user)


if __name__ == "__main__":
    main()
