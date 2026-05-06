# ============================================================
# demo_answer.py —— M3-T12 演示"参数足够 → 返回回答"
# ============================================================
# 这个脚本演示的是：
#   用户问了一个信息充足的问题 → 系统组装回答 → 经过整理 → 输出
#
# 运行方式：
#   conda activate NervosBrain
#   cd 项目根目录
#   python scripts/demo_answer.py
#
# 你会看到完整的"检索 → 回答 → 引用修复 → Markdown 清洁 → 切段"流程。
# ============================================================

import sys
import os

# 把 src 目录加入 Python 搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nervos_brain.graph_engine.mini_graph import build_mini_graph
from nervos_brain.graph_engine.sample_data import make_sufficient_state
from nervos_brain.response_normalizer.normalizer import (
    chunk_for_platform,
    normalize_citations,
    sanitize_markdown,
    validate_response_shape,
)


def main():
    print("=" * 60)
    print("  演示场景：参数足够 → 组装回答 → 整理输出")
    print("=" * 60)
    print()

    # 第 1 步：构造一个信息充足的用户消息
    state = make_sufficient_state()
    user_msg = state["user_message"]["content"]
    print(f"👤 用户提问: {user_msg}")
    print(f"   平台: {state['user_message']['context']['platform']}")
    print(f"   用户: {state['user_message']['context']['user_id']}")
    print()

    # 第 2 步：展示已有的证据
    print("📚 已有证据:")
    for i, ev in enumerate(state["evidence"], 1):
        print(f"   证据 {i}: [{ev['source']}] {ev['title']}")
        print(f"           分数: {ev['score']}")
        print(f"           摘要: {ev['snippet'][:60]}...")
    print()

    # 第 3 步：运行 LangGraph 流程
    print("⚙️  运行 LangGraph 工作流...")
    print("-" * 40)
    graph = build_mini_graph()
    result = graph.invoke(state)
    print("-" * 40)
    print()

    # 第 4 步：取出最终回答
    response = result["_final_response"]

    # 第 5 步：校验回答格式
    ok, errors = validate_response_shape(response)
    print(f"✅ 回答格式校验: {'通过' if ok else '失败 ' + str(errors)}")

    # 第 6 步：引用编号修复
    original_text = response["text"]
    response["text"], response["citations"] = normalize_citations(
        response["text"], response["citations"]
    )
    print(f"✅ 引用编号修复: {len(response['citations'])} 条引用")

    # 第 7 步：Markdown 清洁
    response["text"] = sanitize_markdown(response["text"])
    print("✅ Markdown 清洁完成")

    # 第 8 步：按平台限制切段（模拟 Discord 2000 字符限制）
    segments = chunk_for_platform(response["text"], max_chars=2000)
    print(f"✅ 平台切段: 切成 {len(segments)} 段")
    print()

    # 第 9 步：展示最终结果
    print("🤖 系统回答:")
    print("-" * 40)
    for i, seg in enumerate(segments, 1):
        if len(segments) > 1:
            print(f"  --- 第 {i} 段 ---")
        print(f"  {seg}")
    print("-" * 40)
    print()

    print("📎 引用列表:")
    for cit in response["citations"]:
        print(f"   {cit['label']} {cit['title']}")
        print(f"      URL: {cit['url']}")
        print(f"      锚点: {cit['anchor']}")
    print()

    print("=" * 60)
    print("  演示结束：完整流程 检索→回答→修复→清洁→切段 全部跑通")
    print("=" * 60)


if __name__ == "__main__":
    main()
