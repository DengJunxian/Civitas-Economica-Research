"""Optional UI; simulation execution belongs to the headless service."""
import json
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

from core.experiments.config import ExperimentConfig
from core.experiments.runner import ExperimentRunner

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Civitas Lab · 经济金融实验教学", layout="wide")
st.title("Civitas Lab · 经济金融实验教学")
LAB_PAGES = ["课程中心", "场景实验室", "计算实验室", "对照分析", "AI 教学席"]
page = st.sidebar.radio("工作区", LAB_PAGES + ["配置与运行", "结果与对照", "研究扩展"], key="lab_page")
st.sidebar.caption("仿真教学用途：结果是模型条件下的输出，不是市场预测或投资建议。")
output_root = ROOT / "outputs/experiments"

if page in LAB_PAGES:
    from ui.lab import render_ai_desk, render_calculator_lab, render_comparison, render_course_center, render_scenario_lab
    lab_root = ROOT / "outputs/lab"
    {"课程中心": render_course_center,
     "场景实验室": lambda: render_scenario_lab(lab_root),
     "计算实验室": render_calculator_lab,
     "对照分析": lambda: render_comparison(lab_root),
     "AI 教学席": lambda: render_ai_desk(lab_root)}[page]()
elif page == "配置与运行":
    content = st.text_area("实验配置（YAML）", (ROOT / "examples/experiments/minimal.yaml").read_text(), height=400)
    if st.button("校验配置"):
        try:
            st.json(ExperimentConfig.model_validate(yaml.safe_load(content)).model_dump(mode="json"))
        except Exception as exc:
            st.error(str(exc))
    if st.button("运行实验", type="primary"):
        try:
            config = ExperimentConfig.model_validate(yaml.safe_load(content))
            with st.spinner("执行仿真并保存逐步结果"):
                result = ExperimentRunner(config).run(output_root)
            st.session_state["last_run"] = str(result)
            st.success(f"运行完成：{result}")
            st.json(json.loads((result / "summary.json").read_text()))
        except Exception as exc:
            st.error(f"实验失败：{exc}。运行目录保留失败状态和日志。")
elif page == "结果与对照":
    runs = sorted(output_root.glob("*/summary.json"), reverse=True)
    if not runs:
        st.info("尚无实验结果。先配置并运行一个实验。")
    else:
        selected = st.multiselect("选择实验", runs, default=runs[:1], format_func=lambda p: p.parent.name)
        series = {}
        for file in selected:
            st.json(json.loads(file.read_text()))
            metrics = file.parent / "metrics.csv"
            if metrics.exists():
                frame = pd.read_csv(metrics)
                series[file.parent.name] = frame.set_index("step")["price"]
                with st.expander(f"指标与日志：{file.parent.name}"):
                    st.dataframe(frame)
                    log = file.parent / "run.log"
                    if log.exists():
                        st.code(log.read_text()[-12000:])
                    st.download_button("下载配置", (file.parent / "config.json").read_bytes(), "config.json", key=str(file))
        if series:
            st.line_chart(pd.DataFrame(series))
else:
    st.write("原有政策、历史回放、监管优化及行为诊断模块保留为研究扩展，具有各自的模型假设、依赖和输出格式。模块状态见 docs/ARCHITECTURE.md。")
    extensions = {
        "政策与宏观耦合": ("ui.policy_lab", "render_policy_lab"),
        "历史回放": ("ui.history_replay", "render_history_replay"),
        "因子回测": ("ui.backtest_panel", "render_backtest_panel"),
        "行为诊断": ("ui.behavioral_diagnostics", "render_behavioral_diagnostics"),
        "监管优化": ("ui.regulator_optimization", "render_regulator_optimization"),
    }
    selected = st.selectbox("研究模块", extensions)
    if st.checkbox("加载所选研究模块"):
        from importlib import import_module
        module, function = extensions[selected]
        try:
            getattr(import_module(module), function)()
        except Exception as exc:
            st.error(f"研究模块运行失败：{type(exc).__name__}: {exc}")
