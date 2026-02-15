import { useState, useEffect } from 'react';
import {
  useTemplates,
  useToolList,
  useCreateTool,
  useDeleteTool,
  useToggleTool,
} from '@/hooks/useTools';
import type { ToolTemplate, UserToolCreate } from '@/types/tool';

const INITIAL_FORM: UserToolCreate & { _headersJson?: string; _inputSchemaJson?: string } = {
  name: '',
  display_name: '',
  description: '',
  execution_mode: 'http',
  input_schema: {},
  http_config: null,
  code: null,
  category: 'custom',
  tags: [],
  timeout: 30,
  enabled: true,
  is_public: false,
  _headersJson: '{}',
  _inputSchemaJson: '{}',
};

export default function ToolPage() {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [selectedTemplateId, setSelectedTemplateId] = useState('http_get_api');
  const [formData, setFormData] = useState(INITIAL_FORM);
  const [httpMethod, setHttpMethod] = useState('GET');
  const [httpUrl, setHttpUrl] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);

  const { data: templateData, isLoading: templatesLoading } = useTemplates();
  const { data: toolData, isLoading: toolsLoading } = useToolList({
    enabled_only: false,
    tags: selectedTags.length > 0 ? selectedTags.join(',') : undefined
  });
  const createMutation = useCreateTool();
  const deleteMutation = useDeleteTool();
  const toggleMutation = useToggleTool();

  // Pre-fill form when template changes
  useEffect(() => {
    if (!templateData?.templates) return;
    const template = templateData.templates.find(
      (t: ToolTemplate) => t.id === selectedTemplateId
    );
    if (!template) return;

    const tpl = template.template as Record<string, unknown>;
    const httpConfig = tpl.http_config as Record<string, unknown> | undefined;
    const headers = httpConfig?.headers ?? {};
    const inputSchema = (tpl.input_schema as Record<string, unknown>) ?? {};

    setFormData({
      ...INITIAL_FORM,
      name: (tpl.name as string) ?? '',
      display_name: (tpl.display_name as string) ?? '',
      description: (tpl.description as string) ?? '',
      execution_mode: template.execution_mode,
      input_schema: inputSchema,
      category: (tpl.category as string) ?? 'custom',
      tags: (tpl.tags as string[]) ?? [],
      timeout: (tpl.timeout as number) ?? 30,
      code: (tpl.code as string) ?? null,
      http_config: httpConfig ? (tpl.http_config as Record<string, unknown>) : null,
      inner_tool_name: template.inner_tool_name ?? undefined,
      parameter_mapping: (tpl.parameter_mapping as Record<string, string>) ?? undefined,
      _headersJson: JSON.stringify(headers, null, 2),
      _inputSchemaJson: JSON.stringify(inputSchema, null, 2),
    });

    setHttpMethod((httpConfig?.method as string) ?? 'GET');
    setHttpUrl((httpConfig?.url as string) ?? '');
  }, [selectedTemplateId, templateData]);

  const openCreateModal = () => {
    setSelectedTemplateId('http_get_api');
    setShowCreateModal(true);
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();

    let inputSchema: Record<string, unknown>;
    try {
      inputSchema = JSON.parse(formData._inputSchemaJson ?? '{}');
    } catch {
      alert('Input Schema is not valid JSON');
      return;
    }

    const payload: UserToolCreate = {
      name: formData.name,
      display_name: formData.display_name,
      description: formData.description,
      execution_mode: formData.execution_mode,
      input_schema: inputSchema,
      category: formData.category,
      tags: formData.tags,
      timeout: formData.timeout,
      enabled: formData.enabled,
      is_public: formData.is_public,
    };

    // Add inner_tool_name + parameter_mapping if set (for inner_tool templates)
    if (formData.inner_tool_name) {
      payload.inner_tool_name = formData.inner_tool_name;
      payload.parameter_mapping = formData.parameter_mapping;
    }

    if (formData.execution_mode === 'http' && !formData.inner_tool_name) {
      let headers: Record<string, unknown>;
      try {
        headers = JSON.parse(formData._headersJson ?? '{}');
      } catch {
        alert('Headers is not valid JSON');
        return;
      }
      payload.http_config = {
        method: httpMethod,
        url: httpUrl,
        headers,
        timeout: formData.timeout,
      };
    }

    if (formData.execution_mode === 'server_run' && !formData.inner_tool_name) {
      payload.code = formData.code;
    }

    try {
      await createMutation.mutateAsync(payload);
      setShowCreateModal(false);
    } catch (error) {
      alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleDelete = async (id: string, name: string) => {
    if (!confirm(`Are you sure you want to delete "${name}"?`)) return;
    try {
      await deleteMutation.mutateAsync(id);
    } catch (error) {
      alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleToggle = async (id: string, currentEnabled: boolean) => {
    try {
      await toggleMutation.mutateAsync({ id, enabled: !currentEnabled });
    } catch (error) {
      alert(`Toggle failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  if (toolsLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="text-secondary-500 dark:text-secondary-400">Loading...</div>
      </div>
    );
  }

  const tools = toolData?.tools ?? [];
  const isInnerTool = (toolType: string) => toolType === 'inner';

  // Extract all unique tags from tools
  const allTags = Array.from(
    new Set(
      tools.flatMap((tool) => tool.tags ?? [])
    )
  ).sort();

  const toggleTag = (tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]
    );
  };

  const clearTags = () => setSelectedTags([]);

  return (
    <div>
      {/* Header */}
      <div className="mb-6 flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold text-navy-900 dark:text-navy-100">Tools</h2>
          <p className="text-sm text-secondary-500 dark:text-secondary-400 mt-1">
            Manage built-in and custom tools for your AI agents
          </p>
        </div>
        <button
          onClick={openCreateModal}
          className="px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600 focus:outline-none focus:ring-2 focus:ring-primary-500"
        >
          + Create Tool
        </button>
      </div>

      {/* Tag Filter */}
      {allTags.length > 0 && (
        <div className="mb-6 bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700 p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-navy-900 dark:text-navy-100">
              Filter by Tags
            </h3>
            {selectedTags.length > 0 && (
              <button
                onClick={clearTags}
                className="text-xs text-primary-500 hover:text-primary-600 font-medium"
              >
                Clear All
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {allTags.map((tag) => {
              const isSelected = selectedTags.includes(tag);
              return (
                <button
                  key={tag}
                  onClick={() => toggleTag(tag)}
                  className={`px-3 py-1.5 text-sm rounded-full font-medium transition-colors ${
                    isSelected
                      ? 'bg-primary-500 text-white hover:bg-primary-600'
                      : 'bg-secondary-100 dark:bg-navy-700 text-secondary-700 dark:text-secondary-300 hover:bg-secondary-200 dark:hover:bg-navy-600'
                  }`}
                >
                  {tag}
                  {isSelected && ' ✓'}
                </button>
              );
            })}
          </div>
          {selectedTags.length > 0 && (
            <div className="mt-3 text-xs text-secondary-500 dark:text-secondary-400">
              Showing tools with: {selectedTags.join(', ')}
            </div>
          )}
        </div>
      )}

      {/* Create Modal */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 dark:bg-opacity-70 flex items-center justify-center z-50">
          <div className="bg-white dark:bg-navy-800 rounded-lg p-6 max-w-2xl w-full mx-4 border border-secondary-200 dark:border-navy-700 max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-semibold mb-4 text-navy-900 dark:text-navy-100">
              Create Tool
            </h3>

            <form onSubmit={handleCreate} className="space-y-4">
              {/* Template Selector */}
              <div>
                <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                  Template *
                </label>
                <select
                  value={selectedTemplateId}
                  onChange={(e) => setSelectedTemplateId(e.target.value)}
                  className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                    bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                    focus:outline-none focus:ring-2 focus:ring-primary-500"
                >
                  {templatesLoading ? (
                    <option>Loading templates...</option>
                  ) : (
                    templateData?.templates.map((t: ToolTemplate) => (
                      <option key={t.id} value={t.id}>
                        {t.name} — {t.description}
                      </option>
                    ))
                  )}
                </select>
              </div>

              {/* Name */}
              <div>
                <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                  Name *
                </label>
                <input
                  type="text"
                  value={formData.name}
                  onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                  placeholder="e.g., weather_api"
                  className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                    bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                    placeholder-secondary-400 dark:placeholder-secondary-500
                    focus:outline-none focus:ring-2 focus:ring-primary-500"
                  required
                />
              </div>

              {/* Display Name */}
              <div>
                <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                  Display Name *
                </label>
                <input
                  type="text"
                  value={formData.display_name}
                  onChange={(e) => setFormData({ ...formData, display_name: e.target.value })}
                  placeholder="e.g., Weather API"
                  className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                    bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                    placeholder-secondary-400 dark:placeholder-secondary-500
                    focus:outline-none focus:ring-2 focus:ring-primary-500"
                  required
                />
              </div>

              {/* Description */}
              <div>
                <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                  Description *
                </label>
                <textarea
                  value={formData.description}
                  onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                  placeholder="Describe what this tool does"
                  rows={2}
                  className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                    bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                    placeholder-secondary-400 dark:placeholder-secondary-500
                    focus:outline-none focus:ring-2 focus:ring-primary-500"
                  required
                />
              </div>

              {/* Execution Mode (read-only) + Category + Timeout */}
              <div className="grid grid-cols-3 gap-4">
                <div>
                  <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                    Execution Mode
                  </label>
                  <input
                    type="text"
                    value={formData.execution_mode}
                    readOnly
                    className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                      bg-secondary-50 dark:bg-navy-900 text-secondary-500 dark:text-secondary-400 cursor-not-allowed"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                    Category
                  </label>
                  <input
                    type="text"
                    value={formData.category ?? ''}
                    onChange={(e) => setFormData({ ...formData, category: e.target.value })}
                    className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                      bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                      focus:outline-none focus:ring-2 focus:ring-primary-500"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                    Timeout (s)
                  </label>
                  <input
                    type="number"
                    value={formData.timeout}
                    onChange={(e) => setFormData({ ...formData, timeout: parseInt(e.target.value) || 30 })}
                    min={1}
                    max={3600}
                    className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                      bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                      focus:outline-none focus:ring-2 focus:ring-primary-500"
                  />
                </div>
              </div>

              {/* HTTP Config Section */}
              {formData.execution_mode === 'http' && !formData.inner_tool_name && (
                <fieldset className="border border-secondary-200 dark:border-navy-600 rounded-md p-4 space-y-3">
                  <legend className="text-sm font-medium text-secondary-600 dark:text-secondary-300 px-2">
                    HTTP Configuration
                  </legend>
                  <div className="grid grid-cols-4 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-secondary-500 dark:text-secondary-400 mb-1">
                        Method
                      </label>
                      <select
                        value={httpMethod}
                        onChange={(e) => setHttpMethod(e.target.value)}
                        className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                          bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                          focus:outline-none focus:ring-2 focus:ring-primary-500"
                      >
                        {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    </div>
                    <div className="col-span-3">
                      <label className="block text-xs font-medium text-secondary-500 dark:text-secondary-400 mb-1">
                        URL *
                      </label>
                      <input
                        type="text"
                        value={httpUrl}
                        onChange={(e) => setHttpUrl(e.target.value)}
                        placeholder="https://api.example.com/endpoint"
                        className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                          bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100
                          placeholder-secondary-400 dark:placeholder-secondary-500
                          focus:outline-none focus:ring-2 focus:ring-primary-500"
                        required
                      />
                    </div>
                  </div>
                  <div>
                    <label className="block text-xs font-medium text-secondary-500 dark:text-secondary-400 mb-1">
                      Headers (JSON)
                    </label>
                    <textarea
                      value={formData._headersJson}
                      onChange={(e) => setFormData({ ...formData, _headersJson: e.target.value })}
                      rows={3}
                      className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                        bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100 font-mono text-sm
                        focus:outline-none focus:ring-2 focus:ring-primary-500"
                    />
                  </div>
                </fieldset>
              )}

              {/* Code Section */}
              {formData.execution_mode === 'server_run' && !formData.inner_tool_name && (
                <div>
                  <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                    Python Code *
                  </label>
                  <textarea
                    value={formData.code ?? ''}
                    onChange={(e) => setFormData({ ...formData, code: e.target.value })}
                    rows={8}
                    placeholder="# input_data contains your parameters&#10;result = {'output': input_data['text']}"
                    className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                      bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100 font-mono text-sm
                      placeholder-secondary-400 dark:placeholder-secondary-500
                      focus:outline-none focus:ring-2 focus:ring-primary-500"
                    required
                  />
                </div>
              )}

              {/* Input Schema */}
              <div>
                <label className="block text-sm font-medium text-secondary-600 dark:text-secondary-300 mb-1">
                  Input Schema (JSON)
                </label>
                <textarea
                  value={formData._inputSchemaJson}
                  onChange={(e) => setFormData({ ...formData, _inputSchemaJson: e.target.value })}
                  rows={6}
                  className="w-full px-3 py-2 border border-secondary-200 dark:border-navy-600 rounded-md
                    bg-white dark:bg-navy-700 text-navy-900 dark:text-navy-100 font-mono text-sm
                    focus:outline-none focus:ring-2 focus:ring-primary-500"
                />
              </div>

              {/* Actions */}
              <div className="flex gap-3 pt-4">
                <button
                  type="button"
                  onClick={() => setShowCreateModal(false)}
                  className="flex-1 px-4 py-2 border border-secondary-200 dark:border-navy-600
                    text-secondary-600 dark:text-secondary-300 rounded-md
                    hover:bg-navy-50 dark:hover:bg-navy-700"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={createMutation.isPending}
                  className="flex-1 px-4 py-2 bg-primary-500 text-white rounded-md
                    hover:bg-primary-600 disabled:opacity-50"
                >
                  {createMutation.isPending ? 'Creating...' : 'Create Tool'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Tool Grid */}
      {tools.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {tools.map((tool) => {
            const inner = isInnerTool(tool.tool_type);
            return (
              <div
                key={tool.id}
                className="bg-white dark:bg-navy-800 rounded-lg border border-secondary-200 dark:border-navy-700
                  p-6 hover:shadow-lg dark:hover:shadow-navy-900/50 transition-shadow"
              >
                <div className="flex items-start justify-between mb-3">
                  <div className="flex-1 min-w-0">
                    <h3 className="text-lg font-semibold text-navy-900 dark:text-navy-100 truncate">
                      {tool.display_name || tool.name}
                    </h3>
                    <p className="text-xs text-secondary-400 dark:text-secondary-500 font-mono">
                      {tool.name}
                    </p>
                  </div>
                  <div className="flex gap-1.5 ml-2 flex-shrink-0">
                    {/* Tool type badge */}
                    {inner ? (
                      <span className="inline-block px-2 py-0.5 text-xs rounded font-medium bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400">
                        Built-in
                      </span>
                    ) : (
                      <span
                        className={`inline-block px-2 py-0.5 text-xs rounded font-medium ${
                          tool.execution_mode === 'http'
                            ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400'
                            : 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-400'
                        }`}
                      >
                        {tool.execution_mode}
                      </span>
                    )}
                    <span
                      className={`inline-block px-2 py-0.5 text-xs rounded font-medium ${
                        tool.enabled
                          ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                          : 'bg-secondary-100 dark:bg-navy-700 text-secondary-500 dark:text-secondary-400'
                      }`}
                    >
                      {tool.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                  </div>
                </div>

                <p className="text-sm text-secondary-600 dark:text-secondary-400 mb-3 line-clamp-2">
                  {tool.description}
                </p>

                {/* Tags */}
                {tool.tags && tool.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    {tool.tags.map((tag) => (
                      <span
                        key={tag}
                        className="px-2 py-0.5 text-xs rounded-full bg-secondary-100 dark:bg-navy-700 text-secondary-600 dark:text-secondary-300"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                )}

                <div className="text-xs text-secondary-500 dark:text-secondary-400 mb-4 space-y-1">
                  {tool.category && <p>Category: {tool.category}</p>}
                  {!inner && <p>Usage: {tool.usage_count} calls</p>}
                  <p>Created: {new Date(tool.created_at).toLocaleDateString()}</p>
                </div>

                {/* Actions: only for external tools */}
                {!inner && (
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleToggle(tool.id, tool.enabled)}
                      disabled={toggleMutation.isPending}
                      className={`flex-1 px-3 py-1.5 text-sm rounded-md disabled:opacity-50 ${
                        tool.enabled
                          ? 'border border-secondary-200 dark:border-navy-600 text-secondary-600 dark:text-secondary-300 hover:bg-navy-50 dark:hover:bg-navy-700'
                          : 'bg-green-500 text-white hover:bg-green-600'
                      }`}
                    >
                      {tool.enabled ? 'Disable' : 'Enable'}
                    </button>
                    <button
                      onClick={() => handleDelete(tool.id, tool.display_name || tool.name)}
                      disabled={deleteMutation.isPending}
                      className="px-3 py-1.5 border border-red-300 dark:border-red-700
                        text-red-600 dark:text-red-400 text-sm rounded-md
                        hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        /* Empty State */
        <div className="text-center py-12">
          <div className="text-secondary-400 dark:text-secondary-600 mb-4">
            <svg
              className="mx-auto h-12 w-12"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"
              />
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"
              />
            </svg>
          </div>
          <h3 className="text-lg font-medium text-navy-900 dark:text-navy-100 mb-1">
            No Tools Yet
          </h3>
          <p className="text-secondary-500 dark:text-secondary-400 mb-4">
            Create your first custom tool to extend your AI agent's capabilities
          </p>
          <button
            onClick={openCreateModal}
            className="px-4 py-2 bg-primary-500 text-white rounded-lg hover:bg-primary-600"
          >
            Create Tool
          </button>
        </div>
      )}
    </div>
  );
}
