"""自分のテキストを3観点で並列レビューさせる対話スクリプト

※ 本リポジトリ限定の追加教材。書籍本文には登場しません。

13-2_agent_pipeline.py のパイプライン（Supervisor → 3観点Workerの並列実行 →
reducerで集約 → 根拠照合（validate_findings）→ 統合レポート）のノードを
import して使い、レビュー対象をダミー契約書ではなく「自分のテキスト」にする。
テキストはファイルパス指定（--file）か、標準入力への貼り付けで渡す。

2モードで動く。

1. 既定（APIキー不要）:
   Workerは 13-5 と同じ擬似Worker。擬似Workerはキーワード規則
   （「賠償」「催告なく」「別途協議」「問題がなければ検収」「個人情報」「納入する」）
   に反応する決め打ちなので、これらの語を含まないテキストでは指摘0件になる。
   それでも、並列fan-out → reducer集約 → 根拠照合 → 統合という流れは
   自分の文書の行番号・章立てで確かめられる。

2. 本番（任意）:
   環境変数 ANTHROPIC_API_KEY が設定され、anthropic パッケージが入っていれば、
   3観点のWorkerを実際の Claude 呼び出しに差し替え、自由なテキストへの指摘を得る。
   モデルが申告した参照（章・行・抜粋）は 13-6 の根拠照合をそのまま通すので、
   原文と一致しない指摘が「根拠不一致・未確認」に分けられる様子も観察できる。
   （anthropic の導入: pip install anthropic）
   ANTHROPIC_API_KEY の代わりに USE_BEDROCK=1 を設定すると、AWS の認証情報を使って
   Amazon Bedrock 経由で Claude を呼ぶ（導入: pip install "anthropic[bedrock]"）。

実行:
    python interactive_doc_review.py --file 契約書.txt   # ファイルを1回レビュー
    python interactive_doc_review.py                     # 貼り付けモード（空行で確定）

貼り付けモードは空行を入力すると文書の確定になるため、空行を含む文書は
--file で渡すこと。対話は何も貼り付けずに空行（または Ctrl+C）で終了する。

注意:
    モデル名（MODEL）は新しい世代が出るたびに更新される。本番モードで
    not_found_error 等が出たら、Anthropic 公式ドキュメントで現行の
    モデル名を確認して置き換えること。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import sys

from langgraph.graph import END, START, StateGraph

from _common import pseudo_worker_analyze

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

# ファイル名にハイフンを含むため、13-2 のモジュールはパス指定で読み込む
_here = pathlib.Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "agent_pipeline", _here / "13-2_agent_pipeline.py")
_pipeline = importlib.util.module_from_spec(_spec)
# sys.modules に登録してから実行する。未登録だと、State（from __future__ import
# annotations 下の TypedDict）の型ヒントを LangGraph が評価するときに
# モジュールのグローバルを参照できず NameError: 'Annotated' になる
sys.modules[_spec.name] = _pipeline
_spec.loader.exec_module(_pipeline)

DOCUMENT_ID = "user-doc"
DOCUMENT_VERSION = "v1"

VIEWPOINTS = {"法務": "legal_worker", "業務要件": "requirements_worker",
              "技術妥当性": "tech_worker"}

# 「第N条」「第N条(見出し)」「第N条（見出し）」を章の切れ目として拾う
_CHAPTER_RE = re.compile(r"^第[0-9０-９]+条(?:（[^）]*）|\([^)]*\))?")


def to_document(text: str) -> list[dict]:
    """自由テキストを、13章の文書形式（行番号・章つきの行の一覧）に変換する。

    _common.numbered_document() と同じキー構成にすることで、擬似Worker・
    根拠照合（validate_findings）・統合をそのまま流用できる。
    """
    rows: list[dict] = []
    chapter = "本文"
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        m = _CHAPTER_RE.match(line)
        if m:
            chapter = m.group(0)
            line = line[m.end():].strip() or line  # 見出しだけの行は行全体を本文に残す
        rows.append({
            "document_id": DOCUMENT_ID,
            "document_version": DOCUMENT_VERSION,
            "line": line_number,
            "chapter": chapter,
            "text": line,
        })
    return rows


# --- Worker実装（差し替え可能）------------------------------------------------
def pseudo_worker(viewpoint: str, document: list[dict]) -> list[dict]:
    """既定：13-5 と同じ擬似Worker（キーワード規則）で自分の文書を走査する。"""
    return pseudo_worker_analyze(viewpoint, document)


def _make_live_worker(client):
    """本番：1観点ぶんの分析を実際の Claude に任せるWorkerを作る。

    返答はJSONで受け取り、参照の検証（文書ID・版・章・行・抜粋の照合）は
    モデルを信用せず 13-6 の validate_findings に任せる。
    """
    def live_worker(viewpoint: str, document: list[dict]) -> list[dict]:
        # 章名と本文を分けて見せる。続けて書くと、モデルが章名ごと excerpt に書き写し、
        # 根拠照合（本文との一致）に全件落ちる
        doc_text = "\n".join(
            f"行{r['line']}（章: {r['chapter']}）本文: {r['text']}" for r in document)
        prompt = (
            f"あなたは文書レビューの{viewpoint}の専門家です。次の行番号付き文書を"
            f"{viewpoint}の観点でレビューし、問題点を挙げてください。\n\n"
            f"# 文書\n{doc_text}\n\n"
            "JSON配列だけを出力してください。各要素は\n"
            '{"chapter": "該当行の章", "line": 行番号(整数), '
            '"excerpt": "該当行の本文をそのまま書き写す", '
            '"issue": "指摘", "severity": "高/中/低"}\n'
            "chapter は該当行の「章:」の値、excerpt は「本文:」より後ろと"
            "一字一句同じにしてください（章名は含めない）。"
            "指摘が無ければ [] を返してください。"
        )
        resp = client.messages.create(
            model=_model_name(), max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        if getattr(resp, "stop_reason", None) != "end_turn":
            raise ValueError("応答が正常に完了していない")
        # モデルは JSON を ```json ... ``` で囲んで返すことがあるので、囲みだけ外す
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        # 配列全体を検証する。壊れた応答・途中で切れた応答を指摘0件と混同しない。
        items = json.loads(text)
        if not isinstance(items, list):
            raise ValueError("JSON配列が必要")
        findings = []
        for it in items:
            if (not isinstance(it, dict)
                    or any(not isinstance(it.get(k), str) or not it[k].strip()
                           for k in ("chapter", "excerpt", "issue"))
                    or type(it.get("line")) is not int or it["line"] < 1
                    or it.get("severity") not in ("高", "中", "低")):
                raise ValueError("指摘の形式が不正")
            chapter = it.get("chapter", "不明")
            excerpt = it.get("excerpt", "")
            findings.append({
                "document_id": DOCUMENT_ID,
                "document_version": DOCUMENT_VERSION,
                "viewpoint": viewpoint,
                "chapter": chapter,
                "line": it.get("line"),
                "excerpt": excerpt,
                "issue": it.get("issue", ""),
                "severity": it.get("severity", "中"),
                "recommendation": "担当者が原文と社内基準を確認する。",
                "status": "要確認",
            })
        return findings

    return live_worker


def build_graph(worker_fn):
    """13-2 と同じグラフを、Workerの実装だけ差し替え可能にして組む。"""
    def make_node(viewpoint: str):
        def node(state):
            try:
                return {"findings": worker_fn(viewpoint, state["document"])}
            except Exception as exc:
                # 応答本文やAPIエラー詳細は載せず、失敗した観点と再実行要否を残す。
                return {"findings": [], "worker_errors": [{
                    "viewpoint": viewpoint, "reason": type(exc).__name__,
                    "retry": "未実施。応答形式・接続設定を確認して再実行する",
                }]}
        return node

    b = StateGraph(_pipeline.State)
    b.add_node("supervisor", _pipeline.supervisor_dispatch)
    for viewpoint, node_name in VIEWPOINTS.items():
        b.add_node(node_name, make_node(viewpoint))
    b.add_node("validate", _pipeline.validate)
    b.add_node("integrate", _pipeline.integrate)

    b.add_edge(START, "supervisor")
    for node_name in VIEWPOINTS.values():
        b.add_edge("supervisor", node_name)   # fan-out：3観点を並列実行
        b.add_edge(node_name, "validate")     # fan-in：全Worker完了後に1回だけ
    b.add_edge("validate", "integrate")
    b.add_edge("integrate", END)
    return b.compile()


def _setup_client():
    if not (os.environ.get("ANTHROPIC_API_KEY") or _use_bedrock()):
        print("[メモ] ANTHROPIC_API_KEY / USE_BEDROCK 未設定のため、擬似Worker（キーワード規則）で動きます。")
        return None
    try:
        import anthropic
        if _use_bedrock():
            import boto3  # noqa: F401  # anthropic[bedrock] で入る。存在確認のみ
    except ImportError:
        print("[メモ] anthropic パッケージ未導入のため、擬似Workerで動きます"
              "（本番モードは pip install anthropic、Bedrock は pip install 'anthropic[bedrock]'）。")
        return None
    if _use_bedrock():
        # 認証は AWS の認証情報（AWS_PROFILE 等）から。APIキーは使わない
        return anthropic.AnthropicBedrock(
            aws_region=os.environ.get("AWS_REGION", BEDROCK_REGION))
    return anthropic.Anthropic()


def review(graph, text: str) -> None:
    document = to_document(text)
    if not document:
        print("テキストが空のため、レビューをスキップします。")
        return
    chapters = list(dict.fromkeys(r["chapter"] for r in document))
    print(f"\n文書 {len(document)}行（章立て: {chapters}）を3観点で並列レビューします。")
    out = graph.invoke({"document": document, "findings": [], "worker_errors": []},
                       {"recursion_limit": 50})
    print(f"  集まった指摘: {len(out['findings'])}件 / "
          f"根拠を確認できた指摘: {len(out['valid_findings'])}件 / "
          f"根拠不一致: {len(out['rejected_findings'])}件")
    print()
    print(out["report"])


def read_pasted() -> str | None:
    """標準入力から貼り付けを受ける。空行で確定。最初から空行なら None（終了）。"""
    print("\nレビューする文書を貼り付けてください（空行で確定。何も貼らずに空行で終了）")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines) if lines else None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="自分のテキストを3観点（法務・業務要件・技術妥当性）で並列レビューする")
    parser.add_argument("--file", help="レビューするテキストファイルのパス（UTF-8）")
    args = parser.parse_args()

    client = _setup_client()
    worker_fn = _make_live_worker(client) if client else pseudo_worker
    graph = build_graph(worker_fn)

    if args.file:
        path = pathlib.Path(args.file)
        if not path.exists():
            print(f"ファイルが見つかりません: {path}")
            sys.exit(1)
        review(graph, path.read_text(encoding="utf-8"))
        return

    while True:
        text = read_pasted()
        if text is None:
            break
        review(graph, text)


if __name__ == "__main__":
    main()
