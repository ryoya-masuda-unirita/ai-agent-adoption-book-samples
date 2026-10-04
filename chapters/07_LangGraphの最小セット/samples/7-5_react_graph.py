"""7-5 手書き ReAct を LangGraph で書き直す ── 完全版

本文 7-5 では「構造（状態・ノード・エッジ・分岐・戻り線）」に集中するため、
モデルやツールの準備といった周辺コードを省いた骨格だけを掲載した。
このファイルはその省いた周辺コードを補い、そのまま動かせるようにしたもの。
実行が1周する流れ（START→model→分岐→tools→model→…→END）は、
末尾 main() 上のトレース解説コメントと README を参照（本文から移設）。

APIキーなし（ドライラン）でも動く：在庫確認エージェントを模した FakeReActModel が、
「A-100 を確認 → 品切れ → B-200 を確認 → 在庫あり → 最終回答」という ReAct ループを
回す。本番モード（実際のモデルに自走させる）は README を参照。
"""

from __future__ import annotations

import os
from typing import Annotated

from typing_extensions import TypedDict

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages


# --- 状態（本文掲載） ------------------------------------------------------
# add_messages はリデューサ：ノードが返したメッセージを「上書きではなく追記」する。
class State(TypedDict):
    messages: Annotated[list, add_messages]


# --- 周辺コード：ツール本体（本文では省略） --------------------------------
# ダミー在庫。A-100=品切れ、B-200=在庫あり。
_STOCK = {"A-100": 0, "B-200": 15}


def get_stock(product_code: str) -> int:
    return _STOCK.get(product_code, 0)


def execute_tool_calls(ai_message: AIMessage) -> list[ToolMessage]:
    """直前のモデル応答が要求した道具を実行し、tool_result を返す。"""
    results: list[ToolMessage] = []
    for call in ai_message.tool_calls:
        if call["name"] == "get_stock":
            value = get_stock(call["args"]["product_code"])
            results.append(ToolMessage(content=str(value), tool_call_id=call["id"]))
        else:
            results.append(
                ToolMessage(content="unknown tool", tool_call_id=call["id"])
            )
    return results


# --- 周辺コード：モデル（本文では model.invoke として参照のみ） ------------
def build_model():
    """本番モード：APIキーがあれば LangChain 経由で実モデルを使う。

    無ければドライラン用の FakeReActModel を返す。
    """
    use_bedrock = os.getenv("USE_BEDROCK") == "1"
    if os.getenv("ANTHROPIC_API_KEY") or use_bedrock:
        try:
            from langchain.chat_models import init_chat_model

            if use_bedrock:
                # Amazon Bedrock 経由（要 langchain-aws。認証は AWS の認証情報から）。
                # モデルは推論プロファイルのIDで指定する（jp. は日本国内で処理）
                model = init_chat_model(
                    "jp.anthropic.claude-sonnet-4-6",
                    model_provider="bedrock_converse",
                    region_name=os.getenv("AWS_REGION", "ap-northeast-1"),
                )
            else:
                model = init_chat_model("claude-sonnet-4-6", model_provider="anthropic")
            from langchain_core.tools import tool

            @tool("get_stock")  # execute_tool_calls が探す名前に合わせる（既定は関数名になる）
            def get_stock_tool(product_code: str) -> str:
                """商品コードの在庫数を返す。"""
                return str(get_stock(product_code))

            return model.bind_tools([get_stock_tool]), True
        except Exception as exc:  # noqa: BLE001
            print(f"[メモ] 実モデルの初期化に失敗したためドライランに切替: {exc}")
    return FakeReActModel(), False


class FakeReActModel:
    """ドライラン用の擬似モデル。会話の進み具合を見て tool_use / 最終回答を返す。"""

    def invoke(self, messages):
        asked = {
            tm.tool_call_id
            for tm in messages
            if isinstance(tm, ToolMessage)
        }
        if "call-a" not in asked:
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_stock", "args": {"product_code": "A-100"}, "id": "call-a"}
                ],
            )
        if "call-b" not in asked:
            return AIMessage(
                content="",
                tool_calls=[
                    {"name": "get_stock", "args": {"product_code": "B-200"}, "id": "call-b"}
                ],
            )
        return AIMessage(
            content="商品A-100は品切れですが、代替品B-200が15個あります。"
        )


# --- ノードとエッジ（本文掲載） --------------------------------------------
def call_model(state: State):
    response = model.invoke(state["messages"])  # bind_toolsで道具を結びつけたモデルに問い合わせる
    return {"messages": [response]}             # 応答を会話に追記


def run_tools(state: State):
    last = state["messages"][-1]                # 直前のモデル応答
    results = execute_tool_calls(last)          # 要求された道具を実行（役割は前章6-3と同じ）
    return {"messages": results}                # 実行結果（tool_result）を会話に追記


def should_continue(state: State):
    last = state["messages"][-1]
    if last.tool_calls:                         # モデルが道具の使用を要求しているか
        return "tools"
    return END


def build_graph():
    builder = StateGraph(State)
    builder.add_node("model", call_model)
    builder.add_node("tools", run_tools)
    builder.add_edge(START, "model")                         # 開始 → モデル
    builder.add_conditional_edges("model", should_continue)  # モデルの後、分岐
    builder.add_edge("tools", "model")                       # ツールの後、モデルへ戻る（＝ループ）
    return builder.compile()


model, real_mode = build_model()


# --- 実行の流れ（トレース解説：本文 7-5 から移設） --------------------------
# ドライランでは、invoke 1回の中でグラフが次の順にノードを巡る。
#
#   START
#   → model ノード（1周目）: モデルが「A-100 の在庫を確認したい」と tool_use を返す
#   → should_continue: tool_calls あり → "tools" へ
#   → tools ノード: get_stock("A-100") を実行 → 0（品切れ）を会話に追記
#   → 固定エッジで model へ戻る（★ここが前章の while ループの戻り）
#   → model ノード（2周目）: 品切れを見て「B-200 も確認したい」と tool_use を返す
#   → should_continue: tool_calls あり → "tools" へ
#   → tools ノード: get_stock("B-200") を実行 → 15（在庫あり）を会話に追記
#   → model へ戻る
#   → model ノード（3周目）: 材料が揃ったので最終回答（tool_calls なし）を返す
#   → should_continue: tool_calls なし → END
#
# 各周で「会話への追記」を messages.append と書いていないことに注意。
# ノードは追記したい分を返すだけで、積む作業はリデューサ add_messages の仕事。
def main():
    if not real_mode:
        print("[メモ] ANTHROPIC_API_KEY / USE_BEDROCK 未設定のため、擬似モデルで流れだけを表示します。\n")
    graph = build_graph()
    question = "商品A-100の在庫を確認して。品切れなら代替品B-200の在庫も調べて、まとめて報告して。"
    result = graph.invoke(
        {"messages": [HumanMessage(content=question)]},
        {"recursion_limit": 25},   # 暴走の歯止め（本文 7-4）：ステップ数の上限
    )
    for m in result["messages"]:
        kind = type(m).__name__
        text = m.content if m.content else f"tool_calls={getattr(m, 'tool_calls', None)}"
        print(f"[{kind}] {text}")


if __name__ == "__main__":
    main()
