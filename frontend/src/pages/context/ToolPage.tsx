import { useState, useEffect } from 'react';
import { Settings, Loader2, Trash2 } from 'lucide-react';
import { useTemplates, useToolList, useCreateTool, useDeleteTool, useToggleTool } from '@/hooks/useTools';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { cn } from '@/lib/utils';
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
    tags: selectedTags.length > 0 ? selectedTags.join(',') : undefined,
  });
  const createMutation = useCreateTool();
  const deleteMutation = useDeleteTool();
  const toggleMutation = useToggleTool();

  useEffect(() => {
    if (!templateData?.templates) return;
    const template = templateData.templates.find((t: ToolTemplate) => t.id === selectedTemplateId);
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

  const openCreateModal = () => { setSelectedTemplateId('http_get_api'); setShowCreateModal(true); };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    let inputSchema: Record<string, unknown>;
    try { inputSchema = JSON.parse(formData._inputSchemaJson ?? '{}'); }
    catch { alert('Input Schema is not valid JSON'); return; }

    const payload: UserToolCreate = {
      name: formData.name, display_name: formData.display_name, description: formData.description,
      execution_mode: formData.execution_mode, input_schema: inputSchema, category: formData.category,
      tags: formData.tags, timeout: formData.timeout, enabled: formData.enabled, is_public: formData.is_public,
    };
    if (formData.inner_tool_name) {
      payload.inner_tool_name = formData.inner_tool_name;
      payload.parameter_mapping = formData.parameter_mapping;
    }
    if (formData.execution_mode === 'http' && !formData.inner_tool_name) {
      let headers: Record<string, unknown>;
      try { headers = JSON.parse(formData._headersJson ?? '{}'); }
      catch { alert('Headers is not valid JSON'); return; }
      payload.http_config = { method: httpMethod, url: httpUrl, headers, timeout: formData.timeout };
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
    try { await deleteMutation.mutateAsync(id); }
    catch (error) { alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };

  const handleToggle = async (id: string, currentEnabled: boolean) => {
    try { await toggleMutation.mutateAsync({ id, enabled: !currentEnabled }); }
    catch (error) { alert(`Toggle failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };

  if (toolsLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const tools = toolData?.tools ?? [];
  const isInnerTool = (toolType: string) => toolType === 'inner';
  const allTags = Array.from(new Set(tools.flatMap((t) => t.tags ?? []))).sort();

  return (
    <div>
      <div className="mb-6 flex justify-between items-center">
        <div>
          <h2 className="text-xl font-bold">Tools</h2>
          <p className="text-sm text-muted-foreground mt-1">Manage built-in and custom tools for your AI agents</p>
        </div>
        <Button onClick={openCreateModal}>+ Create Tool</Button>
      </div>

      {allTags.length > 0 && (
        <div className="mb-6 bg-card rounded-lg border border-border p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold">Filter by Tags</h3>
            {selectedTags.length > 0 && (
              <Button variant="ghost" size="sm" onClick={() => setSelectedTags([])}>Clear All</Button>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {allTags.map((tag) => (
              <button
                key={tag}
                type="button"
                onClick={() => setSelectedTags((prev) => prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag])}
                className={cn(
                  'px-3 py-1.5 text-sm rounded-full font-medium transition-colors',
                  selectedTags.includes(tag) ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/70'
                )}
              >
                {tag}{selectedTags.includes(tag) && ' ✓'}
              </button>
            ))}
          </div>
          {selectedTags.length > 0 && (
            <div className="mt-3 text-xs text-muted-foreground">Showing tools with: {selectedTags.join(', ')}</div>
          )}
        </div>
      )}

      {tools.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {tools.map((tool) => {
            const inner = isInnerTool(tool.tool_type);
            return (
              <div key={tool.id} className="bg-card rounded-lg border border-border p-6 hover:shadow-lg transition-shadow">
                <div className="flex items-start justify-between mb-3">
                  <div className="flex-1 min-w-0">
                    <h3 className="text-lg font-semibold truncate">{tool.display_name || tool.name}</h3>
                    <p className="text-xs text-muted-foreground font-mono">{tool.name}</p>
                  </div>
                  <div className="flex gap-1.5 ml-2 shrink-0">
                    {inner ? (
                      <Badge variant="outline">Built-in</Badge>
                    ) : (
                      <Badge variant={tool.execution_mode === 'http' ? 'default' : 'secondary'}>
                        {tool.execution_mode}
                      </Badge>
                    )}
                    <Badge variant={tool.enabled ? 'default' : 'secondary'}>
                      {tool.enabled ? 'Enabled' : 'Disabled'}
                    </Badge>
                  </div>
                </div>

                <p className="text-sm text-muted-foreground mb-3 line-clamp-2">{tool.description}</p>

                {tool.tags && tool.tags.length > 0 && (
                  <div className="flex flex-wrap gap-1.5 mb-3">
                    {tool.tags.map((tag) => <Badge key={tag} variant="secondary">{tag}</Badge>)}
                  </div>
                )}

                <div className="text-xs text-muted-foreground mb-4 space-y-1">
                  {tool.category && <p>Category: {tool.category}</p>}
                  {!inner && <p>Usage: {tool.usage_count} calls</p>}
                  <p>Created: {new Date(tool.created_at).toLocaleDateString()}</p>
                </div>

                {!inner && (
                  <div className="flex gap-2">
                    <Button
                      variant={tool.enabled ? 'outline' : 'default'}
                      size="sm"
                      className="flex-1"
                      disabled={toggleMutation.isPending}
                      onClick={() => handleToggle(tool.id, tool.enabled)}
                    >
                      {tool.enabled ? 'Disable' : 'Enable'}
                    </Button>
                    <Button
                      variant="outline"
                      size="icon"
                      className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                      disabled={deleteMutation.isPending}
                      onClick={() => handleDelete(tool.id, tool.display_name || tool.name)}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="text-center py-12">
          <Settings className="mx-auto size-12 text-muted-foreground/40 mb-4" />
          <h3 className="text-lg font-medium mb-1">No Tools Yet</h3>
          <p className="text-muted-foreground mb-4">Create your first custom tool to extend your AI agent's capabilities</p>
          <Button onClick={openCreateModal}>Create Tool</Button>
        </div>
      )}

      {/* Create Dialog */}
      <Dialog open={showCreateModal} onOpenChange={(open) => { if (!open) setShowCreateModal(false); }}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Create Tool</DialogTitle>
          </DialogHeader>

          <form id="create-tool-form" onSubmit={handleCreate} className="space-y-4">
            <div className="space-y-1.5">
              <Label>Template *</Label>
              <Select value={selectedTemplateId} onValueChange={setSelectedTemplateId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a template" />
                </SelectTrigger>
                <SelectContent>
                  {templatesLoading ? (
                    <SelectItem value="_loading" disabled>Loading templates...</SelectItem>
                  ) : (
                    templateData?.templates.map((t: ToolTemplate) => (
                      <SelectItem key={t.id} value={t.id}>{t.name} — {t.description}</SelectItem>
                    ))
                  )}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-1.5">
              <Label>Name *</Label>
              <Input value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} placeholder="e.g., weather_api" required />
            </div>

            <div className="space-y-1.5">
              <Label>Display Name *</Label>
              <Input value={formData.display_name} onChange={(e) => setFormData({ ...formData, display_name: e.target.value })} placeholder="e.g., Weather API" required />
            </div>

            <div className="space-y-1.5">
              <Label>Description *</Label>
              <Textarea value={formData.description} onChange={(e) => setFormData({ ...formData, description: e.target.value })} placeholder="Describe what this tool does" rows={2} required />
            </div>

            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-1.5">
                <Label>Execution Mode</Label>
                <Input value={formData.execution_mode} readOnly className="bg-muted text-muted-foreground cursor-not-allowed" />
              </div>
              <div className="space-y-1.5">
                <Label>Category</Label>
                <Input value={formData.category ?? ''} onChange={(e) => setFormData({ ...formData, category: e.target.value })} />
              </div>
              <div className="space-y-1.5">
                <Label>Timeout (s)</Label>
                <Input type="number" value={formData.timeout} onChange={(e) => setFormData({ ...formData, timeout: parseInt(e.target.value) || 30 })} min={1} max={3600} />
              </div>
            </div>

            {formData.execution_mode === 'http' && !formData.inner_tool_name && (
              <fieldset className="border border-border rounded-md p-4 space-y-3">
                <legend className="text-sm font-medium text-muted-foreground px-2">HTTP Configuration</legend>
                <div className="grid grid-cols-4 gap-3">
                  <div className="space-y-1.5">
                    <Label className="text-xs">Method</Label>
                    <Select value={httpMethod} onValueChange={setHttpMethod}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => (
                          <SelectItem key={m} value={m}>{m}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="col-span-3 space-y-1.5">
                    <Label className="text-xs">URL *</Label>
                    <Input value={httpUrl} onChange={(e) => setHttpUrl(e.target.value)} placeholder="https://api.example.com/endpoint" required />
                  </div>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">Headers (JSON)</Label>
                  <Textarea value={formData._headersJson} onChange={(e) => setFormData({ ...formData, _headersJson: e.target.value })} rows={3} className="font-mono text-sm" />
                </div>
              </fieldset>
            )}

            {formData.execution_mode === 'server_run' && !formData.inner_tool_name && (
              <div className="space-y-1.5">
                <Label>Python Code *</Label>
                <Textarea
                  value={formData.code ?? ''}
                  onChange={(e) => setFormData({ ...formData, code: e.target.value })}
                  rows={8}
                  placeholder={`# input_data contains your parameters\nresult = {'output': input_data['text']}`}
                  className="font-mono text-sm"
                  required
                />
              </div>
            )}

            <div className="space-y-1.5">
              <Label>Input Schema (JSON)</Label>
              <Textarea value={formData._inputSchemaJson} onChange={(e) => setFormData({ ...formData, _inputSchemaJson: e.target.value })} rows={6} className="font-mono text-sm" />
            </div>
          </form>

          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCreateModal(false)}>Cancel</Button>
            <Button type="submit" form="create-tool-form" disabled={createMutation.isPending}>
              {createMutation.isPending ? 'Creating...' : 'Create Tool'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
