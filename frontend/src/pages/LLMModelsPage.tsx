import { useMemo, useState } from 'react';
import {
  Cpu, Plus, Trash2, Pencil, Loader2, Search, CheckCircle2,
  XCircle, Star, Eye, Zap, Layers, Settings2, Globe, MessageSquare
} from 'lucide-react';
import {
  useChatModels, useCreateChatModel, useUpdateChatModel, useDeleteChatModel,
  useEmbeddingModels, useCreateEmbeddingModel, useUpdateEmbeddingModel, useDeleteEmbeddingModel,
} from '@/hooks/useLLMModels';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from '@/components/ui/dialog';
import { cn } from '@/lib/utils';
import { formatRelativeTime } from '@/utils/formatDate';
import type { ChatModel, ChatModelCreate, EmbeddingModel, EmbeddingModelCreate } from '@/types/llm';

// ─── Provider badge colors ────────────────────────────────────────────────────

const PROVIDER_COLOR: Record<string, string> = {
  openai:     'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
  custom:     'bg-muted text-muted-foreground',
};

function ProviderBadge({ provider }: { provider: string }) {
  const cls = PROVIDER_COLOR[provider.toLowerCase()] ?? PROVIDER_COLOR.custom;
  return (
    <span className={cn('text-[10px] font-medium px-1.5 py-0.5 rounded-full border border-transparent', cls)}>
      {provider}
    </span>
  );
}

function EnabledBadge({ enabled }: { enabled: boolean }) {
  return enabled ? (
    <span className="flex items-center gap-0.5 text-[10px] text-green-600 dark:text-green-400">
      <CheckCircle2 className="size-3" /> On
    </span>
  ) : (
    <span className="flex items-center gap-0.5 text-[10px] text-muted-foreground">
      <XCircle className="size-3" /> Off
    </span>
  );
}

// ─── Chat Model Form ──────────────────────────────────────────────────────────

const EMPTY_CHAT: ChatModelCreate = {
  name: '', provider: '', model_id: '', description: null,
  base_url: null, api_key_ref: null,
  supports_vision: false, supports_function_call: true, supports_streaming: true,
  enabled: true, is_default: false,
};

function ChatModelDialog({
  open, onClose, initial,
}: {
  open: boolean;
  onClose: () => void;
  initial?: ChatModel | null;
}) {
  const isEdit = !!initial;
  const [form, setForm] = useState<ChatModelCreate>(() =>
    initial
      ? {
          name: initial.name, provider: initial.provider, model_id: initial.model_id,
          description: initial.description ?? null, base_url: initial.base_url ?? null,
          api_key_ref: initial.api_key_ref ?? null,
          max_tokens: initial.max_tokens ?? null, context_window: initial.context_window ?? null,
          supports_vision: initial.supports_vision, supports_function_call: initial.supports_function_call,
          supports_streaming: initial.supports_streaming,
          default_temperature: initial.default_temperature ?? null,
          input_price: initial.input_price ?? null, output_price: initial.output_price ?? null,
          currency: initial.currency, is_default: initial.is_default, enabled: initial.enabled,
        }
      : { ...EMPTY_CHAT }
  );

  const createMutation = useCreateChatModel();
  const updateMutation = useUpdateChatModel();
  const isPending = createMutation.isPending || updateMutation.isPending;

  const set = (key: keyof ChatModelCreate, value: unknown) =>
    setForm(prev => ({ ...prev, [key]: value }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (isEdit && initial) {
        await updateMutation.mutateAsync({ id: initial.id, data: form });
      } else {
        await createMutation.mutateAsync(form);
      }
      onClose();
    } catch (err) {
      alert(`Save failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Edit Chat Model' : 'Add Chat Model'}</DialogTitle>
        </DialogHeader>
        <form id="chat-model-form" onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1 col-span-2">
              <Label>Name *</Label>
              <Input value={form.name} onChange={e => set('name', e.target.value)} required placeholder="e.g. GPT-4o" />
            </div>
            <div className="space-y-1">
              <Label>Provider *</Label>
              <Input value={form.provider} onChange={e => set('provider', e.target.value)} required placeholder="openai / custom" />
            </div>
            <div className="space-y-1">
              <Label>Model ID *</Label>
              <Input value={form.model_id} onChange={e => set('model_id', e.target.value)} required placeholder="gpt-4.1-mini" />
            </div>
            <div className="space-y-1 col-span-2">
              <Label>Description</Label>
              <Input value={form.description ?? ''} onChange={e => set('description', e.target.value || null)} placeholder="Optional description" />
            </div>
            <div className="space-y-1 col-span-2">
              <Label>Base URL</Label>
              <Input value={form.base_url ?? ''} onChange={e => set('base_url', e.target.value || null)} placeholder="https://api.openai.com/v1" />
            </div>
            <div className="space-y-1 col-span-2">
              <Label>API Key</Label>
              <Input value={form.api_key_ref ?? ''} onChange={e => set('api_key_ref', e.target.value || null)} placeholder="sk-..." type="password" />
            </div>
            <div className="space-y-1">
              <Label>Context Window</Label>
              <Input type="number" min={1} value={form.context_window ?? ''} onChange={e => set('context_window', e.target.value ? Number(e.target.value) : null)} placeholder="128000" />
            </div>
            <div className="space-y-1">
              <Label>Max Output Tokens</Label>
              <Input type="number" min={1} value={form.max_tokens ?? ''} onChange={e => set('max_tokens', e.target.value ? Number(e.target.value) : null)} placeholder="4096" />
            </div>
            <div className="space-y-1">
              <Label>Temperature</Label>
              <Input type="number" min={0} max={2} step={0.1} value={form.default_temperature ?? ''} onChange={e => set('default_temperature', e.target.value ? Number(e.target.value) : null)} placeholder="0.7" />
            </div>
            <div className="space-y-1">
              <Label>Input Price / 1K tokens</Label>
              <Input type="number" min={0} step={0.0001} value={form.input_price ?? ''} onChange={e => set('input_price', e.target.value ? Number(e.target.value) : null)} placeholder="0.005" />
            </div>
            <div className="space-y-1">
              <Label>Output Price / 1K tokens</Label>
              <Input type="number" min={0} step={0.0001} value={form.output_price ?? ''} onChange={e => set('output_price', e.target.value ? Number(e.target.value) : null)} placeholder="0.015" />
            </div>
            <div className="space-y-1">
              <Label>Currency</Label>
              <Input value={form.currency ?? 'USD'} onChange={e => set('currency', e.target.value)} placeholder="USD" maxLength={10} />
            </div>
          </div>

          {/* Capabilities */}
          <div className="space-y-2 pt-1">
            <p className="text-xs font-medium text-muted-foreground">Capabilities</p>
            <div className="grid grid-cols-3 gap-2">
              {(
                [
                  ['supports_vision', 'Vision'],
                  ['supports_function_call', 'Function Call'],
                  ['supports_streaming', 'Streaming'],
                ] as [keyof ChatModelCreate, string][]
              ).map(([key, label]) => (
                <label key={key} className="flex items-center gap-1.5 cursor-pointer text-xs">
                  <input
                    type="checkbox"
                    checked={!!form[key]}
                    onChange={e => set(key, e.target.checked)}
                    className="rounded"
                  />
                  {label}
                </label>
              ))}
            </div>
          </div>

          {/* Status */}
          <div className="grid grid-cols-2 gap-2 pt-1">
            <label className="flex items-center gap-1.5 cursor-pointer text-xs">
              <input type="checkbox" checked={!!form.enabled} onChange={e => set('enabled', e.target.checked)} className="rounded" />
              Enabled
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer text-xs">
              <input type="checkbox" checked={!!form.is_default} onChange={e => set('is_default', e.target.checked)} className="rounded" />
              Set as Default
            </label>
          </div>
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isPending}>Cancel</Button>
          <Button form="chat-model-form" type="submit" disabled={isPending}>
            {isPending && <Loader2 className="size-3.5 animate-spin mr-1" />}
            {isEdit ? 'Save' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Embedding Model Form ─────────────────────────────────────────────────────

const EMPTY_EMBED: EmbeddingModelCreate = {
  name: '', provider: '', model_id: '', dimension: 1536,
  description: null, base_url: null, api_key_ref: null,
  supports_batch: true, batch_size: 32, normalize: true,
  distance_metric: 'cosine', enabled: true, is_default: false,
};

const EMBEDDING_PRESETS = [
  {
    id: 'openai-small',
    label: 'OpenAI text-embedding-3-small',
    values: {
      name: 'OpenAI text-embedding-3-small',
      provider: 'openai',
      model_id: 'text-embedding-3-small',
      dimension: 1536,
      base_url: 'https://api.openai.com/v1',
      max_tokens: 8192,
      currency: 'USD',
    },
  },
] satisfies {
  id: string;
  label: string;
  values: Partial<EmbeddingModelCreate> &
    Pick<EmbeddingModelCreate, 'name' | 'provider' | 'model_id' | 'dimension'>;
}[];

const DEFAULT_EMBEDDING_VALUES = EMBEDDING_PRESETS[0].values;

function createEmbeddingForm(
  defaults?: Partial<EmbeddingModelCreate>
): EmbeddingModelCreate {
  return {
    ...EMPTY_EMBED,
    ...DEFAULT_EMBEDDING_VALUES,
    ...defaults,
  };
}

function EmbeddingModelDialog({
  open, onClose, initial, defaults,
}: {
  open: boolean;
  onClose: () => void;
  initial?: EmbeddingModel | null;
  defaults?: Partial<EmbeddingModelCreate>;
}) {
  const isEdit = !!initial;
  const buildInitialForm = (): EmbeddingModelCreate => {
    if (initial) {
      return {
          name: initial.name, provider: initial.provider, model_id: initial.model_id,
          description: initial.description ?? null, base_url: initial.base_url ?? null,
          api_key_ref: initial.api_key_ref ?? null,
          dimension: initial.dimension, max_tokens: initial.max_tokens ?? null,
          supports_batch: initial.supports_batch, batch_size: initial.batch_size,
          normalize: initial.normalize, distance_metric: initial.distance_metric,
          price: initial.price ?? null, currency: initial.currency,
          is_default: initial.is_default, enabled: initial.enabled,
      };
    }

    return createEmbeddingForm(defaults);
  };

  const [form, setForm] = useState<EmbeddingModelCreate>(buildInitialForm);

  const createMutation = useCreateEmbeddingModel();
  const updateMutation = useUpdateEmbeddingModel();
  const isPending = createMutation.isPending || updateMutation.isPending;

  const selectedPresetId = useMemo(() => {
    const preset = EMBEDDING_PRESETS.find((item) =>
      item.values.provider === form.provider &&
      item.values.model_id === form.model_id &&
      item.values.dimension === form.dimension
    );
    return preset?.id ?? 'custom';
  }, [form.dimension, form.model_id, form.provider]);

  const set = (key: keyof EmbeddingModelCreate, value: unknown) =>
    setForm(prev => ({ ...prev, [key]: value }));

  const applyPreset = (presetId: string) => {
    if (presetId === 'custom') return;
    const preset = EMBEDDING_PRESETS.find((item) => item.id === presetId);
    if (!preset) return;
    setForm((prev) => ({
      ...prev,
      ...preset.values,
      api_key_ref: prev.api_key_ref,
      description: prev.description,
      enabled: prev.enabled,
      is_default: prev.is_default,
    }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      if (isEdit && initial) {
        await updateMutation.mutateAsync({ id: initial.id, data: form });
      } else {
        await createMutation.mutateAsync(form);
      }
      onClose();
    } catch (err) {
      alert(`Save failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? 'Edit Embedding Model' : 'Add Embedding Model'}</DialogTitle>
        </DialogHeader>
        <form id="embed-model-form" onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1 col-span-2">
              <Label>Preset</Label>
              <Select value={selectedPresetId} onValueChange={applyPreset}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EMBEDDING_PRESETS.map((preset) => (
                    <SelectItem key={preset.id} value={preset.id}>
                      {preset.label}
                    </SelectItem>
                  ))}
                  <SelectItem value="custom">Custom</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1 col-span-2">
              <Label>Name *</Label>
              <Input value={form.name} onChange={e => set('name', e.target.value)} required placeholder="e.g. text-embedding-3-small" />
            </div>
            <div className="space-y-1">
              <Label>Provider *</Label>
              <Input value={form.provider} onChange={e => set('provider', e.target.value)} required placeholder="openai / custom" />
            </div>
            <div className="space-y-1">
              <Label>Model ID *</Label>
              <Input value={form.model_id} onChange={e => set('model_id', e.target.value)} required placeholder="text-embedding-3-small" />
            </div>
            <div className="space-y-1 col-span-2">
              <Label>Description</Label>
              <Input value={form.description ?? ''} onChange={e => set('description', e.target.value || null)} placeholder="Optional description" />
            </div>
            <div className="space-y-1 col-span-2">
              <Label>Base URL</Label>
              <Input value={form.base_url ?? ''} onChange={e => set('base_url', e.target.value || null)} placeholder="https://api.openai.com/v1" />
            </div>
            <div className="space-y-1 col-span-2">
              <Label>API Key</Label>
              <Input value={form.api_key_ref ?? ''} onChange={e => set('api_key_ref', e.target.value || null)} placeholder="sk-..." type="password" />
            </div>
            <div className="space-y-1">
              <Label>Dimension *</Label>
              <Input type="number" min={1} value={form.dimension} onChange={e => set('dimension', Number(e.target.value))} required placeholder="1536" />
            </div>
            <div className="space-y-1">
              <Label>Max Input Tokens</Label>
              <Input type="number" min={1} value={form.max_tokens ?? ''} onChange={e => set('max_tokens', e.target.value ? Number(e.target.value) : null)} placeholder="8192" />
            </div>
            <div className="space-y-1">
              <Label>Distance Metric</Label>
              <Input value={form.distance_metric ?? 'cosine'} onChange={e => set('distance_metric', e.target.value)} placeholder="cosine / euclidean / dot_product" />
            </div>
            <div className="space-y-1">
              <Label>Batch Size</Label>
              <Input type="number" min={1} value={form.batch_size ?? 32} onChange={e => set('batch_size', Number(e.target.value))} />
            </div>
            <div className="space-y-1">
              <Label>Price / 1K tokens</Label>
              <Input type="number" min={0} step={0.00001} value={form.price ?? ''} onChange={e => set('price', e.target.value ? Number(e.target.value) : null)} placeholder="0.0001" />
            </div>
            <div className="space-y-1">
              <Label>Currency</Label>
              <Input value={form.currency ?? 'USD'} onChange={e => set('currency', e.target.value)} placeholder="USD" maxLength={10} />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2 pt-1">
            <label className="flex items-center gap-1.5 cursor-pointer text-xs">
              <input type="checkbox" checked={!!form.supports_batch} onChange={e => set('supports_batch', e.target.checked)} className="rounded" />
              Supports Batch
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer text-xs">
              <input type="checkbox" checked={!!form.normalize} onChange={e => set('normalize', e.target.checked)} className="rounded" />
              Normalize Vectors
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer text-xs">
              <input type="checkbox" checked={!!form.enabled} onChange={e => set('enabled', e.target.checked)} className="rounded" />
              Enabled
            </label>
            <label className="flex items-center gap-1.5 cursor-pointer text-xs">
              <input type="checkbox" checked={!!form.is_default} onChange={e => set('is_default', e.target.checked)} className="rounded" />
              Set as Default
            </label>
          </div>
        </form>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={isPending}>Cancel</Button>
          <Button form="embed-model-form" type="submit" disabled={isPending}>
            {isPending && <Loader2 className="size-3.5 animate-spin mr-1" />}
            {isEdit ? 'Save' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Default Model Quick Config ───────────────────────────────────────────────

function GlobalDefaultConfig() {
  const [showEmbeddingDialog, setShowEmbeddingDialog] = useState(false);
  const { data: chatData, isLoading: chatLoading } = useChatModels({ enabled: true });
  const { data: embedData, isLoading: embedLoading } = useEmbeddingModels({ enabled: true });
  
  const updateChat = useUpdateChatModel();
  const updateEmbed = useUpdateEmbeddingModel();

  const chatModels = chatData?.items ?? [];
  const embedModels = embedData?.items ?? [];

  const defaultChat = chatModels.find(m => m.is_default && !m.supports_vision);
  const defaultVision = chatModels.find(m => m.is_default && m.supports_vision);
  const defaultEmbed = embedModels.find(m => m.is_default);

  const defaultEmbeddingDefaults = useMemo<Partial<EmbeddingModelCreate>>(
    () => ({
      ...DEFAULT_EMBEDDING_VALUES,
      is_default: true,
      enabled: true,
    }),
    []
  );

  const handleSetDefaultChat = async (id: string) => {
    const model = chatModels.find(m => m.id === id);
    if (!model) return;
    try {
      await updateChat.mutateAsync({ id, data: { is_default: true } });
    } catch (err) {
      alert(`Failed to set default: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  const handleSetDefaultEmbed = async (id: string) => {
    try {
      await updateEmbed.mutateAsync({ id, data: { is_default: true } });
    } catch (err) {
      alert(`Failed to set default: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  if (chatLoading || embedLoading) return null;

  return (
    <>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 p-4 rounded-xl border border-primary/20 bg-primary/5 mb-6">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <MessageSquare className="size-4 text-primary" />
            <Label className="text-sm font-bold">Default Chat Model</Label>
          </div>
          <Select value={defaultChat?.id} onValueChange={handleSetDefaultChat}>
            <SelectTrigger className="h-9 bg-background">
              <SelectValue placeholder="Select default chat" />
            </SelectTrigger>
            <SelectContent>
              {chatModels.filter(m => !m.supports_vision).map(m => (
                <SelectItem key={m.id} value={m.id}>
                  {m.name} ({m.provider})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-[10px] text-muted-foreground">Global fallback for text-only conversations.</p>
        </div>

        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Eye className="size-4 text-primary" />
            <Label className="text-sm font-bold">Default Multi-modal</Label>
          </div>
          <Select value={defaultVision?.id} onValueChange={handleSetDefaultChat}>
            <SelectTrigger className="h-9 bg-background">
              <SelectValue placeholder="Select default vision" />
            </SelectTrigger>
            <SelectContent>
              {chatModels.filter(m => m.supports_vision).map(m => (
                <SelectItem key={m.id} value={m.id}>
                  {m.name} ({m.provider})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-[10px] text-muted-foreground">Used when images or files are provided.</p>
        </div>

        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Globe className="size-4 text-primary" />
              <Label className="text-sm font-bold">Default Embedding</Label>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-7 px-2 gap-1 text-xs bg-background"
              onClick={() => setShowEmbeddingDialog(true)}
            >
              <Plus className="size-3" />
              Configure
            </Button>
          </div>
          {embedModels.length > 0 ? (
            <Select value={defaultEmbed?.id} onValueChange={handleSetDefaultEmbed}>
              <SelectTrigger className="h-9 bg-background">
                <SelectValue placeholder="Select default embedding" />
              </SelectTrigger>
              <SelectContent>
                {embedModels.map(m => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.name} ({m.provider})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <Button
              type="button"
              variant="outline"
              className="h-9 w-full justify-start bg-background text-muted-foreground"
              onClick={() => setShowEmbeddingDialog(true)}
            >
              <Plus className="size-3.5 mr-1" />
              Add default embedding
            </Button>
          )}
          <p className="text-[10px] text-muted-foreground">Global fallback for knowledge base indexing.</p>
        </div>
      </div>
      {showEmbeddingDialog && (
        <EmbeddingModelDialog
          open={showEmbeddingDialog}
          onClose={() => setShowEmbeddingDialog(false)}
          defaults={defaultEmbeddingDefaults}
        />
      )}
    </>
  );
}

// ─── Chat Models Table ────────────────────────────────────────────────────────

function ChatModelsTab() {
  const [search, setSearch] = useState('');
  const [showDialog, setShowDialog] = useState(false);
  const [editTarget, setEditTarget] = useState<ChatModel | null>(null);

  const { data, isLoading } = useChatModels({ page: 1, page_size: 50 });
  const deleteMutation = useDeleteChatModel();
  const updateMutation = useUpdateChatModel();

  const chatModels = useMemo(() => data?.items ?? [], [data?.items]);
  const filtered = useMemo(() => chatModels.filter(m =>
    !search || m.name.toLowerCase().includes(search.toLowerCase()) ||
    m.model_id.toLowerCase().includes(search.toLowerCase()) ||
    m.provider.toLowerCase().includes(search.toLowerCase())
  ), [chatModels, search]);

  const handleDelete = async (m: ChatModel) => {
    if (!confirm(`Delete model "${m.name}"?`)) return;
    try {
      await deleteMutation.mutateAsync(m.id);
    } catch (err) {
      alert(`Delete failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  const handleToggleDefault = async (m: ChatModel) => {
    try {
      await updateMutation.mutateAsync({ id: m.id, data: { is_default: !m.is_default } });
    } catch (err) {
      alert(`Update failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  const openEdit = (m: ChatModel) => { setEditTarget(m); setShowDialog(true); };
  const closeDialog = () => { setShowDialog(false); setEditTarget(null); };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
          <Input
            className="pl-8 h-8 text-xs"
            aria-label="Search chat models"
            name="chat-model-search"
            autoComplete="off"
            placeholder="Search models…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <Button size="sm" className="gap-1.5 h-8" onClick={() => { setEditTarget(null); setShowDialog(true); }}>
          <Plus className="size-3.5" />
          Add Model
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 border border-dashed border-border rounded-xl">
          <Cpu className="mx-auto size-8 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground mb-3">No chat models yet</p>
          <Button size="sm" onClick={() => { setEditTarget(null); setShowDialog(true); }}>Add First Model</Button>
        </div>
      ) : (
        <div className="rounded-lg border border-border overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-muted/50 border-b border-border">
              <tr>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Name</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Provider</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Model ID</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Capabilities</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Price (in/out)</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Status</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Created</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.map(m => (
                <tr key={m.id} className="hover:bg-muted/30 transition-colors">
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => handleToggleDefault(m)}
                        aria-label={m.is_default ? `${m.name} is the default chat model` : `Set ${m.name} as default chat model`}
                        title={m.is_default ? 'Default chat model' : 'Set as default chat model'}
                        className={cn("transition-colors", m.is_default ? "text-yellow-500 hover:text-yellow-600" : "text-muted-foreground/20 hover:text-yellow-500/50")}
                      >
                        <Star className={cn("size-3.5", m.is_default && "fill-yellow-500")} />
                      </button>
                      <span className="font-medium truncate max-w-[140px]">{m.name}</span>
                    </div>
                    {m.description && (
                      <p className="text-[10px] text-muted-foreground truncate max-w-[140px]">{m.description}</p>
                    )}
                  </td>
                  <td className="px-3 py-2.5"><ProviderBadge provider={m.provider} /></td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-muted-foreground">{m.model_id}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1">
                      {m.supports_vision && <span title="Vision"><Eye className="size-3 text-blue-500" /></span>}
                      {m.supports_function_call && <span title="Function Call"><Zap className="size-3 text-yellow-500" /></span>}
                      {m.supports_streaming && <span title="Streaming"><Layers className="size-3 text-green-500" /></span>}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 tabular-nums text-muted-foreground">
                    {m.input_price != null && m.output_price != null
                      ? `${m.input_price} / ${m.output_price} ${m.currency}`
                      : <span className="text-muted-foreground/40">—</span>}
                  </td>
                  <td className="px-3 py-2.5"><EnabledBadge enabled={m.enabled} /></td>
                  <td className="px-3 py-2.5 text-muted-foreground whitespace-nowrap">{formatRelativeTime(m.created_at)}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1 justify-end">
                      <Button variant="ghost" size="icon" className="size-6" onClick={() => openEdit(m)} title="Edit">
                        <Pencil className="size-3" />
                      </Button>
                      {!m.is_system && (
                        <Button
                          variant="ghost" size="icon" className="size-6 text-destructive hover:text-destructive hover:bg-destructive/10"
                          onClick={() => handleDelete(m)}
                          disabled={deleteMutation.isPending}
                          title="Delete"
                        >
                          <Trash2 className="size-3" />
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ChatModelDialog open={showDialog} onClose={closeDialog} initial={editTarget} />
    </div>
  );
}

// ─── Embedding Models Table ───────────────────────────────────────────────────

function EmbeddingModelsTab() {
  const [search, setSearch] = useState('');
  const [showDialog, setShowDialog] = useState(false);
  const [editTarget, setEditTarget] = useState<EmbeddingModel | null>(null);

  const { data, isLoading } = useEmbeddingModels({ page: 1, page_size: 50 });
  const deleteMutation = useDeleteEmbeddingModel();
  const updateMutation = useUpdateEmbeddingModel();

  const embedModels = useMemo(() => data?.items ?? [], [data?.items]);
  const createDefaults = useMemo<Partial<EmbeddingModelCreate>>(
    () => ({ is_default: embedModels.length === 0 }),
    [embedModels.length]
  );
  const filtered = useMemo(() => embedModels.filter(m =>
    !search || m.name.toLowerCase().includes(search.toLowerCase()) ||
    m.model_id.toLowerCase().includes(search.toLowerCase()) ||
    m.provider.toLowerCase().includes(search.toLowerCase())
  ), [embedModels, search]);

  const handleDelete = async (m: EmbeddingModel) => {
    if (!confirm(`Delete model "${m.name}"?`)) return;
    try {
      await deleteMutation.mutateAsync(m.id);
    } catch (err) {
      alert(`Delete failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  const handleToggleDefault = async (m: EmbeddingModel) => {
    if (m.is_default) return;
    try {
      await updateMutation.mutateAsync({ id: m.id, data: { is_default: true, enabled: true } });
    } catch (err) {
      alert(`Update failed: ${err instanceof Error ? err.message : 'Unknown error'}`);
    }
  };

  const openEdit = (m: EmbeddingModel) => { setEditTarget(m); setShowDialog(true); };
  const closeDialog = () => { setShowDialog(false); setEditTarget(null); };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
          <Input
            className="pl-8 h-8 text-xs"
            aria-label="Search embedding models"
            name="embedding-model-search"
            autoComplete="off"
            placeholder="Search models…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <Button size="sm" className="gap-1.5 h-8" onClick={() => { setEditTarget(null); setShowDialog(true); }}>
          <Plus className="size-3.5" />
          Add Model
        </Button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-16">
          <Loader2 className="size-5 animate-spin text-muted-foreground" />
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-16 border border-dashed border-border rounded-xl">
          <Cpu className="mx-auto size-8 text-muted-foreground/30 mb-3" />
          <p className="text-sm text-muted-foreground mb-3">No embedding models yet</p>
          <Button size="sm" onClick={() => { setEditTarget(null); setShowDialog(true); }}>Add First Model</Button>
        </div>
      ) : (
        <div className="rounded-lg border border-border overflow-hidden">
          <table className="w-full text-xs">
            <thead className="bg-muted/50 border-b border-border">
              <tr>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Name</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Provider</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Model ID</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Dimension</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Distance</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Price / 1K</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Status</th>
                <th className="text-left px-3 py-2 font-medium text-muted-foreground">Created</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {filtered.map(m => (
                <tr key={m.id} className="hover:bg-muted/30 transition-colors">
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1.5">
                      <button
                        type="button"
                        onClick={() => handleToggleDefault(m)}
                        aria-label={m.is_default ? `${m.name} is the default embedding model` : `Set ${m.name} as default embedding model`}
                        title={m.is_default ? 'Default embedding model' : 'Set as default embedding model'}
                        className={cn("transition-colors", m.is_default ? "text-yellow-500 hover:text-yellow-600" : "text-muted-foreground/20 hover:text-yellow-500/50")}
                      >
                        <Star className={cn("size-3.5", m.is_default && "fill-yellow-500")} />
                      </button>
                      <span className="font-medium truncate max-w-[140px]">{m.name}</span>
                    </div>
                    {m.description && (
                      <p className="text-[10px] text-muted-foreground truncate max-w-[140px]">{m.description}</p>
                    )}
                  </td>
                  <td className="px-3 py-2.5"><ProviderBadge provider={m.provider} /></td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-muted-foreground">{m.model_id}</td>
                  <td className="px-3 py-2.5 tabular-nums">{m.dimension}</td>
                  <td className="px-3 py-2.5 text-muted-foreground">{m.distance_metric}</td>
                  <td className="px-3 py-2.5 tabular-nums text-muted-foreground">
                    {m.price != null
                      ? `${m.price} ${m.currency}`
                      : <span className="text-muted-foreground/40">—</span>}
                  </td>
                  <td className="px-3 py-2.5"><EnabledBadge enabled={m.enabled} /></td>
                  <td className="px-3 py-2.5 text-muted-foreground whitespace-nowrap">{formatRelativeTime(m.created_at)}</td>
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-1 justify-end">
                      <Button variant="ghost" size="icon" className="size-6" onClick={() => openEdit(m)} title="Edit">
                        <Pencil className="size-3" />
                      </Button>
                      {!m.is_system && (
                        <Button
                          variant="ghost" size="icon" className="size-6 text-destructive hover:text-destructive hover:bg-destructive/10"
                          onClick={() => handleDelete(m)}
                          disabled={deleteMutation.isPending}
                          title="Delete"
                        >
                          <Trash2 className="size-3" />
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showDialog && (
        <EmbeddingModelDialog
          open={showDialog}
          onClose={closeDialog}
          initial={editTarget}
          defaults={createDefaults}
        />
      )}
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function LLMModelsPage() {
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 mb-2">
        <Settings2 className="size-4 text-primary" />
        <h3 className="text-sm font-bold uppercase tracking-tight text-foreground/80">Global Model Defaults</h3>
      </div>
      
      <GlobalDefaultConfig />

      <Tabs defaultValue="chat">
        <TabsList className="h-7 mb-4">
          <TabsTrigger value="chat" className="text-xs px-3 gap-1.5">
            <Cpu className="size-3" />Chat Models
          </TabsTrigger>
          <TabsTrigger value="embedding" className="text-xs px-3 gap-1.5">
            <Layers className="size-3" />Embedding Models
          </TabsTrigger>
        </TabsList>
        <TabsContent value="chat"><ChatModelsTab /></TabsContent>
        <TabsContent value="embedding"><EmbeddingModelsTab /></TabsContent>
      </Tabs>
    </div>
  );
}
