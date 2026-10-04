"""商談準備エージェントに自分の商談相手・目的を入力する対話スクリプト

※ 本リポジトリ限定の追加教材。書籍本文には登場しません。

12-2_agent_pipeline.py のパイプライン（Planner → リスク判定 → 必要時HITL① →
Executor → Synthesizer → HITL②）のノードをそのまま import して使い、
商談相手と目的を自分で入力して、Plannerが立てる計画と、HITLの承認・修正・却下を
標準入力で体験する。12-2 のデモは Command(resume=...) で自動再開するが、
本スクリプトではその判断を実際に人間（あなた）が入力する。

2モードで動く。

1. 既定（APIキー不要）:
   計画は擬似Planner、ドラフト統合は擬似Synthesizer（いずれも _common.py）。
   HITLの二段構え（リスクのある計画だけ確認①、最終ドラフトは必ず確認②）と、
   却下したドラフトが外に出ない流れをキーなしで確かめられる。

2. 本番（任意）:
   環境変数 ANTHROPIC_API_KEY が設定され、anthropic パッケージが入っていれば、
   Planner と Synthesizer だけを実際の Claude 呼び出しに差し替える。
   Executor のステップ実行の判断と3つの情報源（Web・CRM・過去案件）は
   ダミーのままなので、確かめられるのは「実モデルが立てる計画」と
   「実モデルが書くドラフト」まで。
   （anthropic の導入: pip install anthropic）
   ANTHROPIC_API_KEY の代わりに USE_BEDROCK=1 を設定すると、AWS の認証情報を使って
   Amazon Bedrock 経由で Claude を呼ぶ（導入: pip install "anthropic[bedrock]"）。

実行:
    python interactive_meeting_prep.py

ヒント:
    - ダミー情報源が知っている企業は「みらい物流」だけ。他社名を入れると、
      調査が空振りして「未確認」だらけのドラフトになる動きを確かめられる
    - 目的に「同名企業」という語を含めると、計画確認（HITL①）が発動する

対話は空行または Ctrl+C で終了する。

注意:
    モデル名（MODEL）は新しい世代が出るたびに更新される。本番モードで
    not_found_error 等が出たら、Anthropic 公式ドキュメントで現行の
    モデル名を確認して置き換えること。
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from _common import plan_review_reasons, pseudo_plan, pseudo_synthesize

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

# ファイル名にハイフンを含むため、12-2 のモジュールはパス指定で読み込む
_here = pathlib.Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "agent_pipeline", _here / "12-2_agent_pipeline.py")
_pipeline = importlib.util.module_from_spec(_spec)
# sys.modules に登録してから実行する。未登録だと、State（from __future__ import
# annotations 下の TypedDict）の型ヒントを LangGraph が評価するときに
# モジュールのグローバルを参照できず NameError: 'Annotated' になる
sys.modules[_spec.name] = _pipeline
_spec.loader.exec_module(_pipeline)

# Executor が呼べるのはこの3ツールだけ（12-6。実体はダミー情報源）
ALLOWED_TOOLS = ("web_search", "crm_search", "past_case_search")


# --- 本番モード：Planner / Synthesizer を実モデルに差し替える -----------------
def _extract_json(text: str, open_ch: str, close_ch: str):
    """モデルの返答から最初のJSON配列／オブジェクトを取り出す。失敗時は None。"""
    start, end = text.find(open_ch), text.rfind(close_ch)
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _make_live_planner(client):
    def planner(state):
        prompt = (
            "あなたは営業商談準備エージェントのPlannerです。次のゴールを達成する"
            "調査計画を立ててください。\n"
            f"ゴール: {state['goal']}\n\n"
            "使えるツールは次の3つだけです。\n"
            "- web_search: 公開Web情報を検索する\n"
            "- crm_search: 社内CRMで過去のやり取りを検索する\n"
            "- past_case_search: 自社の過去案件ログから類似事例を検索する\n\n"
            "2〜4ステップのJSON配列だけを出力してください。各要素は\n"
            '{"desc": "ステップの説明", "tool": "ツール名", "arg": "検索クエリ"}'
        )
        resp = client.messages.create(
            model=_model_name(), max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        steps = _extract_json(text, "[", "]")
        plan = [s for s in (steps or [])
                if isinstance(s, dict) and s.get("tool") in ALLOWED_TOOLS
                and s.get("desc") and s.get("arg")]
        if not plan:
            print("  [メモ] 計画をJSONとして解釈できなかったため、擬似Plannerに切り替えます。")
            plan = pseudo_plan(state["goal"])
        return {
            "plan": plan,
            "plan_review_reasons": plan_review_reasons(state["goal"], plan),
            "step_idx": 0,
            "findings": [],
        }
    return planner


def _make_live_synthesizer(client):
    _KEYS = ("準備メモ", "想定質問", "確認したい質問", "提案骨子", "未確認事項と出典")

    def synthesizer(state):
        findings = json.dumps(state["findings"], ensure_ascii=False, indent=2)
        prompt = (
            "あなたは営業商談準備エージェントのSynthesizerです。次の調査結果を、"
            "商談準備ドキュメントのドラフトに統合してください。\n"
            f"ゴール: {state['goal']}\n\n"
            f"# 調査結果（findings）\n{findings}\n\n"
            "JSONオブジェクトだけを出力してください。キーは「準備メモ」「想定質問」"
            "「確認したい質問」「提案骨子」「未確認事項と出典」の5つ。"
            "statusがokのcontentだけを根拠に使い、それ以外の状態は未確認事項に残してください。"
            "調査結果に無いことは推測で埋めず、「未確認」と明記してください。"
        )
        resp = client.messages.create(
            model=_model_name(), max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        draft = _extract_json(text, "{", "}")
        if not isinstance(draft, dict) or not all(k in draft for k in _KEYS):
            print("  [メモ] ドラフトをJSONとして解釈できなかったため、擬似Synthesizerに切り替えます。")
            draft = pseudo_synthesize(state["goal"], state["findings"])
        return {"draft": draft}
    return synthesizer


def build_graph(planner_fn, synthesizer_fn):
    """12-2 と同じグラフを、Planner / Synthesizer だけ差し替え可能にして組む。"""
    b = StateGraph(_pipeline.State)
    b.add_node("planner", planner_fn)
    b.add_node("review_plan", _pipeline.review_plan)
    b.add_node("replan", _pipeline.replan)
    b.add_node("executor", _pipeline.executor)
    b.add_node("tools", ToolNode(_pipeline.TOOLS))
    b.add_node("advance", _pipeline.advance)
    b.add_node("synthesizer", synthesizer_fn)
    b.add_node("review_draft", _pipeline.review_draft)
    b.add_node("withhold", _pipeline.withhold)
    b.add_node("finalize", _pipeline.finalize)

    b.add_edge(START, "planner")
    b.add_conditional_edges("planner", _pipeline.route_after_planner,
                            {"review_plan": "review_plan", "executor": "executor"})
    b.add_conditional_edges("review_plan", _pipeline.route_after_plan,
                            {"executor": "executor", "replan": "replan"})
    b.add_edge("replan", END)
    b.add_conditional_edges("executor", _pipeline.route_executor,
                            {"tools": "tools", "advance": "advance"})
    b.add_edge("tools", "executor")
    b.add_conditional_edges("advance", _pipeline.route_after_advance,
                            {"executor": "executor", "synthesizer": "synthesizer"})
    b.add_edge("synthesizer", "review_draft")
    b.add_conditional_edges("review_draft", _pipeline.route_after_review,
                            {"finalize": "finalize", "withhold": "withhold"})
    b.add_edge("withhold", END)
    b.add_edge("finalize", END)
    return b.compile(checkpointer=InMemorySaver())


def _setup_client():
    if not (os.environ.get("ANTHROPIC_API_KEY") or _use_bedrock()):
        print("[メモ] ANTHROPIC_API_KEY / USE_BEDROCK 未設定のため、擬似Planner／擬似Synthesizerで動きます。")
        return None
    try:
        import anthropic
        if _use_bedrock():
            import boto3  # noqa: F401  # anthropic[bedrock] で入る。存在確認のみ
    except ImportError:
        print("[メモ] anthropic パッケージ未導入のため、擬似Planner／擬似Synthesizerで動きます"
              "（本番モードは pip install anthropic、Bedrock は pip install 'anthropic[bedrock]'）。")
        return None
    if _use_bedrock():
        # 認証は AWS の認証情報（AWS_PROFILE 等）から。APIキーは使わない
        return anthropic.AnthropicBedrock(
            aws_region=os.environ.get("AWS_REGION", BEDROCK_REGION))
    return anthropic.Anthropic()


def _show_plan(plan: list) -> None:
    print("  [Plannerの計画]")
    for i, step in enumerate(plan, 1):
        print(f"    {i}. {step['desc']}（tool={step['tool']} / arg={step['arg']}）")


def _decide_plan(plan: list) -> dict:
    """HITL①の判断を標準入力で受ける。承認／却下／ステップの絞り込み（修正）。"""
    while True:
        try:
            raw = input("  計画の判断 [y=承認 / n=却下 / 残すステップ番号（例: 1,3）]> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return {"approved": False}
        if raw.lower() == "y":
            return {"approved": True}
        if raw.lower() == "n":
            return {"approved": False}
        try:
            keep = [int(x) for x in raw.replace("、", ",").split(",")]
            edited = [plan[i - 1] for i in keep if 1 <= i <= len(plan)]
        except ValueError:
            edited = []
        if edited:
            # 修正後の計画もステップの形（desc/tool/arg）を保っている（12-2 の前提どおり）
            return {"edited_plan": edited}
        print("  入力を解釈できませんでした。y / n / 番号（例: 1,3）で入力してください。")


def _show_draft(draft: dict) -> None:
    print("  [ドラフト（この時点では外に出ていない）]")
    for key, value in draft.items():
        print(f"    {key}: {value}")


def run_session(graph, goal: str, thread_id: str) -> None:
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 50}

    # 1) HITL①（リスクのある計画のみ）またはHITL②（ドラフト）まで進む
    paused = graph.invoke({"goal": goal}, config)
    plan = graph.get_state(config).values.get("plan", [])
    _show_plan(plan)

    payload = paused["__interrupt__"][0].value
    if "reasons" in payload:  # HITL①で停止中
        print("  [HITL① 計画確認] 一時停止の理由:", "、".join(payload["reasons"]))
        decision = _decide_plan(plan)
        paused = graph.invoke(Command(resume=decision), config)
        if "__interrupt__" not in paused:
            print("  → 計画を却下したため、調査は実行されませんでした。")
            return
        if "edited_plan" in decision:
            print("  → 修正した計画で実行しました。")
    else:
        print("  → 計画に確認理由なし。HITL①を挟まず自動で調査を実行しました。")

    # 2) HITL②（最終レビュー）は必ず停止する
    payload = paused["__interrupt__"][0].value
    print("  [HITL② 最終レビュー]", payload["ask"])
    _show_draft(payload["draft"])
    try:
        raw = input("  このドラフトを承認しますか？ [y=承認 / それ以外=却下]> ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raw = ""

    # 3) 承認なら finalize が出力し、却下なら withhold が「外に出さない」
    graph.invoke(Command(resume={"approved": raw.lower() == "y"}), config)


def main() -> None:
    client = _setup_client()
    if client:
        graph = build_graph(_make_live_planner(client), _make_live_synthesizer(client))
    else:
        graph = build_graph(_pipeline.planner, _pipeline.synthesizer)

    print("情報源はダミーです（Web・CRMが知っているのは「みらい物流」のみ。"
          "他社名では調査が空振りする動きを確かめられます）。")
    print("目的に「同名企業」という語を含めると、計画確認（HITL①）が発動します。")

    session = 0
    while True:
        try:
            company = input("\n商談相手の企業名（空行で終了）> ").strip()
            if not company:
                break
            purpose = input("商談の目的（例: 新規提案）> ").strip() or "新規提案"
        except (EOFError, KeyboardInterrupt):
            print()
            break
        session += 1
        run_session(graph, f"{company} {purpose}", f"interactive-{session}")


if __name__ == "__main__":
    main()
