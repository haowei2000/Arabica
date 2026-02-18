import { useState, useEffect } from 'react';
import { Wrench, Loader2, Trash2, ToggleLeft, ToggleRight } from 'lucide-react';
import { useTemplates, useToolList, useCreateTool, useDeleteTool, useToggleTool } from '@/hooks/useTools';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { ViewToggle, type ViewMode } from '@/components/ViewToggle';
import { AccordionItem } from '@/components/AccordionItem';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/formatDate';
import type { ToolTemplate, UserToolCreate } from '@/types/tool';

const INITIAL_FORM: UserToolCreate & { _headersJson?: string; _inputSchemaJson?: string } = {
  name: '', display_name: '', description: '', execution_mode: 'http',
  input_schema: {}, http_config: null, code: null, category: 'custom',
  tags: [], timeout: 30, enabled: true, is_public: false,
  _headersJson: '{}', _inputSchemaJson: '{}',
};

const MODE_COLOR: Record<string, string> = {
  http:       'bg-blue-100 text-blue-700 border-blue-200/70 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-800/50',
  server_run: 'bg-purple-100 text-purple-700 border-purple-200/70 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-800/50',
  inner:      'bg-muted text-muted-foreground border-border/50',
};

export default function ToolPage() {
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [selectedTemplateId, setSelectedTemplateId] = useState('http_get_api');
  const [formData, setFormData] = useState(INITIAL_FORM);
  const [httpMethod, setHttpMethod] = useState('GET');
  const [httpUrl, setHttpUrl] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [viewMode, setViewMode] = useState<ViewMode>('card');
  const [openItemId, setOpenItemId] = useState<string | null>(null);

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
      name: (tpl.name as string) ?? '', display_name: (tpl.display_name as string) ?? '',
      description: (tpl.description as string) ?? '', execution_mode: template.execution_mode,
      input_schema: inputSchema, category: (tpl.category as string) ?? 'custom',
      tags: (tpl.tags as string[]) ?? [], timeout: (tpl.timeout as number) ?? 30,
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
    if (formData.inner_tool_name) { payload.inner_tool_name = formData.inner_tool_name; payload.parameter_mapping = formData.parameter_mapping; }
    if (formData.execution_mode === 'http' && !formData.inner_tool_name) {
      let headers: Record<string, unknown>;
      try { headers = JSON.parse(formData._headersJson ?? '{}'); }
      catch { alert('Headers is not valid JSON'); return; }
      payload.http_config = { method: httpMethod, url: httpUrl, headers, timeout: formData.timeout };
    }
    if (formData.execution_mode === 'server_run' && !formData.inner_tool_name) { payload.code = formData.code; }
    try { await createMutation.mutateAsync(payload); setShowCreateModal(false); }
    catch (error) { alert(`Creation failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };

  const handleDelete = async (id: string, name: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Delete "${name}"?`)) return;
    try { await deleteMutation.mutateAsync(id); }
    catch (error) { alert(`Deletion failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };

  const handleToggle = async (id: string, currentEnabled: boolean, e: React.MouseEvent) => {
    e.stopPropagation();
    try { await toggleMutation.mutateAsync({ id, enabled: !currentEnabled }); }
    catch (error) { alert(`Toggle failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };

  const handleModeToggle = (m: ViewMode) => {
    setViewMode(m);
    if (m !== 'drawer') setOpenItemId(null);
  };

  if (toolsLoading) {
    return <div className="flex items-center justify-center py-16"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>;
  }

  const tools = toolData?.tools ?? [];
  const isInnerTool = (toolType: string) => toolType === 'inner';
  const allTags = Array.from(new Set(tools.flatMap((t) => t.tags ?? []))).sort();

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <Wrench className="size-3.5 text-muted-foreground" />
            <h2 className="text-sm font-semibold">Tools</h2>
          </div>
          <span className="text-xs text-muted-foreground bg-muted rounded-full px-2 py-0.5 tabular-nums">{tools.length}</span>
        </div>
        <div className="flex items-center gap-2">
          <ViewToggle mode={viewMode} onToggle={handleModeToggle} />
          <Button size="sm" onClick={openCreateModal}>+ New</Button>
        </div>
      </div>

      {/* Tag filter bar */}
      {allTags.length > 0 && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          {selectedTags.length > 0 && (
            <button type="button" onClick={() => setSelectedTags([])}
              className="text-[10px] px-2 py-1 rounded-full border border-border text-muted-foreground hover:text-foreground transition-colors">
              Clear
            </button>
          )}
          {allTags.map((tag) => (
            <button key={tag} type="button"
              onClick={() => setSelectedTags((prev) => prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag])}
              className={cn('text-[10px] px-2.5 py-1 rounded-full border font-medium transition-colors',
                selectedTags.includes(tag)
                  ? 'bg-foreground text-background border-foreground'
                  : 'bg-muted/50 text-muted-foreground border-border/50 hover:border-foreground/30'
              )}>
              {tag}
            </button>
          ))}
        </div>
      )}

      {tools.length > 0 ? viewMode === 'card' ? (
        /* ── Card grid ── */
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {tools.map((tool) => {
            const inner = isInnerTool(tool.tool_type);
            const modeKey = inner ? 'inner' : tool.execution_mode;
            return (
              <div key={tool.id} className="rounded-xl border border-border bg-card p-4 flex flex-col gap-2.5 hover:bg-muted/20 transition-colors">
                <div className="flex items-start gap-2.5">
                  <span className={cn('size-2 rounded-full mt-1 shrink-0',
                    inner ? 'bg-muted-foreground/40' : tool.enabled ? 'bg-green-500' : 'bg-muted-foreground/30'
                  )} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold leading-snug truncate">{tool.display_name || tool.name}</p>
                    {tool.display_name && <p className="text-[10px] text-muted-foreground/60 font-mono truncate">{tool.name}</p>}
                  </div>
                </div>
                {tool.description && <p className="text-xs text-muted-foreground line-clamp-2">{tool.description}</p>}
                <div className="flex flex-wrap gap-1.5">
                  <span className={cn('text-[10px] px-2 py-0.5 rounded-full border', MODE_COLOR[modeKey] ?? MODE_COLOR.inner)}>
                    {inner ? 'built-in' : tool.execution_mode}
                  </span>
                  {tool.category && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50">
                      {tool.category}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3 text-[10px] text-muted-foreground mt-auto">
                  {!inner && <span className="tabular-nums">{tool.usage_count ?? 0} calls</span>}
                  {!inner && <span className={cn(tool.enabled ? 'text-green-600' : '')}>{tool.enabled ? 'enabled' : 'disabled'}</span>}
                  <span className="ml-auto">{formatRelativeTime(tool.created_at)}</span>
                </div>
                {!inner && (
                  <div className="flex gap-2 pt-2 border-t border-border/40">
                    <Button size="sm" variant="outline" className="flex-1 gap-1.5"
                      disabled={toggleMutation.isPending} onClick={(e) => handleToggle(tool.id, tool.enabled, e)}>
                      {tool.enabled ? <ToggleRight className="size-3.5 text-green-600" /> : <ToggleLeft className="size-3.5" />}
                      {tool.enabled ? 'Disable' : 'Enable'}
                    </Button>
                    <Button size="sm" variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                      disabled={deleteMutation.isPending} onClick={(e) => handleDelete(tool.id, tool.display_name || tool.name, e)}>
                      <Trash2 className="size-3.5" />
                    </Button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-card overflow-visible divide-y divide-border/50">
          {tools.map((tool) => {
            const inner = isInnerTool(tool.tool_type);
            const modeKey = inner ? 'inner' : tool.execution_mode;

            const statusDot = (
              <span className={cn('size-2 rounded-full shrink-0',
                inner ? 'bg-muted-foreground/40' : tool.enabled ? 'bg-green-500' : 'bg-muted-foreground/30'
              )} />
            );
            const modeChip = (
              <span className={cn('text-[10px] px-2 py-0.5 rounded-full border shrink-0', MODE_COLOR[modeKey] ?? MODE_COLOR.inner)}>
                {inner ? 'built-in' : tool.execution_mode}
              </span>
            );
            const categoryChip = tool.category ? (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 shrink-0">
                {tool.category}
              </span>
            ) : null;

            const rowHeader = (
              <>
                {statusDot}
                <div className="flex-1 min-w-0 flex items-baseline gap-2">
                  <span className="text-sm font-medium truncate">{tool.display_name || tool.name}</span>
                  {tool.display_name && <span className="text-[10px] text-muted-foreground/60 font-mono truncate hidden sm:block">{tool.name}</span>}
                </div>
                {categoryChip}
                {modeChip}
                {!inner && <span className="text-[10px] text-muted-foreground/50 tabular-nums shrink-0">{tool.usage_count ?? 0}×</span>}
              </>
            );

            if (viewMode === 'list') {
              return (
                <div key={tool.id} className="group relative flex items-center gap-3 px-4 py-2.5 hover:bg-muted/40 transition-colors">
                  {statusDot}
                  <div className="flex-1 min-w-0 flex items-baseline gap-2">
                    <span className="text-sm font-medium truncate">{tool.display_name || tool.name}</span>
                    {tool.display_name && <span className="text-[10px] text-muted-foreground/60 font-mono truncate hidden sm:block">{tool.name}</span>}
                  </div>
                  {categoryChip}
                  {modeChip}
                  {!inner && <span className="text-[10px] text-muted-foreground/50 tabular-nums shrink-0">{tool.usage_count ?? 0}×</span>}
                  {!inner && (
                    <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                      <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted transition-colors"
                        onClick={(e) => handleToggle(tool.id, tool.enabled, e)} title={tool.enabled ? 'Disable' : 'Enable'}>
                        {tool.enabled ? <ToggleRight className="size-4 text-green-600" /> : <ToggleLeft className="size-4 text-muted-foreground" />}
                      </button>
                      <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-destructive/10 transition-colors"
                        onClick={(e) => handleDelete(tool.id, tool.display_name || tool.name, e)}>
                        <Trash2 className="size-3.5 text-destructive/70" />
                      </button>
                    </div>
                  )}
                  {/* Hover tooltip */}
                  <div className="absolute right-2 top-full mt-1 z-50 w-80 rounded-xl border border-border bg-card shadow-lg shadow-black/10 p-3 invisible opacity-0 group-hover:visible group-hover:opacity-100 transition-[opacity,visibility] duration-150 pointer-events-none">
                    <div className="space-y-2 text-xs">
                      {tool.description && <p className="text-foreground/80 leading-relaxed">{tool.description}</p>}
                      {tool.tags && tool.tags.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {tool.tags.map((tag) => <span key={tag} className="px-1.5 py-0.5 rounded bg-muted text-muted-foreground text-[10px]">{tag}</span>)}
                        </div>
                      )}
                      <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pt-1.5 border-t border-border/50 text-muted-foreground">
                        <span>Mode</span><span className="text-foreground">{inner ? 'built-in' : tool.execution_mode}</span>
                        {!inner && (<><span>Usage</span><span className="text-foreground tabular-nums">{tool.usage_count ?? 0} calls</span>
                          <span>Status</span><span className="text-foreground">{tool.enabled ? 'Enabled' : 'Disabled'}</span>
                          <span>Created</span><span className="text-foreground">{formatRelativeTime(tool.created_at)}</span></>)}
                      </div>
                    </div>
                  </div>
                </div>
              );
            }

            // Drawer mode
            const schemaStr = tool.input_schema && Object.keys(tool.input_schema).length > 0
              ? JSON.stringify(tool.input_schema, null, 2) : null;

            return (
              <AccordionItem
                key={tool.id}
                isOpen={openItemId === tool.id}
                onToggle={() => setOpenItemId(openItemId === tool.id ? null : tool.id)}
                header={rowHeader}
                detail={
                  <div className="space-y-3">
                    {tool.description && <p className="text-xs text-foreground/80 leading-relaxed">{tool.description}</p>}
                    {tool.tags && tool.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {tool.tags.map((tag) => <span key={tag} className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{tag}</span>)}
                      </div>
                    )}
                    <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
                      <span className="text-muted-foreground">Mode</span><span>{inner ? 'built-in' : tool.execution_mode}</span>
                      {tool.category && <><span className="text-muted-foreground">Category</span><span>{tool.category}</span></>}
                      {!inner && (<>
                        <span className="text-muted-foreground">Timeout</span><span className="tabular-nums">{tool.timeout ?? 30}s</span>
                        <span className="text-muted-foreground">Usage</span><span className="tabular-nums">{tool.usage_count ?? 0} calls</span>
                        <span className="text-muted-foreground">Status</span>
                        <span className="flex items-center gap-1.5">
                          <span className={cn('size-1.5 rounded-full', tool.enabled ? 'bg-green-500' : 'bg-muted-foreground/30')} />
                          {tool.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                        <span className="text-muted-foreground">Public</span><span>{tool.is_public ? 'Yes' : 'No'}</span>
                        <span className="text-muted-foreground">Created</span><span>{formatRelativeTime(tool.created_at)}</span>
                      </>)}
                    </div>
                    {schemaStr && (
                      <div>
                        <p className="text-[10px] text-muted-foreground mb-1 font-semibold uppercase tracking-wide">Input Schema</p>
                        <pre className="text-[10px] text-muted-foreground font-mono bg-muted/50 rounded-md p-2 overflow-x-auto max-h-32 overflow-y-auto">
                          {schemaStr}
                        </pre>
                      </div>
                    )}
                    {!inner && (
                      <div className="flex gap-2 pt-2 border-t border-border/40">
                        <Button size="sm" variant="outline" className="flex-1 gap-1.5"
                          disabled={toggleMutation.isPending} onClick={(e) => handleToggle(tool.id, tool.enabled, e)}>
                          {tool.enabled ? <ToggleRight className="size-3.5 text-green-600" /> : <ToggleLeft className="size-3.5" />}
                          {tool.enabled ? 'Disable' : 'Enable'}
                        </Button>
                        <Button size="sm" variant="outline" className="text-destructive hover:text-destructive hover:bg-destructive/10 border-destructive/30"
                          disabled={deleteMutation.isPending} onClick={(e) => handleDelete(tool.id, tool.display_name || tool.name, e)}>
                          <Trash2 className="size-3.5" />
                        </Button>
                      </div>
                    )}
                  </div>
                }
              />
            );
          })}
        </div>
      ) : (
        <div className="text-center py-16 border border-dashed border-border rounded-xl">
          <Wrench className="mx-auto size-8 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground mb-3">No tools yet</p>
          <Button size="sm" onClick={openCreateModal}>Create Tool</Button>
        </div>
      )}

      {/* Create Dialog */}
      <Dialog open={showCreateModal} onOpenChange={(open) => { if (!open) setShowCreateModal(false); }}>
        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
          <DialogHeader><DialogTitle>Create Tool</DialogTitle></DialogHeader>
          <form id="create-tool-form" onSubmit={handleCreate} className="space-y-4">
            <div className="space-y-1.5"><Label>Template *</Label>
              <Select value={selectedTemplateId} onValueChange={setSelectedTemplateId}>
                <SelectTrigger><SelectValue placeholder="Select a template" /></SelectTrigger>
                <SelectContent>
                  {templatesLoading ? <SelectItem value="_loading" disabled>Loading templates...</SelectItem>
                    : templateData?.templates.map((t: ToolTemplate) => <SelectItem key={t.id} value={t.id}>{t.name} — {t.description}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5"><Label>Name *</Label><Input value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} placeholder="e.g., weather_api" required /></div>
            <div className="space-y-1.5"><Label>Display Name *</Label><Input value={formData.display_name} onChange={(e) => setFormData({ ...formData, display_name: e.target.value })} placeholder="e.g., Weather API" required /></div>
            <div className="space-y-1.5"><Label>Description *</Label><Textarea value={formData.description} onChange={(e) => setFormData({ ...formData, description: e.target.value })} placeholder="Describe what this tool does" rows={2} required /></div>
            <div className="grid grid-cols-3 gap-4">
              <div className="space-y-1.5"><Label>Execution Mode</Label><Input value={formData.execution_mode} readOnly className="bg-muted text-muted-foreground cursor-not-allowed" /></div>
              <div className="space-y-1.5"><Label>Category</Label><Input value={formData.category ?? ''} onChange={(e) => setFormData({ ...formData, category: e.target.value })} /></div>
              <div className="space-y-1.5"><Label>Timeout (s)</Label><Input type="number" value={formData.timeout} onChange={(e) => setFormData({ ...formData, timeout: parseInt(e.target.value) || 30 })} min={1} max={3600} /></div>
            </div>
            {formData.execution_mode === 'http' && !formData.inner_tool_name && (
              <fieldset className="border border-border rounded-md p-4 space-y-3">
                <legend className="text-sm font-medium text-muted-foreground px-2">HTTP Configuration</legend>
                <div className="grid grid-cols-4 gap-3">
                  <div className="space-y-1.5"><Label className="text-xs">Method</Label>
                    <Select value={httpMethod} onValueChange={setHttpMethod}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>{['GET','POST','PUT','PATCH','DELETE'].map((m) => <SelectItem key={m} value={m}>{m}</SelectItem>)}</SelectContent>
                    </Select>
                  </div>
                  <div className="col-span-3 space-y-1.5"><Label className="text-xs">URL *</Label><Input value={httpUrl} onChange={(e) => setHttpUrl(e.target.value)} placeholder="https://api.example.com/endpoint" required /></div>
                </div>
                <div className="space-y-1.5"><Label className="text-xs">Headers (JSON)</Label><Textarea value={formData._headersJson} onChange={(e) => setFormData({ ...formData, _headersJson: e.target.value })} rows={3} className="font-mono text-sm" /></div>
              </fieldset>
            )}
            {formData.execution_mode === 'server_run' && !formData.inner_tool_name && (
              <div className="space-y-1.5"><Label>Python Code *</Label>
                <Textarea value={formData.code ?? ''} onChange={(e) => setFormData({ ...formData, code: e.target.value })} rows={8}
                  placeholder={`# input_data contains your parameters\nresult = {'output': input_data['text']}`} className="font-mono text-sm" required />
              </div>
            )}
            <div className="space-y-1.5"><Label>Input Schema (JSON)</Label><Textarea value={formData._inputSchemaJson} onChange={(e) => setFormData({ ...formData, _inputSchemaJson: e.target.value })} rows={6} className="font-mono text-sm" /></div>
          </form>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowCreateModal(false)}>Cancel</Button>
            <Button type="submit" form="create-tool-form" disabled={createMutation.isPending}>{createMutation.isPending ? 'Creating...' : 'Create Tool'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
