import { useCallback, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowLeft, Loader2, Download, Eye, Trash2,
  FolderOpen, Folder, FileText, ChevronDown, ChevronRight,
  Table2, Code2, FileCode, Rows3, Wand2,
  BookOpen, Book, AlignLeft, Search, ChevronsDownUp, ChevronsUpDown,
} from 'lucide-react';
import { useKnowledge } from '@/hooks/useKnowledge';
import { useDeleteDocument, useDocumentList, useUploadDocument } from '@/hooks/useDocuments';
import { useChunksByDocument } from '@/hooks/useChunks';
import { documentService, detectStructureType } from '@/services/documentService';
import type { StructureType } from '@/services/documentService';
import { API_BASE_URL, API_ENDPOINTS } from '@/constants/api';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import type { Document } from '@/types/document';
import type { Chunk } from '@/types/chunk';

// ─── Types ────────────────────────────────────────────────────────────────────

type UploadMode = StructureType | 'auto';

type UploadingFile = {
  file: File;
  taskId: string | null;
  status: 'uploading' | 'processing' | 'success' | 'error' | 'duplicate';
  detectedType: StructureType;
  error?: string;
};

type ViewMode = 'documents' | 'sections' | 'preview';

interface SectionNode {
  section: Chunk;
  children: SectionNode[];
}

// ─── Tree builder ─────────────────────────────────────────────────────────────

function buildSectionTree(sections: Chunk[]): SectionNode[] {
  const roots: SectionNode[] = [];
  // Stack tracks ancestors: each entry is { node, level }
  const stack: { node: SectionNode; level: number }[] = [];

  for (const section of sections) {
    const level = section.meta?.section_level ?? 1;
    const node: SectionNode = { section, children: [] };

    // Pop stack entries that are same level or deeper
    while (stack.length > 0 && stack[stack.length - 1].level >= level) {
      stack.pop();
    }

    if (stack.length === 0) {
      roots.push(node);
    } else {
      stack[stack.length - 1].node.children.push(node);
    }
    stack.push({ node, level });
  }

  return roots;
}

// ─── Lang badge ───────────────────────────────────────────────────────────────

const LANG_BADGE: Record<string, string> = {
  python:     'bg-blue-500/15 text-blue-600',
  typescript: 'bg-sky-500/15 text-sky-600',
  javascript: 'bg-yellow-500/15 text-yellow-700',
  java:       'bg-orange-500/15 text-orange-600',
  go:         'bg-cyan-500/15 text-cyan-700',
  sql:        'bg-rose-500/15 text-rose-600',
};

function langBadgeCls(lang: string) {
  return LANG_BADGE[lang.toLowerCase()] ?? 'bg-muted text-muted-foreground';
}

// ─── Node icon ────────────────────────────────────────────────────────────────

function NodeIcon({ node, open }: { node: SectionNode; open: boolean }) {
  const st = node.section.meta?.structure_type ?? 'document';
  const hasChildren = node.children.length > 0;
  const level = node.section.meta?.section_level ?? 1;

  if (st === 'code') {
    return hasChildren
      ? <FileCode className="size-[15px] text-violet-500 shrink-0" />
      : <Code2 className="size-[15px] text-violet-400 shrink-0" />;
  }
  if (st === 'table') {
    return <Table2 className="size-[15px] text-emerald-500 shrink-0" />;
  }

  // document
  if (!hasChildren) return <AlignLeft className="size-[15px] text-muted-foreground/70 shrink-0" />;
  if (level === 1) {
    return open
      ? <BookOpen className="size-[15px] text-primary shrink-0" />
      : <Book className="size-[15px] text-primary/80 shrink-0" />;
  }
  return open
    ? <FolderOpen className="size-[15px] text-amber-500 shrink-0" />
    : <Folder className="size-[15px] text-amber-400 shrink-0" />;
}

// ─── Recursive tree node ─────────────────────────────────────────────────────

interface SectionTreeNodeProps {
  node: SectionNode;
  depth?: number;
  expandAll?: boolean; // undefined = user-controlled; true/false = forced
}

function SectionTreeNode({ node, depth = 0, expandAll }: SectionTreeNodeProps) {
  const defaultOpen = depth < 2;
  const [open, setOpen] = useState(defaultOpen);
  const [contentOpen, setContentOpen] = useState(false);

  // Sync with expand-all toggle
  const prevExpandAll = useRef<boolean | undefined>(undefined);
  if (expandAll !== undefined && expandAll !== prevExpandAll.current) {
    prevExpandAll.current = expandAll;
    if (open !== expandAll) setOpen(expandAll);
  }

  const st = node.section.meta?.structure_type ?? 'document';
  const level = node.section.meta?.section_level ?? 1;
  const title = node.section.meta?.section_title || node.section.content.slice(0, 80);
  const hasChildren = node.children.length > 0;
  const hasContent = node.section.content.trim().length > 0;
  const langTag = st === 'code' ? node.section.meta?.code_language : undefined;
  const rowIdx = st === 'table' ? node.section.meta?.row_index : undefined;

  return (
    <div>
      {/* Row */}
      <div
        className="flex items-center gap-1.5 py-1 px-1.5 rounded hover:bg-muted/60 cursor-pointer group select-none min-h-[28px]"
        style={{ paddingLeft: `${6 + depth * 18}px` }}
        onClick={() => hasChildren ? setOpen(v => !v) : setContentOpen(v => !v)}
      >
        {/* Expand chevron */}
        <span className="w-4 shrink-0 flex items-center justify-center text-muted-foreground/60">
          {hasChildren
            ? (open
                ? <ChevronDown className="size-3.5" />
                : <ChevronRight className="size-3.5" />)
            : <span className="size-3.5" />}
        </span>

        {/* Icon */}
        <NodeIcon node={node} open={open} />

        {/* Lang badge */}
        {langTag && (
          <span className={cn('text-[10px] font-mono font-bold px-1 py-0.5 rounded leading-none shrink-0', langBadgeCls(langTag))}>
            {langTag}
          </span>
        )}

        {/* Row index */}
        {rowIdx !== undefined && (
          <span className="text-[10px] font-mono bg-muted text-muted-foreground px-1 py-0.5 rounded leading-none shrink-0">
            #{rowIdx + 1}
          </span>
        )}

        {/* Level chip for deep document sections */}
        {st === 'document' && level > 2 && (
          <span className="text-[10px] text-muted-foreground/60 font-mono shrink-0">
            H{level}
          </span>
        )}

        {/* Title */}
        <span className="flex-1 text-sm text-foreground truncate leading-snug" title={title}>
          {title || <span className="text-muted-foreground italic">Untitled</span>}
        </span>

        {/* Char count toggle */}
        {hasContent && (
          <button
            type="button"
            className="shrink-0 text-[11px] text-muted-foreground/50 opacity-0 group-hover:opacity-100 hover:text-primary transition-opacity px-1"
            onClick={e => { e.stopPropagation(); setContentOpen(v => !v); }}
          >
            {contentOpen ? 'hide' : `${node.section.content.length}c`}
          </button>
        )}
      </div>

      {/* Inline content panel */}
      {contentOpen && hasContent && (
        <div
          className={cn(
            'mb-1.5 p-3 rounded text-xs leading-relaxed whitespace-pre-wrap break-words border border-border/50',
            st === 'code' ? 'bg-zinc-950 text-zinc-200 font-mono' : 'bg-muted/30 text-foreground'
          )}
          style={{ marginLeft: `${6 + depth * 18 + 38}px`, marginRight: '8px' }}
        >
          {node.section.content}
        </div>
      )}

      {/* Children — with guide line */}
      {open && hasChildren && (
        <div
          className="border-l border-border/40"
          style={{ marginLeft: `${6 + depth * 18 + 14}px` }}
        >
          {node.children.map(child => (
            <SectionTreeNode key={child.section.id} node={child} depth={depth + 1} expandAll={expandAll} />
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Sections / directory view ────────────────────────────────────────────────

function SectionsView({ document, onBack }: { document: Document; onBack: () => void }) {
  const { data: sectionsData, isLoading } = useChunksByDocument(document.id, {
    page: 1,
    page_size: 1000,
  });

  const [search, setSearch] = useState('');
  // undefined = each node controls itself; true/false = global override
  const [expandAll, setExpandAll] = useState<boolean | undefined>(undefined);
  const expandKey = useRef(0);

  const allSections = sectionsData?.items ?? [];

  // Filter: keep sections whose title or content matches the query
  const filtered = useMemo(() => {
    if (!search.trim()) return allSections;
    const q = search.toLowerCase();
    return allSections.filter(s =>
      (s.meta?.section_title ?? '').toLowerCase().includes(q) ||
      s.content.toLowerCase().includes(q)
    );
  }, [allSections, search]);

  const tree = useMemo(() => buildSectionTree(filtered), [filtered]);

  const dominantType = allSections[0]?.meta?.structure_type ?? 'document';

  function toggleExpandAll(val: boolean) {
    expandKey.current += 1;
    setExpandAll(val);
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center gap-3 mb-5">
        <Button variant="ghost" size="icon" onClick={onBack}>
          <ArrowLeft className="size-5" />
        </Button>
        <div className="flex-1 min-w-0">
          <h3 className="text-lg font-semibold truncate">{document.original_name}</h3>
          <p className="text-sm text-muted-foreground">
            {allSections.length} sections
            {dominantType !== 'document' && (
              <span className="ml-2 capitalize text-xs font-medium px-1.5 py-0.5 rounded bg-muted">
                {dominantType}
              </span>
            )}
          </p>
        </div>

        {/* Toolbar */}
        <div className="flex items-center gap-2 shrink-0">
          <Button
            variant="ghost" size="sm"
            className="text-xs gap-1 h-7 px-2"
            onClick={() => toggleExpandAll(true)}
            title="Expand all"
          >
            <ChevronsUpDown className="size-3.5" />
            <span className="hidden sm:inline">Expand</span>
          </Button>
          <Button
            variant="ghost" size="sm"
            className="text-xs gap-1 h-7 px-2"
            onClick={() => toggleExpandAll(false)}
            title="Collapse all"
          >
            <ChevronsDownUp className="size-3.5" />
            <span className="hidden sm:inline">Collapse</span>
          </Button>
        </div>
      </div>

      {/* Search */}
      <div className="relative mb-4">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none" />
        <input
          type="text"
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search sections…"
          className="w-full pl-9 pr-3 py-2 text-sm bg-muted/40 border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-primary/30 placeholder:text-muted-foreground/60"
        />
        {search && (
          <button
            type="button"
            className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground text-xs"
            onClick={() => setSearch('')}
          >
            ✕
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="text-center py-8">
          <Loader2 className="size-6 animate-spin text-muted-foreground mx-auto" />
        </div>
      ) : tree.length > 0 ? (
        <div className="bg-card rounded-lg border border-border py-2 px-1">
          {tree.map(node => (
            <SectionTreeNode
              key={`${node.section.id}-${expandKey.current}`}
              node={node}
              depth={0}
              expandAll={expandAll}
            />
          ))}
          {search && (
            <p className="text-xs text-muted-foreground text-center py-2">
              {filtered.length} of {allSections.length} sections match
            </p>
          )}
        </div>
      ) : (
        <div className="text-center py-12 bg-card rounded-lg border border-border">
          {search
            ? <p className="text-muted-foreground">No sections match "{search}"</p>
            : <>
                <h3 className="text-lg font-medium mb-1">No Sections Yet</h3>
                <p className="text-muted-foreground">This document has no sections</p>
              </>}
        </div>
      )}
    </div>
  );
}

// ─── Preview view ─────────────────────────────────────────────────────────────

function PreviewView({ document, onBack }: { document: Document; onBack: () => void }) {
  const { data: previewData, isLoading, error } = useQuery({
    queryKey: ['document', 'preview', document.id],
    queryFn: () => documentService.getPreview(document.id),
  });

  return (
    <div>
      <div className="flex items-center gap-4 mb-6">
        <Button variant="ghost" size="icon" onClick={onBack}>
          <ArrowLeft className="size-5" />
        </Button>
        <div>
          <h3 className="text-lg font-semibold">{document.original_name}</h3>
          <p className="text-sm text-muted-foreground">{previewData?.content_length ?? 0} characters</p>
        </div>
      </div>

      {isLoading ? (
        <div className="text-center py-8">
          <Loader2 className="size-6 animate-spin text-muted-foreground mx-auto" />
        </div>
      ) : error ? (
        <div className="text-center py-12 bg-card rounded-lg border border-border">
          <p className="text-destructive mb-2">Failed to load preview</p>
          <p className="text-sm text-muted-foreground">
            {error instanceof Error ? error.message : 'Unknown error'}
          </p>
        </div>
      ) : previewData?.content ? (
        <div className="bg-card rounded-lg border border-border p-6">
          <pre className="whitespace-pre-wrap text-sm text-foreground font-sans">{previewData.content}</pre>
        </div>
      ) : (
        <div className="text-center py-12 bg-card rounded-lg border border-border">
          <h3 className="text-lg font-medium mb-1">No Preview Available</h3>
          <p className="text-muted-foreground">This document has no preview content</p>
        </div>
      )}
    </div>
  );
}

// ─── Structure type selector ──────────────────────────────────────────────────

const STRUCTURE_OPTIONS: { value: UploadMode; label: string; desc: string; icon: React.ReactNode }[] = [
  {
    value: 'auto',
    label: 'Auto',
    desc: 'Detected from file type',
    icon: <Wand2 className="size-4" />,
  },
  {
    value: 'document',
    label: 'Document',
    desc: 'Headings & paragraphs',
    icon: <FileText className="size-4" />,
  },
  {
    value: 'table',
    label: 'Table',
    desc: 'Rows & columns',
    icon: <Table2 className="size-4" />,
  },
  {
    value: 'code',
    label: 'Code',
    desc: 'Functions & classes',
    icon: <Code2 className="size-4" />,
  },
];

const DETECTED_TYPE_LABEL: Record<StructureType, string> = {
  document: 'Document',
  table: 'Table',
  code: 'Code',
};

function StructureTypeSelector({
  value,
  onChange,
}: {
  value: UploadMode;
  onChange: (v: UploadMode) => void;
}) {
  return (
    <div className="flex gap-2 flex-wrap">
      {STRUCTURE_OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm border transition-colors',
            value === opt.value
              ? 'border-primary bg-primary/10 text-primary font-medium'
              : 'border-border text-muted-foreground hover:border-primary/50 hover:text-foreground'
          )}
        >
          {opt.icon}
          <span>{opt.label}</span>
          <span className="hidden sm:inline text-xs text-muted-foreground">— {opt.desc}</span>
        </button>
      ))}
    </div>
  );
}

// ─── Status helpers ───────────────────────────────────────────────────────────

const docStatusVariant = (s: string): 'default' | 'secondary' | 'destructive' | 'outline' => {
  if (s === 'completed') return 'default';
  if (['processing', 'downloading', 'parsing', 'structuring', 'structured', 'embedding'].includes(s))
    return 'secondary';
  if (s === 'failed') return 'destructive';
  return 'outline';
};

const IN_PROGRESS_STATUSES = new Set([
  'processing', 'downloading', 'parsing', 'structuring', 'structured', 'embedding',
]);

// ─── Main page ────────────────────────────────────────────────────────────────

export default function DocumentPage() {
  const { knowledgeId } = useParams<{ knowledgeId: string }>();
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [uploadingFiles, setUploadingFiles] = useState<UploadingFile[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [structureType, setStructureType] = useState<UploadMode>('auto');
  const [viewMode, setViewMode] = useState<ViewMode>('documents');
  const [selectedDocument, setSelectedDocument] = useState<Document | null>(null);

  const { data: knowledge, isLoading: knowledgeLoading } = useKnowledge(knowledgeId || '');
  const { data: documentsData, isLoading: documentsLoading } = useDocumentList(knowledgeId || '');
  const uploadMutation = useUploadDocument();
  const deleteMutation = useDeleteDocument(knowledgeId || '');

  const handleFileUpload = useCallback(async (files: FileList | null) => {
    if (!files || !knowledgeId) return;
    for (const file of Array.from(files)) {
      const effectiveType: StructureType =
        structureType === 'auto' ? detectStructureType(file) : structureType;
      const uploadingFile: UploadingFile = {
        file, taskId: null, status: 'uploading', detectedType: effectiveType,
      };
      setUploadingFiles((prev) => [...prev, uploadingFile]);
      try {
        const result = await uploadMutation.mutateAsync({
          file,
          knowledge_id: knowledgeId,
          structure_type: effectiveType,
        });
        setUploadingFiles((prev) =>
          prev.map((f) => f.file === file
            ? { ...f, taskId: result.task_id, status: result.task_id === 'duplicate' ? 'duplicate' : 'success' }
            : f)
        );
        setTimeout(() => setUploadingFiles((prev) => prev.filter((f) => f.file !== file)), 3000);
      } catch (error) {
        setUploadingFiles((prev) =>
          prev.map((f) => f.file === file
            ? { ...f, status: 'error', error: error instanceof Error ? error.message : 'Upload failed' }
            : f)
        );
      }
    }
  }, [knowledgeId, uploadMutation, structureType]);

  const handleDragOver = useCallback((e: React.DragEvent) => { e.preventDefault(); setDragOver(true); }, []);
  const handleDragLeave = useCallback((e: React.DragEvent) => { e.preventDefault(); setDragOver(false); }, []);
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDragOver(false); handleFileUpload(e.dataTransfer.files);
  }, [handleFileUpload]);

  const handleDelete = async (doc: Document, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`Are you sure you want to delete "${doc.original_name}"?`)) return;
    try { await deleteMutation.mutateAsync(doc.id); }
    catch (error) { alert(`Delete failed: ${error instanceof Error ? error.message : 'Unknown error'}`); }
  };

  const handleDownload = async (doc: Document, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch(`${API_BASE_URL}${API_ENDPOINTS.DOCUMENT.DOWNLOAD(doc.id)}`, {
        headers: { 'Authorization': `Bearer ${token}` },
      });
      if (!response.ok) throw new Error('Download failed');
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const a = window.document.createElement('a');
      a.href = url; a.download = doc.original_name;
      window.document.body.appendChild(a); a.click();
      window.URL.revokeObjectURL(url); window.document.body.removeChild(a);
    } catch (error) {
      alert(`Download failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const getFileIcon = (mimeType?: string): string => {
    if (!mimeType) return 'file';
    if (mimeType.includes('pdf')) return 'pdf';
    if (mimeType.includes('word') || mimeType.includes('document')) return 'doc';
    if (mimeType.includes('text')) return 'txt';
    return 'file';
  };

  if (knowledgeLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <Loader2 className="size-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!knowledge) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="text-center">
          <h2 className="text-xl font-bold mb-2">Knowledge Base Not Found</h2>
          <Button variant="link" onClick={() => navigate('/home')}>Go back to Home</Button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-muted/30">
      {/* Header */}
      <div className="bg-card border-b border-border">
        <div className="max-w-6xl mx-auto px-6 py-4">
          <div className="flex items-center gap-4">
            <Button variant="ghost" size="icon" onClick={() => navigate('/home')}>
              <ArrowLeft className="size-6" />
            </Button>
            <div>
              <h1 className="text-xl font-bold">{knowledge.name}</h1>
              <p className="text-sm text-muted-foreground">
                {knowledge.document_count} documents · {knowledge.chunk_count} sections
              </p>
            </div>
          </div>
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-6 py-8">
        {viewMode === 'documents' && (
          <>
            {/* Upload Area */}
            <div className="mb-8 bg-card rounded-lg border border-border overflow-hidden">
              {/* Structure type selector bar */}
              <div className="px-5 pt-4 pb-3 border-b border-border">
                <p className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wide">
                  Structuring strategy
                </p>
                <StructureTypeSelector value={structureType} onChange={setStructureType} />
              </div>

              {/* Drop zone */}
              <div
                className={cn(
                  'p-8 text-center transition-colors',
                  dragOver ? 'bg-primary/5' : 'hover:bg-muted/20'
                )}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  className="hidden"
                  multiple
                  accept=".pdf,.doc,.docx,.txt,.md,.html,.csv"
                  onChange={(e) => handleFileUpload(e.target.files)}
                />
                <p className="text-foreground mb-2">
                  Drag and drop files here, or{' '}
                  <button
                    type="button"
                    onClick={() => fileInputRef.current?.click()}
                    className="text-primary hover:underline font-medium"
                  >
                    browse
                  </button>
                </p>
                <p className="text-sm text-muted-foreground">
                  PDF, DOCX, TXT, MD, HTML, CSV
                  {structureType === 'auto' && ' · Structurer is detected automatically from the file type'}
                  {structureType === 'table' && ' · Rows & columns extraction'}
                  {structureType === 'code' && ' · Functions & classes extraction'}
                </p>
              </div>
            </div>

            {/* Uploading progress */}
            {uploadingFiles.length > 0 && (
              <div className="mb-8 space-y-2">
                <h3 className="text-sm font-medium mb-2">Uploading</h3>
                {uploadingFiles.map((uploadingFile, index) => (
                  <div key={index} className="bg-card rounded-lg border border-border p-4 flex items-center gap-4">
                    <div className="flex-1">
                      <p className="text-sm font-medium">{uploadingFile.file.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {formatFileSize(uploadingFile.file.size)}
                        <span className="ml-2 capitalize font-medium text-primary/80">
                          {DETECTED_TYPE_LABEL[uploadingFile.detectedType]}
                        </span>
                      </p>
                    </div>
                    <div>
                      {uploadingFile.status === 'uploading' && (
                        <span className="inline-flex items-center gap-2 text-sm text-primary">
                          <Loader2 className="size-4 animate-spin" />Uploading…
                        </span>
                      )}
                      {uploadingFile.status === 'processing' && (
                        <span className="inline-flex items-center gap-2 text-sm text-amber-500">
                          <Loader2 className="size-4 animate-spin" />Processing…
                        </span>
                      )}
                      {uploadingFile.status === 'success' && <Badge variant="default">Uploaded</Badge>}
                      {uploadingFile.status === 'duplicate' && <Badge variant="outline">Duplicate</Badge>}
                      {uploadingFile.status === 'error' && (
                        <Badge variant="destructive">{uploadingFile.error || 'Error'}</Badge>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Documents list */}
            <div>
              <h3 className="text-lg font-semibold mb-4">Documents</h3>
              {documentsLoading ? (
                <div className="text-center py-8">
                  <Loader2 className="size-6 animate-spin text-muted-foreground mx-auto" />
                </div>
              ) : documentsData?.items && documentsData.items.length > 0 ? (
                <div className="space-y-2">
                  {documentsData.items.map((doc) => (
                    <div
                      key={doc.id}
                      className={cn(
                        'bg-card rounded-lg border border-border overflow-hidden',
                        doc.status === 'completed' && doc.chunk_count > 0 &&
                          'hover:border-primary/50 cursor-pointer transition-colors'
                      )}
                      onClick={() =>
                        doc.status === 'completed' && doc.chunk_count > 0 &&
                        (setSelectedDocument(doc), setViewMode('sections'))
                      }
                    >
                      <div className="flex items-center gap-4 px-4 py-3">
                        <div className="shrink-0 w-9 h-9 bg-primary/10 rounded-md flex items-center justify-center">
                          <span className="text-xs font-bold text-primary uppercase">
                            {getFileIcon(doc.mime_type).slice(0, 3)}
                          </span>
                        </div>

                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate">{doc.original_name}</p>
                          <p className="text-xs text-muted-foreground">
                            {doc.mime_type?.split('/').pop() || 'Unknown'} · {formatFileSize(doc.file_size)}
                          </p>
                        </div>

                        <Badge variant={docStatusVariant(doc.status)} className="shrink-0">
                          {IN_PROGRESS_STATUSES.has(doc.status) && (
                            <Loader2 className="size-3 animate-spin mr-1" />
                          )}
                          {doc.status}
                        </Badge>

                        <div className="shrink-0 text-right min-w-[80px]">
                          {doc.status === 'completed' && doc.chunk_count > 0 ? (
                            <span className="text-sm font-medium text-primary">
                              {doc.chunk_count} sections
                            </span>
                          ) : (
                            <span className="text-sm text-muted-foreground">—</span>
                          )}
                        </div>

                        <div className="flex items-center gap-1 shrink-0" onClick={(e) => e.stopPropagation()}>
                          <Button
                            variant="ghost" size="icon" className="size-8 text-muted-foreground hover:text-primary"
                            title="Preview"
                            onClick={(e) => { e.stopPropagation(); setSelectedDocument(doc); setViewMode('preview'); }}
                          >
                            <Eye className="size-4" />
                          </Button>
                          <Button
                            variant="ghost" size="icon" className="size-8 text-muted-foreground hover:text-primary"
                            title="Download"
                            onClick={(e) => handleDownload(doc, e)}
                          >
                            <Download className="size-4" />
                          </Button>
                          <Button
                            variant="ghost" size="icon" className="size-8 text-muted-foreground hover:text-destructive"
                            title="Delete" disabled={deleteMutation.isPending}
                            onClick={(e) => handleDelete(doc, e)}
                          >
                            <Trash2 className="size-4" />
                          </Button>
                        </div>
                      </div>

                      {doc.error_message && (
                        <div className="px-4 pb-3">
                          <p className="text-xs text-destructive truncate" title={doc.error_message}>
                            {doc.error_message}
                          </p>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-12 bg-card rounded-lg border border-border">
                  <h3 className="text-lg font-medium mb-1">No Documents Yet</h3>
                  <p className="text-muted-foreground">Upload your first document to get started</p>
                </div>
              )}

              {documentsData && documentsData.total > documentsData.page_size && (
                <div className="mt-4 flex justify-center">
                  <p className="text-sm text-muted-foreground">
                    Showing {documentsData.items.length} of {documentsData.total} documents
                  </p>
                </div>
              )}
            </div>
          </>
        )}

        {viewMode === 'sections' && selectedDocument && (
          <SectionsView
            document={selectedDocument}
            onBack={() => { setViewMode('documents'); setSelectedDocument(null); }}
          />
        )}

        {viewMode === 'preview' && selectedDocument && (
          <PreviewView
            document={selectedDocument}
            onBack={() => { setViewMode('documents'); setSelectedDocument(null); }}
          />
        )}
      </div>
    </div>
  );
}
