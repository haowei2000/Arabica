"""
Centralized Registry System for Aiwen Service.

Registries:
    - ToolRegistry: Manages tool instances and schemas
    - ExecutorRegistry: Manages agent executor templates

Source modules:
    - aiwen.registries.core: ToolRegistry, ExecutorRegistry, register_tool, register_executor
    - aiwen.registries.manager: RegistryManager, get_registry
    - aiwen.core.interfaces.protocols: ToolProtocol, ExecutorProtocol, is_tool, is_executor
    - aiwen.core.interfaces.tool: BaseTool, InnerTool, ExternalTool, ToolMetadata, ...
    - aiwen.core.interfaces.executor: Executor, WaitingForTool
"""
