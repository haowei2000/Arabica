import { useState } from 'react';
import {
  Wrench, Loader2, Trash2, ToggleLeft, ToggleRight,
  Search, Play, ChevronRight, ChevronDown,
  Link2, Layers, Brain, MoveRight,
} from 'lucide-react';
import {
  useToolList, useDeleteTool, useToggleTool, useTestTool,
  useProbeMcp, useImportFromMcp, useToolBundles,
} from '@/hooks/useTools';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle,
} from '@/components/ui/dialog';
import { ViewToggle, type ViewMode } from '@/components/ViewToggle';
import { AccordionItem } from '@/components/AccordionItem';
import { ContextViewer } from '@/components/ContextViewer';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/formatDate';
import type { UserTool, MCPServerConfig, MCPTransport, MCPToolInfo } from '@/types/tool';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function getSchemaProperties(schema: Record<string, unknown> | undefined) {
  const s = schema as {
    properties?: Record<string, { type?: string; description?: string }>;
    required?: string[];
  } | undefined;
  return {
    props: s?.properties ?? {},
    required: new Set(s?.required ?? []),
  };
}

const MODE_COLOR: Record<string, string> = {
  http:       'bg-blue-100 text-blue-700 border-blue-200/70 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-800/50',
  server_run: 'bg-purple-100 text-purple-700 border-purple-200/70 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-800/50',
  inner:      'bg-muted text-muted-foreground border-border/50',
  mcp:        'bg-green-100 text-green-700 border-green-200/70 dark:bg-green-900/30 dark:text-green-300 dark:border-green-800/50',
};

// ─── Component ───────────────────────────────────────────────────────────────

export default function ToolPage() {
  // ── List state ──
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [viewMode, setViewMode] = useState<ViewMode>('card');
  const [openItemId, setOpenItemId] = useState<string | null>(null);
  const [contextViewId, setContextViewId] = useState<string | null>(null);
  const [groupByBundle, setGroupByBundle] = useState(true);
  const [collapsedBundles, setCollapsedBundles] = useState<Set<string>>(new Set());

  // ── Test dialog state ──
  const [testingTool, setTestingTool] = useState<UserTool | null>(null);
  const [testParamsJson, setTestParamsJson] = useState('{}');

  // ── MCP import dialog state ──
  const [showMcpModal, setShowMcpModal] = useState(false);
  const [mcpTransport, setMcpTransport] = useState<MCPTransport>('sse');
  const [mcpUrl, setMcpUrl] = useState('');
  const [mcpCommand, setMcpCommand] = useState('');
  const [mcpArgsText, setMcpArgsText] = useState('');
  const [mcpEnvText, setMcpEnvText] = useState('');
  const [mcpDiscovered, setMcpDiscovered] = useState<MCPToolInfo[]>([]);
  const [mcpSelected, setMcpSelected] = useState<Set<string>>(new Set());
  const [mcpProbeError, setMcpProbeError] = useState<string | null>(null);
  const [mcpIsPublic, setMcpIsPublic] = useState(false);

  // ── Queries & mutations ──
  const { data: toolData, isLoading: toolsLoading } = useToolList({
    enabled_only: false,
    tags: selectedTags.length > 0 ? selectedTags.join(',') : undefined,
  });
  const { data: bundleData } = useToolBundles({ include_public: true });
  const deleteMutation = useDeleteTool();
  const toggleMutation = useToggleTool();
  const testMutation = useTestTool();
  const probeMcpMutation = useProbeMcp();
  const importFromMcpMutation = useImportFromMcp();

  // ── Handlers ──

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

  const openTestDialog = (tool: UserTool) => {
    setTestingTool(tool);
    const schema = tool.input_schema as { properties?: Record<string, { type?: string; default?: unknown }> };
    const params: Record<string, unknown> = {};
    if (schema?.properties) {
      for (const [key, prop] of Object.entries(schema.properties)) {
        if (prop.default !== undefined) params[key] = prop.default;
        else if (prop.type === 'string') params[key] = '';
        else if (prop.type === 'integer' || prop.type === 'number') params[key] = 0;
        else if (prop.type === 'boolean') params[key] = false;
        else if (prop.type === 'array') params[key] = [];
        else if (prop.type === 'object') params[key] = {};
      }
    }
    setTestParamsJson(JSON.stringify(params, null, 2));
    testMutation.reset();
  };

  const handleRunTest = () => {
    if (!testingTool) return;
    let params: Record<string, unknown>;
    try { params = JSON.parse(testParamsJson); }
    catch { alert('Parameters is not valid JSON'); return; }
    testMutation.mutate({ id: testingTool.id, parameters: params });
  };

  // ── MCP helpers ──
  const mcpClientConfig = (): MCPServerConfig => ({
    transport: mcpTransport,
    url: mcpTransport === 'sse' ? mcpUrl : undefined,
    command: mcpTransport === 'stdio' ? mcpCommand : undefined,
    args: mcpTransport === 'stdio' ? mcpArgsText.split('\n').map(s => s.trim()).filter(Boolean) : undefined,
    env: mcpTransport === 'stdio'
      ? Object.fromEntries(mcpEnvText.split('\n').filter(l => l.includes('=')).map(l => { const i = l.indexOf('='); return [l.slice(0, i).trim(), l.slice(i + 1).trim()]; }))
      : undefined,
  });

  const handleProbeMcp = async () => {
    setMcpProbeError(null);
    setMcpDiscovered([]);
    setMcpSelected(new Set());
    try {
      const res = await probeMcpMutation.mutateAsync(mcpClientConfig());
      if (res.success) {
        setMcpDiscovered(res.tools);
      } else {
        setMcpProbeError(res.error ?? 'Unknown error');
      }
    } catch (err) {
      setMcpProbeError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleImportFromMcp = async () => {
    if (mcpSelected.size === 0) return;
    try {
      const res = await importFromMcpMutation.mutateAsync({
        ...mcpClientConfig(),
        tool_names: [...mcpSelected],
        is_public: mcpIsPublic,
      });
      const summary = [`Imported: ${res.imported.length}`];
      if (res.skipped.length) summary.push(`Skipped (already exist): ${res.skipped.join(', ')}`);
      if (res.failed.length) summary.push(`Failed: ${res.failed.join(', ')}`);
      if (res.bundle_name) summary.push(`Grouped into bundle: "${res.bundle_name}"`);
      alert(summary.join('\n'));
      setShowMcpModal(false);
    } catch (err) {
      alert(`Import failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const toggleMcpTool = (name: string) => {
    setMcpSelected(prev => {
      const next = new Set(prev);
      next.has(name) ? next.delete(name) : next.add(name);
      return next;
    });
  };

  // ── Loading ──
  if (toolsLoading) {
    return <div className="flex items-center justify-center py-16"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>;
  }

  const tools = toolData?.tools ?? [];
  const isInner = (t: UserTool) => t.tool_type === 'inner';
  const allTags = Array.from(new Set(tools.flatMap(t => t.tags ?? []))).sort();

  // ── Bundle grouping ──
  const bundles = bundleData?.bundles ?? [];
  const toolById = new Map(tools.map(t => [t.id, t]));
  let bundleGroups: { groups: { bundle: typeof bundles[0]; tools: UserTool[] }[]; unassigned: UserTool[] } | null = null;
  if (groupByBundle && bundles.length > 0) {
    const assignedIds = new Set<string>();
    const groups: { bundle: typeof bundles[0]; tools: UserTool[] }[] = [];
    for (const bundle of bundles) {
      const bundleTools = bundle.tool_ids.flatMap(id => {
        const t = toolById.get(id);
        return t ? [t] : [];
      });
      if (bundleTools.length > 0) {
        bundleTools.forEach(t => assignedIds.add(t.id));
        groups.push({ bundle, tools: bundleTools });
      }
    }
    const unassigned = tools.filter(t => !assignedIds.has(t.id));
    bundleGroups = { groups, unassigned };
  }

  const toggleBundle = (id: string) => setCollapsedBundles(prev => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  // ─── Per-tool renderer (card / list / drawer) ─────────────────────────────

  const renderTool = (tool: UserTool): React.ReactNode => {
    const inner = isInner(tool);
    const modeKey = tool.tool_type || 'mcp';

    if (viewMode === 'card') {
      return (
        <div key={tool.id} className={cn(
          'rounded-xl border bg-card p-4 flex flex-col gap-2.5 hover:bg-muted/20 transition-colors',
          inner ? 'border-border' : tool.enabled ? 'border-green-200/70 dark:border-green-800/40' : 'border-amber-200/70 dark:border-amber-800/40',
          !inner && !tool.enabled && 'opacity-70')}>
          <div className="flex items-start gap-2.5">
            <span className={cn('size-2 rounded-full mt-1 shrink-0',
              inner ? 'bg-muted-foreground/40' : tool.enabled ? 'bg-green-500' : 'bg-amber-400')} />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold leading-snug truncate">{tool.display_name || tool.name}</p>
              {tool.display_name && <p className="text-[10px] text-muted-foreground/60 font-mono truncate">{tool.name}</p>}
            </div>
            {!inner && (
              <span className={cn('text-[10px] px-1.5 py-0.5 rounded-full border shrink-0 font-medium',
                tool.enabled
                  ? 'bg-green-50 text-green-700 border-green-200/70 dark:bg-green-900/20 dark:text-green-400 dark:border-green-800/50'
                  : 'bg-amber-50 text-amber-700 border-amber-200/70 dark:bg-amber-900/20 dark:text-amber-400 dark:border-amber-800/50')}>
                {tool.enabled ? 'on' : 'off'}
              </span>
            )}
          </div>
          {tool.description && <p className="text-xs text-muted-foreground line-clamp-2">{tool.description}</p>}
          <div className="flex flex-wrap gap-1.5">
            <span className={cn('text-[10px] px-2 py-0.5 rounded-full border', MODE_COLOR[modeKey] ?? MODE_COLOR.mcp)}>
              {inner ? 'built-in' : modeKey}
            </span>
            {tool.category && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50">{tool.category}</span>
            )}
          </div>
          <div className="flex items-center gap-3 text-[10px] text-muted-foreground mt-auto">
            {!inner && <span className="tabular-nums">{tool.usage_count ?? 0} calls</span>}
            <span className="ml-auto">{formatRelativeTime(tool.created_at)}</span>
          </div>
          <div className="flex gap-2 pt-2 border-t border-border/40">
            {!inner && (
              <Button size="sm" variant="outline" className={cn('gap-1.5',
                tool.enabled ? 'text-green-700 border-green-200/70' : 'text-amber-700 border-amber-200/70')}
                disabled={toggleMutation.isPending} onClick={(e) => handleToggle(tool.id, tool.enabled, e)}>
                {tool.enabled ? <ToggleRight className="size-3.5" /> : <ToggleLeft className="size-3.5" />}
              </Button>
            )}
            <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => { e.stopPropagation(); openTestDialog(tool); }}>
              <Play className="size-3.5" />Test
            </Button>
            <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => { e.stopPropagation(); setContextViewId(tool.id); }}>
              <Brain className="size-3.5" />
            </Button>
            {!inner && (
              <Button size="sm" variant="outline" className="text-destructive border-destructive/30 ml-auto"
                disabled={deleteMutation.isPending} onClick={(e) => handleDelete(tool.id, tool.display_name || tool.name, e)}>
                <Trash2 className="size-3.5" />
              </Button>
            )}
          </div>
        </div>
      );
    }

    const statusDot = <span className={cn('size-2 rounded-full shrink-0', inner ? 'bg-muted-foreground/40' : tool.enabled ? 'bg-green-500' : 'bg-amber-400')} />;
    const modeChip = <span className={cn('text-[10px] px-2 py-0.5 rounded-full border shrink-0', MODE_COLOR[modeKey] ?? MODE_COLOR.mcp)}>{inner ? 'built-in' : modeKey}</span>;

    if (viewMode === 'list') {
      return (
        <div key={tool.id} className={cn('group relative flex items-center gap-3 px-4 py-2.5 hover:bg-muted/40 transition-colors', !inner && !tool.enabled && 'opacity-60')}>
          {statusDot}
          <div className="flex-1 min-w-0 flex items-baseline gap-2">
            <span className="text-sm font-medium truncate">{tool.display_name || tool.name}</span>
            {tool.display_name && <span className="text-[10px] text-muted-foreground/60 font-mono truncate hidden sm:block">{tool.name}</span>}
          </div>
          {tool.category && <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 shrink-0">{tool.category}</span>}
          {modeChip}
          <div className="flex gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
            <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted" onClick={(e) => { e.stopPropagation(); openTestDialog(tool); }}><Play className="size-3.5 text-muted-foreground" /></button>
            {!inner && (
              <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted" onClick={(e) => handleToggle(tool.id, tool.enabled, e)}>
                {tool.enabled ? <ToggleRight className="size-4 text-green-600" /> : <ToggleLeft className="size-4 text-amber-500" />}
              </button>
            )}
            <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-muted" onClick={(e) => { e.stopPropagation(); setContextViewId(tool.id); }}><Brain className="size-3.5 text-muted-foreground" /></button>
            {!inner && <button type="button" className="size-7 flex items-center justify-center rounded hover:bg-destructive/10" onClick={(e) => handleDelete(tool.id, tool.display_name || tool.name, e)}><Trash2 className="size-3.5 text-destructive/70" /></button>}
          </div>
        </div>
      );
    }

    // Drawer
    return (
      <AccordionItem key={tool.id} isOpen={openItemId === tool.id}
        onToggle={() => setOpenItemId(openItemId === tool.id ? null : tool.id)}
        header={
          <>
            {statusDot}
            <div className="flex-1 min-w-0 flex items-baseline gap-2">
              <span className="text-sm font-medium truncate">{tool.display_name || tool.name}</span>
              {tool.display_name && <span className="text-[10px] text-muted-foreground/60 font-mono truncate hidden sm:block">{tool.name}</span>}
            </div>
            {tool.category && <span className="text-[10px] px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border/50 shrink-0">{tool.category}</span>}
            {modeChip}
          </>
        }
        detail={
          <div className="space-y-3">
            {tool.description && <p className="text-xs text-foreground/80 leading-relaxed">{tool.description}</p>}
            <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-xs">
              {tool.category && <><span className="text-muted-foreground">Category</span><span>{tool.category}</span></>}
              {!inner && <><span className="text-muted-foreground">Timeout</span><span className="tabular-nums">{tool.timeout ?? 30}s</span></>}
              {!inner && <><span className="text-muted-foreground">Calls</span><span className="tabular-nums">{tool.usage_count ?? 0}</span></>}
            </div>
            {tool.parameter_mapping && Object.keys(tool.parameter_mapping).length > 0 && (
              <div>
                <p className="text-[10px] text-muted-foreground mb-1 font-semibold uppercase tracking-wide">Parameter Mapping</p>
                <div className="rounded border border-primary-200/40 dark:border-primary-800/30 divide-y divide-primary-100/60 dark:divide-primary-800/20">
                  {Object.entries(tool.parameter_mapping).map(([k, v]) => (
                    <div key={k} className="flex items-center gap-2 px-3 py-1.5 text-xs">
                      <code className="font-mono text-foreground/70">{k}</code>
                      <MoveRight className="size-3 text-primary-400 shrink-0" />
                      <code className={cn('font-mono', String(v).includes('{{') ? 'text-primary-700 dark:text-primary-300' : 'text-foreground')}>{v}</code>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="flex gap-2 pt-2 border-t border-border/40">
              <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => { e.stopPropagation(); openTestDialog(tool); }}><Play className="size-3.5" />Test</Button>
              <Button size="sm" variant="outline" className="gap-1.5" onClick={(e) => { e.stopPropagation(); setContextViewId(tool.id); }}><Brain className="size-3.5" />Context</Button>
              {!inner && (
                <Button size="sm" variant="outline" className="text-destructive border-destructive/30" onClick={(e) => handleDelete(tool.id, tool.display_name || tool.name, e)}><Trash2 className="size-3.5" /></Button>
              )}
            </div>
          </div>
        }
      />
    );
  };

  // ═══════════════════════════════════════════════════════════════════════════
  // ─── Main Render ──────────────────────────────────────────────────────────
  // ═══════════════════════════════════════════════════════════════════════════

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
          <button
            type="button"
            title="Group by bundle"
            onClick={() => setGroupByBundle(v => !v)}
            className={cn(
              'size-7 flex items-center justify-center rounded border transition-colors',
              groupByBundle
                ? 'bg-foreground text-background border-foreground'
                : 'bg-muted text-muted-foreground border-border hover:text-foreground'
            )}>
            <Layers className="size-3.5" />
          </button>
          <ViewToggle mode={viewMode} onToggle={(m) => { setViewMode(m); if (m !== 'drawer') setOpenItemId(null); }} />
          <Button size="sm" variant="outline" className="gap-1.5"
            onClick={() => { setShowMcpModal(true); setMcpDiscovered([]); setMcpSelected(new Set()); setMcpProbeError(null); }}>
            <Link2 className="size-3" /> From MCP
          </Button>
        </div>
      </div>

      {/* Tag filter */}
      {allTags.length > 0 && (
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          {selectedTags.length > 0 && (
            <button type="button" onClick={() => setSelectedTags([])}
              className="text-[10px] px-2 py-1 rounded-full border border-border text-muted-foreground hover:text-foreground transition-colors">Clear</button>
          )}
          {allTags.map(tag => (
            <button key={tag} type="button"
              onClick={() => setSelectedTags(prev => prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag])}
              className={cn('text-[10px] px-2.5 py-1 rounded-full border font-medium transition-colors',
                selectedTags.includes(tag)
                  ? 'bg-foreground text-background border-foreground'
                  : 'bg-muted/50 text-muted-foreground border-border/50 hover:border-foreground/30')}>
              {tag}
            </button>
          ))}
        </div>
      )}

      {/* ── Tool List ── */}
      {tools.length > 0 && bundleGroups ? (
        /* ── Grouped by bundle ── */
        <div className="space-y-3">
          {bundleGroups.groups.map(({ bundle, tools: bTools }) => {
            const collapsed = collapsedBundles.has(bundle.id);
            const typeColor: Record<string, string> = {
              inner: 'bg-muted text-muted-foreground border-border/50',
              mcp:   'bg-blue-50 text-blue-700 border-blue-200/70 dark:bg-blue-900/20 dark:text-blue-400 dark:border-blue-800/50',
              user:  'bg-purple-50 text-purple-700 border-purple-200/70 dark:bg-purple-900/20 dark:text-purple-400 dark:border-purple-800/50',
            };
            return (
              <div key={bundle.id} className="rounded-xl border border-border bg-card overflow-hidden">
                <button
                  type="button"
                  onClick={() => toggleBundle(bundle.id)}
                  className="w-full flex items-center gap-2.5 px-4 py-2.5 hover:bg-muted/40 transition-colors text-left"
                >
                  {collapsed ? <ChevronRight className="size-3.5 text-muted-foreground shrink-0" /> : <ChevronDown className="size-3.5 text-muted-foreground shrink-0" />}
                  <span className="text-sm font-semibold flex-1 truncate">{bundle.name}</span>
                  <span className={cn('text-[10px] px-2 py-0.5 rounded-full border shrink-0', typeColor[bundle.bundle_type] ?? typeColor.user)}>
                    {bundle.bundle_type}
                  </span>
                  <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">{bTools.length}</span>
                </button>
                {!collapsed && (
                  <div className={cn('border-t border-border/50',
                    viewMode === 'card' ? 'p-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3' : 'divide-y divide-border/50')}>
                    {bTools.map(tool => renderTool(tool))}
                  </div>
                )}
              </div>
            );
          })}
          {bundleGroups.unassigned.length > 0 && (
            <div className="rounded-xl border border-dashed border-border bg-card overflow-hidden">
              <button
                type="button"
                onClick={() => toggleBundle('__unassigned__')}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 hover:bg-muted/40 transition-colors text-left"
              >
                {collapsedBundles.has('__unassigned__') ? <ChevronRight className="size-3.5 text-muted-foreground shrink-0" /> : <ChevronDown className="size-3.5 text-muted-foreground shrink-0" />}
                <span className="text-sm font-semibold flex-1 text-muted-foreground">Other</span>
                <span className="text-[10px] text-muted-foreground tabular-nums shrink-0">{bundleGroups.unassigned.length}</span>
              </button>
              {!collapsedBundles.has('__unassigned__') && (
                <div className={cn('border-t border-border/50',
                  viewMode === 'card' ? 'p-3 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3' : 'divide-y divide-border/50')}>
                  {bundleGroups.unassigned.map(tool => renderTool(tool))}
                </div>
              )}
            </div>
          )}
        </div>
      ) : tools.length > 0 ? viewMode === 'card' ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {tools.map(renderTool)}
        </div>
      ) : (
        /* ── List / Drawer view ── */
        <div className="rounded-xl border border-border bg-card overflow-visible divide-y divide-border/50">
          {tools.map(renderTool)}
        </div>
      ) : (
        <div className="text-center py-16 border border-dashed border-border rounded-xl">
          <Wrench className="mx-auto size-8 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground mb-3">No tools yet</p>
          <Button size="sm" variant="outline" onClick={() => { setShowMcpModal(true); setMcpDiscovered([]); setMcpSelected(new Set()); setMcpProbeError(null); }}>
            <Link2 className="size-3 mr-1.5" /> Import from MCP
          </Button>
        </div>
      )}

      {/* ── Test Dialog ── */}
      <Dialog open={!!testingTool} onOpenChange={(open) => { if (!open) setTestingTool(null); }}>
        <DialogContent className="max-w-xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Play className="size-4" /> Test — {testingTool?.display_name || testingTool?.name}
            </DialogTitle>
            <DialogDescription>
              Provide parameters and run the tool to verify its behavior.
            </DialogDescription>
          </DialogHeader>
          {testingTool && (
            <div className="space-y-4">
              {(() => {
                const { props, required } = getSchemaProperties(testingTool.input_schema);
                return Object.keys(props).length > 0 && (
                  <div>
                    <p className="text-[10px] text-muted-foreground mb-1.5 font-semibold uppercase tracking-wide">Expected Parameters</p>
                    <div className="space-y-1.5">
                      {Object.entries(props).map(([name, prop]) => {
                        const isRequired = required.has(name);
                        return (
                          <div key={name} className={cn(
                            'rounded border px-2.5 py-2 text-xs',
                            isRequired ? 'bg-foreground/5 border-foreground/15' : 'bg-muted/40 border-border/50'
                          )}>
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="font-mono font-semibold text-foreground">{name}</span>
                              {prop.type && (
                                <span className="text-[10px] font-mono text-primary/70 bg-primary/8 border border-primary/15 px-1.5 py-0.5 rounded">
                                  {prop.type}
                                </span>
                              )}
                              <span className={cn(
                                'text-[10px] px-1.5 py-0.5 rounded border ml-auto',
                                isRequired
                                  ? 'text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-900/20 border-orange-200/70 dark:border-orange-800/50'
                                  : 'text-muted-foreground bg-muted border-border/50'
                              )}>
                                {isRequired ? 'required' : 'optional'}
                              </span>
                            </div>
                            {prop.description && (
                              <p className="text-[11px] text-muted-foreground mt-1 leading-relaxed">{prop.description}</p>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}
              <div className="space-y-1.5">
                <Label className="text-xs">Parameters (JSON)</Label>
                <Textarea value={testParamsJson} onChange={(e) => setTestParamsJson(e.target.value)}
                  rows={6} className="font-mono text-sm" placeholder='{"key": "value"}' />
              </div>
              <Button onClick={handleRunTest} disabled={testMutation.isPending} className="w-full gap-2">
                {testMutation.isPending ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
                {testMutation.isPending ? 'Running...' : 'Run Test'}
              </Button>
              {testMutation.data && (
                <div className={cn('rounded-lg border p-3 space-y-2',
                  testMutation.data.success ? 'border-green-200/70 bg-green-50/30 dark:border-green-800/40 dark:bg-green-900/10' : 'border-red-200/70 bg-red-50/30 dark:border-red-800/40 dark:bg-red-900/10')}>
                  <div className="flex items-center gap-2">
                    <span className={cn('size-2 rounded-full', testMutation.data.success ? 'bg-green-500' : 'bg-red-500')} />
                    <span className="text-xs font-semibold">{testMutation.data.success ? 'Success' : 'Failed'}</span>
                    {testMutation.data.execution_time_ms != null && <span className="text-[10px] text-muted-foreground tabular-nums ml-auto">{testMutation.data.execution_time_ms.toFixed(1)}ms</span>}
                  </div>
                  {testMutation.data.message && <p className="text-xs text-foreground/80">{testMutation.data.message}</p>}
                  {testMutation.data.error && <p className="text-xs text-red-600 dark:text-red-400">{testMutation.data.error}</p>}
                  {testMutation.data.data && (
                    <pre className="text-[10px] font-mono bg-background rounded p-2 overflow-x-auto max-h-48 overflow-y-auto border border-border/30">
                      {JSON.stringify(testMutation.data.data, null, 2)}
                    </pre>
                  )}
                </div>
              )}
              {testMutation.error && (
                <div className="rounded-lg border border-red-200/70 bg-red-50/30 p-3">
                  <p className="text-xs text-red-600">Request failed: {testMutation.error instanceof Error ? testMutation.error.message : 'Unknown error'}</p>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* ── MCP Import Dialog ── */}
      <Dialog open={showMcpModal} onOpenChange={(open) => { if (!open) setShowMcpModal(false); }}>
        <DialogContent className="max-w-xl max-h-[90vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <Link2 className="size-4" /> Import Tools from MCP Server
            </DialogTitle>
            <DialogDescription>
              Connect to an MCP server to discover and import available tools.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-1">
            {/* Transport */}
            <div className="space-y-1.5">
              <Label className="text-xs font-medium">Transport</Label>
              <div className="flex gap-2">
                {(['sse', 'stdio'] as MCPTransport[]).map((t) => (
                  <button key={t} type="button"
                    className={cn(
                      'flex-1 py-2 rounded-lg border text-xs font-mono transition-colors',
                      mcpTransport === t
                        ? 'border-primary bg-primary/10 text-primary font-semibold'
                        : 'border-border bg-muted/30 text-muted-foreground hover:bg-muted/60'
                    )}
                    onClick={() => { setMcpTransport(t); setMcpDiscovered([]); setMcpSelected(new Set()); }}>
                    {t === 'sse' ? 'SSE / HTTP' : 'stdio (local process)'}
                  </button>
                ))}
              </div>
            </div>

            {/* SSE */}
            {mcpTransport === 'sse' && (
              <div className="space-y-1.5">
                <Label className="text-xs font-medium">Server URL <span className="text-destructive">*</span></Label>
                <Input value={mcpUrl} onChange={e => setMcpUrl(e.target.value)}
                  placeholder="http://localhost:8000/mcp" className="font-mono text-sm" />
              </div>
            )}

            {/* stdio */}
            {mcpTransport === 'stdio' && (
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <Label className="text-xs font-medium">Command <span className="text-destructive">*</span></Label>
                  <Input value={mcpCommand} onChange={e => setMcpCommand(e.target.value)}
                    placeholder="uvx" className="font-mono text-sm" />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs font-medium flex justify-between">
                    <span>Arguments</span><span className="font-normal text-muted-foreground">one per line</span>
                  </Label>
                  <textarea value={mcpArgsText} onChange={e => setMcpArgsText(e.target.value)}
                    rows={3} placeholder={"mcp-server-github\n--token\nabc123"}
                    className="w-full rounded-md border border-border bg-muted/30 px-3 py-2 text-xs font-mono resize-none focus:outline-none focus:ring-1 focus:ring-ring" />
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs font-medium flex justify-between">
                    <span>Env variables</span><span className="font-normal text-muted-foreground">KEY=VALUE per line</span>
                  </Label>
                  <textarea value={mcpEnvText} onChange={e => setMcpEnvText(e.target.value)}
                    rows={3} placeholder={"GITHUB_TOKEN=ghp_xxx"}
                    className="w-full rounded-md border border-border bg-muted/30 px-3 py-2 text-xs font-mono resize-none focus:outline-none focus:ring-1 focus:ring-ring" />
                </div>
              </div>
            )}

            {/* Probe */}
            <Button size="sm" variant="outline" className="w-full gap-1.5"
              disabled={probeMcpMutation.isPending || (mcpTransport === 'sse' ? !mcpUrl : !mcpCommand)}
              onClick={handleProbeMcp}>
              {probeMcpMutation.isPending
                ? <><Loader2 className="size-3.5 animate-spin" />Connecting…</>
                : <><Search className="size-3.5" />Discover Tools</>}
            </Button>

            {/* Error */}
            {mcpProbeError && (
              <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2.5 text-xs font-mono text-destructive break-all">
                {mcpProbeError}
              </div>
            )}

            {/* Discovered tools list */}
            {mcpDiscovered.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label className="text-xs font-medium">
                    {mcpDiscovered.length} tool{mcpDiscovered.length !== 1 ? 's' : ''} discovered
                  </Label>
                  <div className="flex gap-2">
                    <button type="button" className="text-[10px] text-primary hover:underline"
                      onClick={() => setMcpSelected(new Set(mcpDiscovered.map(t => t.name)))}>
                      Select all
                    </button>
                    <button type="button" className="text-[10px] text-muted-foreground hover:underline"
                      onClick={() => setMcpSelected(new Set())}>
                      Clear
                    </button>
                  </div>
                </div>
                <div className="space-y-1.5 max-h-52 overflow-y-auto pr-1">
                  {mcpDiscovered.map(tool => (
                    <label key={tool.name}
                      className={cn(
                        'flex items-start gap-2.5 rounded-lg border px-3 py-2.5 cursor-pointer transition-colors',
                        mcpSelected.has(tool.name)
                          ? 'border-primary/50 bg-primary/5'
                          : 'border-border bg-muted/20 hover:bg-muted/40'
                      )}>
                      <input type="checkbox" className="mt-0.5 accent-primary"
                        checked={mcpSelected.has(tool.name)}
                        onChange={() => toggleMcpTool(tool.name)} />
                      <div className="min-w-0">
                        <p className="text-xs font-mono font-semibold">{tool.name}</p>
                        {tool.description && (
                          <p className="text-[10px] text-muted-foreground leading-snug mt-0.5 line-clamp-2">{tool.description}</p>
                        )}
                      </div>
                    </label>
                  ))}
                </div>

                {/* Public toggle */}
                <div className="flex items-center gap-2 pt-1">
                  <button type="button"
                    className={cn('relative inline-flex h-5 w-9 items-center rounded-full transition-colors',
                      mcpIsPublic ? 'bg-primary' : 'bg-muted-foreground/30')}
                    onClick={() => setMcpIsPublic(p => !p)}>
                    <span className={cn('inline-block size-3.5 rounded-full bg-white shadow transition-transform',
                      mcpIsPublic ? 'translate-x-[18px]' : 'translate-x-[2px]')} />
                  </button>
                  <Label className="text-xs cursor-pointer select-none">
                    Make imported tools public (visible to all users)
                  </Label>
                </div>
              </div>
            )}
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" size="sm" onClick={() => setShowMcpModal(false)}>Cancel</Button>
            <Button size="sm" disabled={mcpSelected.size === 0 || importFromMcpMutation.isPending}
              onClick={handleImportFromMcp}>
              {importFromMcpMutation.isPending
                ? <><Loader2 className="size-3.5 animate-spin mr-1" />Importing…</>
                : `Import ${mcpSelected.size > 0 ? `${mcpSelected.size} ` : ''}Tool${mcpSelected.size !== 1 ? 's' : ''}`}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* Context Viewer */}
      {contextViewId && (() => {
        const t = (toolData?.tools ?? []).find((x) => x.id === contextViewId);
        return t ? (
          <ContextViewer
            open={!!contextViewId}
            onClose={() => setContextViewId(null)}
            entityType="tool"
            entityId={contextViewId}
            entityName={t.display_name || t.name}
          />
        ) : null;
      })()}
    </div>
  );
}
