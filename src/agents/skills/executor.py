"""SkillExecutor — inline 指令注入 / fork 子代理执行。

- inline：返回 skill 正文（render 后的方法论），主 agent 自己执行（不产生子代理）。
- fork：经 build_agent(model, tools=只读面筛选结果, system=执行者人设) 生成独立
  子代理；初始 user message = skill 正文（task 已注入），返回纯文本（不带 [n]）。
  子代理工具面默认继承执行者的只读面（allowed-tools 只作收窄，未声明即不收窄）；
  防递归由禁用集（delegate_task / task_*）硬保证。执行期把 current_request_ctx 切到
  子上下文（run.ctx）：工具检索与引用编号落子池，不污染主 agent 引用池（design D7/D8/D9/R3）。
- 通用委派（record 为 None，design D6）：不加载任何 skill 正文，task 直接作子代理初始
  user message；执行者人设只用会话选定智能体（无 skill.agent 可查）；工具面的
  allowed-tools 收窄项为空（仅继承只读面）。其余 fork 机制（超时/轮次/取消/引用池）完全复用。

可观测性（design D11）：fork 子代理复用主 agent 的 llm 实例（或门面 `get_llm` 新建实例）——
与主 agent **同级观测**（同一实例自带 callbacks；Langfuse 是否捕获取决于网关层，应用层
不新增 Langfuse 工作，见 design Risks「create_agent 观测缺口」）。子代理事件消费
实现见 fork_stream.consume_fork_events（本模块只保留装配与控制流）。

模型/思考（spec delegate-execution-controls）：skill 声明 model 或请求上下文可及 →
门面 `get_llm(model=…, extra_body=thinking_extra_body(spec_main, ctx.deep_thinking))`
新建（门面保留 temperature 与内容日志 callback）；两者均不可得（无 ctx 且未声明 model）→
复用主 agent 实例（思考走模型默认）。思考参数形状**出自 provider 层**
（`thinking_extra_body`，完整 dict 含 preserve），与主链路请求期同源（D8）。

防失控（design D13 + spec delegate-execution-controls，三层）：事件级空闲超时
（DELEGATE_MAX_IDLE_S，流式增量不误杀）+ 总时长保险丝（asyncio.wait_for，deep_thinking
档 DELEGATE_TOTAL_TIMEOUT_THINKING_S / 默认档 DELEGATE_TOTAL_TIMEOUT_S）+ turn 上限
（DELEGATE_DEFAULT_MAX_TURNS）。idle/total/turn 中断统一返回
DELEGATE_TIMEOUT_TEXT 并写 ctx.fork_stop_reason；请求取消（abort_signal 置位）抛
CancelledError 由主任务按取消路径收尾。

截断（design D10）：fork 结果超 DELEGATE_RESULT_LIMIT → 截断为结构化摘要
（含总字数提示），防主 agent 上下文膨胀。
"""

import asyncio
import warnings
from collections.abc import Callable

from langchain_core.runnables.config import var_child_runnable_config

from src.agents.graph.agent_factory import build_agent
from src.agents.presets.models import AgentPreset
from src.agents.presets.registry import AgentPresetRegistry
from src.agents.skills.delegate_run import DelegateRun
from src.agents.skills.fork_stream import consume_fork_events
from src.agents.skills.fork_tools import select_fork_tools
from src.agents.skills.models import SkillContext, SkillRecord
from src.agents.skills.rendering import render_skill_body
from src.agents.tools.readonly import readonly_map
from src.config import settings
from src.config.const import (
    DELEGATE_DEFAULT_MAX_TURNS,
    DELEGATE_RESULT_LIMIT,
    DELEGATE_VIA_DELEGATE,
    DELEGATE_VIA_DIRECT,
    SKILL_TASK_PLACEHOLDERS,
    DelegateStopReason,
    SSEInteractionTexts,
)
from src.config.prompts import (
    FORK_DEFAULT_EXECUTOR_PROMPT,
    FORK_DELEGATE_CITATION_INSTRUCTION,
    FORK_DIRECT_CITATION_INSTRUCTION,
    FORK_EXECUTION_CONTRACT,
    FORK_TASK_APPEND_TMPL,
)
from src.core.eval_evidence import current_evidence
from src.infra.llm.provider.factory import _registry
from src.infra.llm.provider.spec import resolve_role
from src.infra.llm.provider.thinking import thinking_extra_body
from src.infra.llm.request_context import current_request_ctx
from src.infra.llm.tool_trace import ToolTraceCollector
from src.infra.llm.trace_context import current_trace_id
from src.models import get_llm  # 模块级：测试需 patch executor.get_llm


class SkillExecutor:
    """按 skill context 分发执行：inline 注入 / fork 子代理。"""

    def __init__(
        self,
        main_llm,
        tool_provider: Callable[[], list] | None = None,
        preset_registry: AgentPresetRegistry | None = None,
    ) -> None:
        """初始化执行器。

        Args:
            main_llm: 主 agent 的 llm 实例（fork 且 skill 未声明 model 时按
                model_name 继承复用；声明 model 或请求上下文可及时经 get_llm 新建）
            tool_provider: 延迟 provider，返回当前启用工具列表（供 fork 按
                allowed-tools 筛选）。None = 无可用工具（子代理零工具）
            preset_registry: 智能体预设注册表（fork 执行者选择来源）。None =
                不做执行者选择，恒落系统默认人设
        """
        self._main_llm = main_llm
        self._tool_provider = tool_provider
        self._preset_registry = preset_registry
        # 主 agent llm 为 ChatOpenAI 族（get_llm 产物）时 model_name 为标准属性；
        # 测试替身/无该属性的模型对象按 None 处理（缺省 None 走 get_llm 默认模型）
        model_name = None
        if hasattr(main_llm, "model_name"):
            model_name = main_llm.model_name
        if not isinstance(model_name, str) or not model_name:
            model_name = None
        self._main_model_name = model_name

    async def execute(
        self, record: SkillRecord | None, task: str, run: DelegateRun | None = None
    ) -> str:
        """执行一个 skill，返回给主 agent 的文本。

        Args:
            record: 命中的 SkillRecord；None = 通用委派（不加载 skill 正文，直接走
                fork 子代理，task 原样作子代理输入）
            task: 主 agent 委托的任务描述（fork 时同时作子代理初始消息与正文
                $ARGUMENTS/{task} 占位替换值；inline 时填入 inline_prompt 占位）
            run: 本次 fork 委派运行态；None 时用当前主 ctx、不隔离（既有 inline /
                无 ctx 调用路径）；非 None 时切到 run.ctx 子上下文执行

        Returns:
            inline：渲染后的方法论文本；fork：子代理纯文本（截断/防失控超时文案）
        """
        if record is not None and record.context == SkillContext.INLINE:
            return self._render_inline(record, task)
        return await self._run_fork(record, task, run)

    def _render_inline(self, record: SkillRecord, task: str) -> str:
        """渲染 inline_prompt 的任务占位符（$ARGUMENTS 或 {task}；无占位则原样）。

        Args:
            record: inline SkillRecord
            task: 任务文本

        Returns:
            渲染后的方法论文本
        """
        return render_skill_body(record.inline_prompt or "", task)

    def _render_fork_task(self, record: SkillRecord | None, task: str) -> str:
        """渲染 fork 子代理的初始 user message（skill 正文，任务已注入）。

        fork 正文声明了任务占位符（SKILL_TASK_PLACEHOLDERS 任一成员）时直接渲染；
        未声明时在正文末尾追加默认任务段，保证子代理始终拿到任务文本。
        通用委派（record 为 None）无正文可渲染，直接返回 task 原样。

        Args:
            record: fork SkillRecord（读 fork_body）；None = 通用委派
            task: 主 agent 委托的任务文本

        Returns:
            渲染后的正文，作为子代理初始 HumanMessage 内容；通用委派即 task
        """
        if record is None:
            return task
        body = record.fork_body or ""
        declared = False
        for placeholder in SKILL_TASK_PLACEHOLDERS:
            if placeholder in body:
                declared = True
                break
        if not declared:
            body = body + "\n" + FORK_TASK_APPEND_TMPL.format(task=task)
        return render_skill_body(body, task)

    async def _run_fork(
        self, record: SkillRecord | None, task: str, run: DelegateRun | None = None
    ) -> str:
        """fork 执行：子上下文隔离 + astream 级消费 + 三层防失控 + 思考跟随请求档。

        Args:
            record: fork SkillRecord；None = 通用委派（task 原样作子代理输入）
            task: 任务描述（渲染进 skill 正文，作子代理初始 HumanMessage）
            run: 本次委派运行态；None 时用当前主 ctx（不隔离），非 None 时切到
                run.ctx 子上下文执行，工具检索写入子引用池

        Returns:
            子代理聚合纯文本（截断对齐原实现）；因 idle/total/turn 中断统一返回
            DELEGATE_TIMEOUT_TEXT 并把 reason 写入子 ctx 的 fork_stop_reason；
            请求取消（abort_signal 置位）抛 asyncio.CancelledError（reason=cancelled），
            由调用方（delegate_task → 主任务）按取消路径收尾。

        Raises:
            asyncio.CancelledError: abort_signal 置位（请求取消）
        """
        # 会话智能体名必须在切子 ctx 之前读主 ctx（child() 不复制 agent，
        # 切后 current_request_ctx 已指向子 ctx）
        ctx = current_request_ctx.get()
        if ctx is None:
            session_agent = ""
        else:
            session_agent = ctx.agent
        if run is not None:
            # 子 ctx 为本次委派独占：注入本次 id 并复位停止原因，供 consume_fork_events
            # 读取与中断写入（无并发串号）
            run.ctx.delegate_id = run.delegate_id
            run.ctx.fork_stop_reason = None
            child_ctx = run.ctx
        else:
            child_ctx = ctx
        # 本次委派的 skill 标签：run 存在时以其 skill_name 为准（定点填 record.name、
        # 通用填 SSEInteractionTexts.DELEGATE_GENERIC_LABEL）；无 run 的 fail-open 路径由 record 回退
        if run is not None:
            skill_name = run.skill_name
        elif record is not None:
            skill_name = record.name
        else:
            skill_name = SSEInteractionTexts.DELEGATE_GENERIC_LABEL
        user_content = self._render_fork_task(record, task)
        preset = self._resolve_executor(record, session_agent)
        if run is not None:
            via = run.via
        else:
            # run 为空只出现在无请求上下文的 fail-open 分支（委派路径），保持既有引用策略
            via = DELEGATE_VIA_DELEGATE
        sub_agent = self._build_sub_agent(record, preset, via)
        max_turns = self._fork_max_turns(preset)
        total_timeout = self._fork_total_timeout(child_ctx)
        # 委派域 trace（design D11）：父 span 标注 delegate_id / skill，其下挂子代理的工具 span。
        # 每委派一个实例——_round 是单值状态，共用实例会跨流串台。
        trace_collector = ToolTraceCollector(
            enabled=settings.LANGFUSE_ENABLE,
            trace_id=current_trace_id.get() or "",
            scope="delegate",
            name_prefix="delegate:",
        )
        if run is not None:
            trace_collector.open_delegate_span(run.delegate_id, skill_name)
        # 隔离子代理回调传播：不 reset 会经 var_child_runnable_config 把外层
        # callback handler 传进 create_agent，子代理 LLM 事件泄漏到外层
        # graph.astream_events（SSE token 污染 + full_answer 累积子代理原文）。
        # reset 后子代理事件只走其自身 handler，由本方法显式接入（scope=delegate）。
        token = var_child_runnable_config.set(None)
        # 切到子 ctx：工具经 ContextVar 读 ctx，检索与引用编号落子池，
        # 主 agent 引用池不受影响（design D7/R3）
        token_ctx = current_request_ctx.set(child_ctx)
        # 证据隔离：子代理的工具/检索证据不得混入当前题（题目证据只记主链路）；
        # finally 恢复，外层证据采集不受影响
        token_evidence = current_evidence.set(None)
        try:
            try:
                text = await asyncio.wait_for(
                    consume_fork_events(
                        sub_agent,
                        run,
                        user_content,
                        skill_name,
                        max_turns,
                        trace_collector,
                    ),
                    timeout=total_timeout,
                )
            except TimeoutError:
                # 总时长保险丝（total）：wait_for 取消内层后在此收敛。
                # 注意：abort 引发的 CancelledError 不会被 wait_for 吞成 TimeoutError——
                # 非超时路径的任务自身 CancelledError 原样透传（Python 3.11+ wait_for 语义），
                # 因此 cancel 会继续上抛到 delegate_task 的 except asyncio.CancelledError
                # （该 CancelledError 已在 consume_fork_events 置 fork_stop_reason=cancelled）。
                # 端到端验证见 Task J；勿在此加 except CancelledError 防吞（会破坏取消语义）
                if child_ctx is not None:
                    child_ctx.fork_stop_reason = DelegateStopReason.TOTAL
                result = SSEInteractionTexts.DELEGATE_TIMEOUT_TEXT
            else:
                result = self._truncate(text)
            # 回写本次委派最终返回给调用方的文本（含 idle/total/turn 中断文案；
            # 取消路径抛异常不写）。当前无读取方，消费方在 Plan 3（确认门/直出），
            # 勿当死代码清理
            if run is not None:
                run.result_text = result
            return result
        finally:
            # 回写本次委派停止原因；当前无读取方，消费方在 Plan 3（确认门/直出），
            # 勿当死代码清理
            if run is not None:
                run.stop_reason = run.ctx.fork_stop_reason
            # 委派父 span 必须关：取消/异常路径不关会留下悬空 span
            trace_collector.close()
            current_evidence.reset(token_evidence)
            current_request_ctx.reset(token_ctx)
            var_child_runnable_config.reset(token)

    def _resolve_executor(
        self, record: SkillRecord | None, session_agent: str
    ) -> AgentPreset | None:
        """按优先级选执行者预设：skill.agent > 会话智能体 > None（系统默认）。

        Args:
            record: fork SkillRecord（其 agent 字段为最高优先级）；None = 通用委派
                （无 skill.agent 可查，跳过该分支，只用会话智能体）
            session_agent: 会话绑定智能体名（空串=未绑定）

        Returns:
            命中的 AgentPreset；都查不到（或未装配 registry）返回 None
        """
        if self._preset_registry is None:
            return None
        if record is not None and record.agent:
            preset = self._preset_registry.get(record.agent)
            if preset is not None:
                return preset
        if session_agent:
            preset = self._preset_registry.get(session_agent)
            if preset is not None:
                return preset
        return None

    def _fork_max_turns(self, preset: AgentPreset | None) -> int:
        """取 fork 子代理的 turn 上限：preset.max_turns 优先，缺省回落系统默认。"""
        if preset is None:
            return DELEGATE_DEFAULT_MAX_TURNS
        if preset.max_turns is None:
            return DELEGATE_DEFAULT_MAX_TURNS
        return preset.max_turns

    def _build_sub_agent(self, record: SkillRecord | None, preset, via: str):
        """构建 fork 子代理：走与主 agent 共用的装配入口。

        初始 user message（skill 正文 + 任务）由 _run_fork 渲染后经
        consume_fork_events 传入；本方法只负责装配人设、只读面筛选结果与 middleware。

        子角色的 middleware 集合为空——其模型轮次记录由 fork 委派的事件消费侧
        （fork_stream._record_delegate_model_turn）承担，图内不重复产出主循环口径
        的轮次日志与观测。轮次上限仍由消费侧判定（见 delegate-execution-controls
        的「fork turn 上限」），故不传 max_turns。

        Args:
            record: fork SkillRecord；None = 通用委派（工具面不收窄、不声明 model）
            preset: 执行者 AgentPreset（None → 系统默认人设）
            via: 执行路径（DELEGATE_VIA_DIRECT|DELEGATE_VIA_DELEGATE），决定引用编号指示

        Returns:
            build_agent 编译产物（astream_events 事件源）
        """
        return build_agent(
            self._resolve_fork_llm(record),
            self._fork_tools(record, preset),
            system=self._executor_system_prompt(preset, via),
            middleware_extra=[],
        )

    def _executor_system_prompt(self, preset, via: str) -> str:
        """解析 fork 子代理 system prompt（执行者人设 + 执行契约 + 按路径的引用指示）。

        Args:
            preset: 执行者 AgentPreset；None 表示未选执行者预设
            via: 执行路径（DELEGATE_VIA_DIRECT|DELEGATE_VIA_DELEGATE），决定引用编号指示

        Returns:
            preset 非空且 system_prompt 非空时返回其人设，否则返回系统默认
            FORK_DEFAULT_EXECUTOR_PROMPT；两者都追加执行契约 FORK_EXECUTION_CONTRACT
            与按 via 选择的引用指示（统一放在最末，保证预设人设也受同一约束）。
            本层不构造 PromptManager、不拉 Langfuse。
        """
        base = FORK_DEFAULT_EXECUTOR_PROMPT
        if preset is not None and preset.system_prompt:
            base = preset.system_prompt
        if via == DELEGATE_VIA_DIRECT:
            citation = FORK_DIRECT_CITATION_INSTRUCTION
        else:
            citation = FORK_DELEGATE_CITATION_INSTRUCTION
        return base + FORK_EXECUTION_CONTRACT + citation

    def _fork_tools(self, record: SkillRecord | None, preset):
        """按 design D7 口径选子代理工具面（继承只读面 − 禁用集 ∩ 声明收窄）。

        只读表从进程级声明读取（`readonly_map()`）——工具在 `build_graph` 期注册，
        **早于任何请求**，故生产路径上此处恒非空；空表由 `select_fork_tools` 按
        fail-closed 处理。

        Args:
            record: fork SkillRecord（读 allowed_tools 作收窄项）；None = 通用委派
                （无声明收窄项，allowed=[] 不收窄，仅继承只读面）
            preset: 执行者预设（读 tools 作再收窄）；None 表示不再收窄

        Returns:
            子代理工具列表。未装配 tool_provider 且**工具已注册过**时记 warning 并返回空
            （真实装配缺陷，不再静默零工具）；工具从未注册（离线/单测）时静默返回空。
        """
        if record is not None:
            allowed = record.allowed_tools
        else:
            allowed = []
        if self._tool_provider is not None:
            available = self._tool_provider()
        else:
            available = []
            if readonly_map():
                # 表非空说明工具确实注册过，却拿不到 provider → 装配缺陷，必须可见
                warnings.warn(
                    "SkillExecutor 未装配 tool_provider，但工具已注册：fork 子代理工具面为空"
                )
        if preset is not None:
            executor_tools = preset.tools
        else:
            executor_tools = None
        return select_fork_tools(allowed, available, executor_tools, readonly_map())

    @staticmethod
    def _fork_total_timeout(ctx) -> float:
        """按请求思考档选 fork 总时长保险丝。

        Args:
            ctx: 当前请求上下文（可能为 None）

        Returns:
            deep_thinking 档取 DELEGATE_TOTAL_TIMEOUT_THINKING_S，否则默认档
            DELEGATE_TOTAL_TIMEOUT_S（无 ctx 亦走默认档）
        """
        if ctx is not None and ctx.deep_thinking:
            return settings.DELEGATE_TOTAL_TIMEOUT_THINKING_S
        return settings.DELEGATE_TOTAL_TIMEOUT_S

    def _resolve_fork_llm(self, record: SkillRecord | None):
        """解析 fork 子代理的 llm 实例（model / deep_thinking 消费）。

        Args:
            record: fork SkillRecord；None = 通用委派（无 model 声明）

        Returns:
            llm 实例：
            - 声明 model 或请求上下文可及（thinking 非 None）→ 门面 `get_llm`（保留
              temperature 与内容日志 callback）新建，思考经 `extra_body` 注入
            - 两者均不可得（无 ctx 且未声明 model）→ 复用主 agent llm 实例

        思考参数形状**出自 provider 层**（`thinking_extra_body`，完整 dict 含 preserve），
        与主链路请求期同源（D8）；`get_llm` 负责构造期的 temperature 与 callbacks（等值）。
        """
        ctx = current_request_ctx.get()
        if ctx is None:
            thinking = None
        else:
            thinking = ctx.deep_thinking
        if record is not None:
            record_model = record.model
        else:
            record_model = None
        if record_model is None and thinking is None:
            return self._main_llm

        kwargs: dict = {}
        if record_model is not None:
            kwargs["model"] = record_model
        elif self._main_model_name is not None:
            kwargs["model"] = self._main_model_name
        if thinking is not None:
            spec = resolve_role("main", _registry()).provider
            kwargs["extra_body"] = thinking_extra_body(spec, thinking)
        return get_llm(**kwargs)

    def _truncate(self, text: str) -> str:
        """fork 结果截断（超阈值时带总字数提示）。

        Args:
            text: 子代理完整输出

        Returns:
            未超阈值原样返回；超阈值截断为 DELEGATE_TRUNCATED_PREFIX + 前 N 字
        """
        if len(text) <= DELEGATE_RESULT_LIMIT:
            return text
        truncated = text[:DELEGATE_RESULT_LIMIT]
        return SSEInteractionTexts.DELEGATE_TRUNCATED_PREFIX.format(
            total=len(text), truncated=truncated
        )
