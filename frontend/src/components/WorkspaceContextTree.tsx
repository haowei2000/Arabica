import { useState, useMemo } from 'react';
import {
  Wrench, Library, Sparkles, Brain, Clock,
  FolderOpen, Folder, FileText, Code2, File,
  ChevronRight, ChevronDown, Loader2, RefreshCw,
  ChevronsDownUp, ChevronsUpDown, Layers,
} from 'lucide-react';
import { useWorkspaceContexts } from '@/hooks/useWorkspaces';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { cn } from '@/lib/utils';
import type { WorkspaceContext } from '@/types/workspace';

// ─── Tree data model ───────────────────────────────────────────────────────────

interface FileNode {
  kind: 'file';
  name: string;
  fullPath: string;
  item: WorkspaceContext;
}

interface FolderNode {
  kind: 'folder';
  name: string;
  fullPath: string;
  children: TreeNode[];
}

type TreeNode = FileNode | FolderNode;

// ─── Folder config ─────────────────────────────────────────────────────────────

const FOLDER_ORDER = ['tools', 'knowledge', 'skills', 'memory', 'history'];

const ROOT_FOLDER_META: Record<string, { label: string; color: string; icon: typeof Wrench }> = {
  tools:     { label: 'Tools',     color: 'text-blue-500',   icon: Wrench   },
  knowledge: { label: 'Knowledge', color: 'text-green-500',  icon: Library  },
  skills:    { label: 'Skills',    color: 'text-purple-500', icon: Sparkles },
  memory:    { label: 'Memory',    color: 'text-pink-500',   icon: Brain    },
  history:   { label: 'History',   color: 'text-orange-500', icon: Clock    },
};

// ─── Tree builder ──────────────────────────────────────────────────────────────

function buildTree(items: WorkspaceContext[]): TreeNode[] {
  const root = new Map<string, { folder: FolderNode; map: Map<string, unknown> }>();

  for (const item of items) {
    let rel = (item.path ?? item.name).replace(/^\/+/, '');

    const segments = rel.split('/').filter(Boolean);
    if (segments.length === 0) continue;

    // Build nested folders
    let currentMap = root as Map<string, any>;
    let currentChildren: TreeNode[] | null = null;

    for (let i = 0; i < segments.length - 1; i++) {
      const seg = segments[i];
      if (!currentMap.has(seg)) {
        const newFolder: FolderNode = {
          kind: 'folder',
          name: seg,
          fullPath: segments.slice(0, i + 1).join('/'),
          children: [],
        };
        currentMap.set(seg, { node: newFolder, map: new Map() });
        if (currentChildren) currentChildren.push(newFolder);
        else if (i === 0) {
          // top-level: handled below, just register
        }
      }
      const entry = currentMap.get(seg);
      currentChildren = entry.node.children;
      currentMap = entry.map;
    }

    // Leaf file node
    const leafSeg = segments[segments.length - 1];
    const fileNode: FileNode = {
      kind: 'file',
      name: item.name || leafSeg,
      fullPath: rel,
      item,
    };

    if (currentChildren) {
      currentChildren.push(fileNode);
    } else {
      // Direct child of a root folder
      if (!root.has(segments[0])) {
        const folderNode: FolderNode = {
          kind: 'folder',
          name: segments[0],
          fullPath: segments[0],
          children: [],
        };
        root.set(segments[0], { node: folderNode, map: new Map() });
      }
      const entry = root.get(segments[0])!;
      if (segments.length === 1) {
        // File directly at root level
        entry.node.children.push(fileNode);
      } else {
        entry.node.children.push(fileNode);
      }
    }
  }

  // Sort by FOLDER_ORDER, then alphabetically for the rest
  const nodes: FolderNode[] = [];
  const rest: FolderNode[] = [];

  for (const key of FOLDER_ORDER) {
    if (root.has(key)) nodes.push(root.get(key)!.node);
  }
  for (const [key, entry] of root) {
    if (!FOLDER_ORDER.includes(key)) rest.push(entry.node);
  }
  rest.sort((a, b) => a.name.localeCompare(b.name));

  return [...nodes, ...rest];
}

// ─── Item icon ─────────────────────────────────────────────────────────────────

function getItemIcon(item: WorkspaceContext, folderKey: string) {
  const ct = item.content_type ?? '';
  if (ct.includes('json') || ct.includes('javascript')) return <Code2 size={12} className="text-yellow-500 shrink-0" />;
  if (ct.includes('text')) return <FileText size={12} className="text-muted-foreground shrink-0" />;
  // Fallback by folder type
  const meta = ROOT_FOLDER_META[folderKey];
  if (meta) {
    const Icon = meta.icon;
    return <Icon size={12} className={cn(meta.color, 'shrink-0')} />;
  }
  return <File size={12} className="text-muted-foreground shrink-0" />;
}

// ─── File node ─────────────────────────────────────────────────────────────────

function FileRow({
  node,
  depth,
  folderKey,
  isLast,
}: {
  node: FileNode;
  depth: number;
  folderKey: string;
  isLast: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = !!(node.item.glance);

  return (
    <div>
      <button
        type="button"
        onClick={() => hasDetail && setExpanded(e => !e)}
        className={cn(
          'group flex items-start w-full py-[3px] pr-2 rounded text-left transition-colors',
          hasDetail ? 'hover:bg-muted/50 cursor-pointer' : 'cursor-default',
        )}
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
      >
        {/* Tree guide line dot */}
        <span className="flex items-center justify-center w-4 shrink-0 mt-[3px]">
          <span className="w-1 h-1 rounded-full bg-border group-hover:bg-muted-foreground/40 transition-colors" />
        </span>

        {getItemIcon(node.item, folderKey)}

        <span className="ml-1.5 min-w-0 flex-1">
          <span className="text-[11px] text-foreground/80 leading-snug truncate block">{node.name}</span>
          {expanded && node.item.glance && (
            <span className="text-[10px] text-muted-foreground leading-relaxed block mt-0.5 whitespace-pre-wrap">
              {node.item.glance}
            </span>
          )}
        </span>

        {hasDetail && (
          <span className="shrink-0 ml-1 mt-[3px] opacity-0 group-hover:opacity-100 transition-opacity">
            {expanded
              ? <ChevronDown size={9} className="text-muted-foreground" />
              : <ChevronRight size={9} className="text-muted-foreground" />}
          </span>
        )}
      </button>
    </div>
  );
}

// ─── Folder node ───────────────────────────────────────────────────────────────

function FolderRow({
  node,
  depth,
  forceOpen,
  folderKey,
}: {
  node: FolderNode;
  depth: number;
  forceOpen: boolean | null;
  folderKey: string;
}) {
  const isRoot = depth === 0;
  const meta = isRoot ? ROOT_FOLDER_META[node.name] : undefined;
  const [localOpen, setLocalOpen] = useState(true);

  // forceOpen=true → expand all, forceOpen=false → collapse all, null → use local state
  const open = forceOpen !== null ? forceOpen : localOpen;

  const count = countLeaves(node);

  const FolderIcon = open
    ? (meta ? meta.icon : FolderOpen)
    : (meta ? meta.icon : Folder);

  const iconColor = meta?.color ?? 'text-amber-500';
  const label = isRoot ? (meta?.label ?? capitalize(node.name)) : node.name;

  return (
    <div>
      <button
        type="button"
        onClick={() => setLocalOpen(o => !o)}
        className={cn(
          'flex items-center w-full py-[3px] pr-2 rounded text-left transition-colors hover:bg-muted/50',
          isRoot && 'py-1',
        )}
        style={{ paddingLeft: `${depth * 12 + 4}px` }}
      >
        <span className="flex items-center justify-center w-4 shrink-0">
          {open
            ? <ChevronDown size={10} className="text-muted-foreground" />
            : <ChevronRight size={10} className="text-muted-foreground" />}
        </span>
        <FolderIcon size={isRoot ? 13 : 12} className={cn(iconColor, 'shrink-0')} />
        <span className={cn(
          'ml-1.5 truncate',
          isRoot ? 'text-xs font-semibold text-foreground' : 'text-[11px] text-foreground/80',
        )}>
          {label}
        </span>
        <span className="ml-auto shrink-0 text-[10px] text-muted-foreground bg-muted/60 px-1.5 rounded-full leading-5">
          {count}
        </span>
      </button>

      {open && node.children.length > 0 && (
        <div className="relative">
          {/* Vertical guide line */}
          <span
            className="absolute top-0 bottom-1 border-l border-border/60"
            style={{ left: `${depth * 12 + 11}px` }}
          />
          <div className="space-y-px">
            {node.children.map((child, i) =>
              child.kind === 'folder' ? (
                <FolderRow
                  key={child.fullPath}
                  node={child}
                  depth={depth + 1}
                  forceOpen={forceOpen}
                  folderKey={folderKey}
                />
              ) : (
                <FileRow
                  key={child.item.id}
                  node={child}
                  depth={depth + 1}
                  folderKey={folderKey}
                  isLast={i === node.children.length - 1}
                />
              )
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function countLeaves(node: FolderNode): number {
  let n = 0;
  for (const child of node.children) {
    if (child.kind === 'file') n++;
    else n += countLeaves(child);
  }
  return n;
}

function capitalize(s: string) {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ─── Main export ───────────────────────────────────────────────────────────────

export default function WorkspaceContextTree({ workspaceId }: { workspaceId: string }) {
  const { data, isLoading, refetch, isFetching } = useWorkspaceContexts(workspaceId, {
    page: 1,
    page_size: 200,
  });

  // null = use per-node local state, true = all expanded, false = all collapsed
  const [forceOpen, setForceOpen] = useState<boolean | null>(null);

  const items = data?.items ?? [];
  const tree = useMemo(() => buildTree(items), [items]);

  const totalLeaves = useMemo(() => tree.reduce((s, n) => s + (n.kind === 'folder' ? countLeaves(n) : 1), 0), [tree]);

  return (
    <div className="flex flex-col h-full gap-2">

      {/* Header */}
      <div className="flex items-center gap-1 shrink-0">
        <div className="flex items-center gap-1 flex-1 min-w-0" title="Workspace Context">
          <Layers size={12} className="text-muted-foreground shrink-0" />
          {data && totalLeaves > 0 && (
            <span className="text-[10px] text-muted-foreground bg-muted/60 px-1.5 rounded-full leading-5 tabular-nums">
              {totalLeaves}
            </span>
          )}
        </div>
        <Button
          variant="ghost" size="icon" className="size-6"
          onClick={() => setForceOpen(v => v === true ? null : true)}
          title="Expand all"
          disabled={isLoading}
        >
          <ChevronsUpDown size={12} />
        </Button>
        <Button
          variant="ghost" size="icon" className="size-6"
          onClick={() => setForceOpen(v => v === false ? null : false)}
          title="Collapse all"
          disabled={isLoading}
        >
          <ChevronsDownUp size={12} />
        </Button>
        <Button
          variant="ghost" size="icon" className="size-6"
          onClick={() => refetch()}
          disabled={isFetching}
          title="Refresh context"
        >
          <RefreshCw size={12} className={cn(isFetching && 'animate-spin')} />
        </Button>
      </div>

      {/* Tree */}
      {isLoading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
      ) : tree.length === 0 ? (
        <div className="text-center py-10 text-muted-foreground">
          <Layers size={20} className="mx-auto mb-2 text-muted-foreground/20" />
          <p className="text-[10px] text-muted-foreground/50">No context loaded</p>
        </div>
      ) : (
        <ScrollArea className="flex-1 -mx-1">
          <div className="px-1 space-y-px pb-2">
            {tree.map(node =>
              node.kind === 'folder' ? (
                <FolderRow
                  key={node.fullPath}
                  node={node}
                  depth={0}
                  forceOpen={forceOpen}
                  folderKey={node.name}
                />
              ) : (
                <FileRow
                  key={(node as FileNode).item.id}
                  node={node as FileNode}
                  depth={0}
                  folderKey=""
                  isLast={false}
                />
              )
            )}
          </div>
        </ScrollArea>
      )}
    </div>
  );
}
