# ============================================================
# demo_ask_user.py —— M3-T11 演示"缺参数 → 反问用户"
# ============================================================
# 这个脚本演示的是：
#   用户问了一个缺参数的问题 → 系统判断信息不够 → 反问用户
#
# 运行方式：
#   conda activate NervosBrain
#   cd 项目根目录
#   python scripts/demo_ask_user.py
#
# 你会看到整个流程的每一步都打印出来了。
# ============================================================

import sys
import os

# 把 src 目录加入 Python 搜索路径，这样就能找到 nervos_brain 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from nervos_brain.graph_engine.mini_graph import build_mini_graph
from nervos_brain.graph_engine.sample_data import make_insufficient_state
from nervos_brain.response_normalizer.normalizer import (
    sanitize_markdown,
    validate_response_shape,
)


def main():
    print("=" * 60)
    print("  演示场景：缺参数 → 反问用户")
    print("=" * 60)
    print()

    # 第 1 步：构造一个缺参数的用户消息
    state = make_insufficient_state()
    user_msg = state["user_message"]["content"]
    print(f"👤 用户提问: {user_msg}")
    print(f"   平台: {state['user_message']['context']['platform']}")
    print(f"   用户: {state['user_message']['context']['user_id']}")
    print()

    # 第 2 步：展示系统发现的信息缺口
    print("🔍 系统分析出的信息缺口:")
    for i, need in enumerate(state["info_needs"], 1):
        print(f"   缺口 {i}: [{need['kind']}] {need['question']}")
        print(f"           必须解决: {need['required']}")
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

    # 第 6 步：对回答文本做 Markdown 清洁
    response["text"] = sanitize_markdown(response["text"])

    # 第 7 步：展示最终结果
    print()
    print("🤖 系统反问:")
    print(f"   {response['text']}")
    print(f"   need_user_input: {response.get('need_user_input')}")
    print(f"   引用数: {len(response['citations'])}")
    print()
    print("=" * 60)
    print("  演示结束：系统正确识别了缺参数并反问用户")
    print("=" * 60)


if __name__ == "__main__":
    main()
