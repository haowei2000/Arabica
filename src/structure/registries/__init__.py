"""
Centralized Registry System for Structure Service.

Registries:
    - ToolRegistry: Manages tool instances and schemas
    - ExecutorRegistry: Manages agent executor templates

Source modules:
    - structure.registries.core: ToolRegistry, ExecutorRegistry, register_tool, register_executor
    - structure.registries.manager: RegistryManager, get_registry
    - structure.core.interfaces.protocols: ToolProtocol, ExecutorProtocol, is_tool, is_executor
    - structure.core.interfaces.tool: BaseTool, InnerTool, ToolMetadata, ...
    - structure.core.interfaces.executor: Executor, WaitingForTool
"""
